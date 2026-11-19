"""Make the BotW enemies mod from the player's own Breath of the Wild RomFS: enemies of that game as actors of this one.

    python tools/make_botw_enemies_mod.py --botw <BotW romfs folder> --game <game folder> [--out <mod folder>]
                                          [--only Hinox,...] [--work <folder>] [--check] [--preview <folder>]

The two games are of different engine generations, so an enemy is put together from four sources:

1. Behaviour from an enemy of this game whose body plan and moves are closest (ENEMIES[...]['base']): its actor pack
   (Pack/Actor/<base>.pack.zs, a zstd SARC of BYML components and AI graphs) is copied under the new actor's name with
   its AI, its animation control (AS/<base>.root.asb) and its sound links. Pack members are shared between packs by
   path, so every member the copy changes is renamed after the actor, and the references to it in the other members (the
   '?<path>' and 'Work/<path>.gyml' forms) follow. Changed: the ActorParam (read by the actor's name), the ModelInfo
   (the converted model), the AnimationParam (the converted animations), the hit and body shapes (Phive ShapeParam
   and the ragdoll's capsules, scaled by the creature's size against the base's), the movement speeds (from the
   creature's own walk and run), and the cloth the base's model carries (removed: it belongs to the base's mesh).
2. The model from the BotW model (Model/<name>.sbfres, BFRES 5.0.3, Yaz0) written into the base's model file (BFRES
   10.4) by tools/botw_bfres.py rebuild_model: the creature's own skeleton and geometry with the base's materials and
   shaders. The skeleton is the creature's bones as they are, then one more bone for each bone of the base that the
   creature has no bone of that name for (its physics, look-at, eye, helper and effect settings name base bones): such
   a bone hangs from the creature bone that matches its nearest base ancestor, placed where that ancestor's offset
   puts it once scaled to the creature's size, and moves with it. The creature's vertices keep their positions,
   normals and matrix indices (the matrix palette is the creature's) and are written in the vertex layout of the base
   shape whose material they are drawn with (ENEMIES[...]['slots']); base shapes nothing is drawn with get one empty
   triangle. Textures (tools/botw_textures.py) are the creature's albedo, normal map and emission, made into this
   game's files, and constants for the material's other slots (roughness, ambient occlusion and the like).
3. The animations from the BotW animation file (Model/<name>_Animation.sbfres): the base's animation control asks for
   the base's animation names, so each is given the creature animation closest to it (ENEMIES[...]['anims']), with
   the base's name, its loop setting and, for one that does not loop, its length (the creature's keys stretched in
   time), so the base's timing (attack frames, events) holds. The root bone's own motion is taken out: the game moves
   the actor. The base's animation events (AnimationEvent/Animation/<base>_Animation.anim.baev.zs) are copied for
   the new animation file.

4. The sounds from the BotW sound archive (Sound/Resource/<name>.bars, ENEMIES[...]['sounds']) by
   tools/botw_sound.py: the base's archive with its clips made from the creature's (resampled to 48 kHz), saved as the
   actor's own archive and given to the actor in the archive list (Sound/ResourceListPack/BarslistPack), which the
   game reads by actor name. The base's sound links still say when each clip plays, by the base's clip names.

The actor gets rows in RSDB/ActorInfo, RSDB/GameActorInfo and RSDB/Tag (the base's tags, so bombs and the like react
to it as to the base), and is placed in the areas' Dynamic placement files (Banc/<folder>/<area>_Dynamic.bcett.byml.zs)
beside enemies already there. Files the game sizes from the resource size table are raised by the runtime
(runtime/romfs.c restbl_overlay).

--check reads back what was written and prints a summary; --preview writes PNG drawings of each converted model in its
bind pose and in frames of its animations.

Run with Python 3.14 (compression.zstd). Standard library only besides the repository's tools. Nothing of either game
goes anywhere but the mod folder and the work folder.
"""
import argparse
import hashlib
import math
import re
import struct
import sys
import textwrap
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import botw_bfres as bb   # noqa: E402  BFRES 5 and 10: models, skeletons, animations, writing
import botw_sound as bs   # noqa: E402  BotW sound archives into this game's
import botw_textures as tx   # noqa: E402  BotW textures into this game's
import make_dark_link_mod as darklink   # noqa: E402  sarc_members, bfres_strings
import n64voice   # noqa: E402  RomFS: the unpacked RomFS with the zstd dictionaries
import randomizer   # noqa: E402  byml_read, byml_write

MOD_NAME = 'BotW enemies'
BASE_BONE_CONTROL = ('PathBoneModifier', 'PathFootIK')   # ControllerSetParam fields left out (enemy_pack)

# The base's members whose distances (fields named ...Distance... or Dist) distance_scale scales: how far its battle
# moves keep it from Link, where it starts an attack, and its own parameters of the kind.
DISTANCE_MEMBERS = ('Game/BSA/ActionSeqContainer/', 'Game/BSA/ActionVerbContainerSelector/',
                    'GameParameter/LizalfosParam/')
T_STR, T_FLOAT = randomizer.T_STR, randomizer.T_FLOAT

# The constant a texture slot gets when the creature has no texture for it, by the slot's suffix.
CONSTANTS = {'Rgh': 170, 'AO': 235, 'Thc': 0, 'Trs': 255, 'Mtl': 0, 'Emm': (0, 0, 0), 'Emc': (0, 0, 0), 'Spc': 0}

# What each enemy is made of.
#   base            the enemy of this game whose behaviour, materials and animation control are used
#   pack            the actor whose pack and table rows the actor starts from, when not the base: a variant of the
#                   base that has the base's model and animations (default: base)
#   botw_model      the BotW model (Model/<name>.sbfres; its textures in <name>.Tex.sbfres), the first model in it
#   botw_anims      the BotW animation file (Model/<name>.sbfres)
#   size_bone       the bone whose height gives the creature's size against the base's
#   size            that size given instead; physics_size: the size its collision shapes take (default: size)
#   bone_map        base bone -> creature bone, where the names differ
#   slots           BotW shape -> the base shape (by its material) it is drawn as; other BotW shapes are not drawn
#   textures        base material -> {texture suffix: ('albedo' | 'normal', BotW texture) or ('const', value)}
#   drop            components of the base's ActorParam the actor goes without (they need the base's own model)
#   blackboard      blackboard key -> the starting value the actor's blackboard tables give it instead of the base's
#   params          member folder -> {field: value}: fields of the base's members in that folder set to other values
#                   (a field inside another as 'Outer.Inner')
#   hit_shapes      base shape (its ShapeParam name after the base's) -> (bone, end A, end B, radius): that shape made
#                   a capsule between two points of the creature's rest pose (metres, model space), carried on that
#                   bone (the sensor unit of the shape follows it). For a creature whose body the base's shapes do not
#                   cover.
#   boxes           base shape -> (centre, half extents): a box shape the base's sensor carries in the actor's own
#                   frame (no bone), given the creature's measure (metres, model space)
#                   The base's sensors follow bones by name, and bone_map's names are given to them too.
#   atlas           base material -> {'size': (colour w, h, normal map w, h), 'cells': [(BotW shape or part model,
#                   (x, y, width, height) as parts of the texture, colour texture, normal map, BotW file or None)]}:
#                   several BotW textures in one texture of that material (textures: ('atlas', 'albedo' | 'normal'))
#   distance_scale  the base's distances to Link in its battle moves times this (DISTANCE_MEMBERS)
#   anims           [(pattern, BotW animation)]: a base animation takes the first whose pattern it matches
#   speeds          MovementParam field -> the BotW animation whose root motion gives that speed
#   sounds          {'bars': BotW archive, 'picks': {last part of a base clip's name: [BotW clips, in turn]}}
#   areas, count, beside    placements: (folder, area), how many in each, the enemies they stand beside
#   spawner         a spawner that makes the actor near Link in the areas it names, instead of placements (spawner())
ENEMIES = {
    'Hinox': {
        'actor': 'Enemy_BotW_Hinox',
        # the Lizalfos's behaviour: it fights in the open (the Moblin of this game keeps to the rails of its patrol
        # and does nothing off them)
        'base': 'Enemy_Lizalfos',
        'botw_model': 'Enemy_Giant',
        # the Lizalfos sees Link from 10 m below to 5 m above its own feet, and its senses are off inside an area
        # kept free of enemies
        'params': {'Component/PerceptionReceiverParam/': {
            'DisableIfInsideEnemyForbidArea': 0, 'NoReceiveEmitInEnemyForbidArea': 0,
            'SightParam.DiscoverHorizontalRange': 30.0, 'SightParam.DiscoverVerticalRangeMin': -8.0,
            'SightParam.DiscoverVerticalRangeMax': 14.0, 'SightParam.DiscoverVerticalCloseRangeMin': -8.0,
            'SightParam.DiscoverVerticalCloseRangeMax': 14.0}},
        'botw_anims': 'Enemy_Giant_Animation',
        'size_bone': 'Head',
        # the Lizalfos keeps 1.6 m from Link and starts its attacks within 4.5 m; a giant keeps its own reach
        'distance_scale': 4.0,
        'bone_map': {'Neck_2': 'Neck'},
        # the Lizalfos's attack shapes are its sword along the Item bone and a ball on its right elbow; on the Hinox
        # those are a hand's length past its fist and 6 m out from its body at shoulder height, so its blows never
        # reached Link: its fists instead (the arms are held out level at rest, 10 m up), which it swings and slams
        # down in its attacks
        'hit_shapes': {'Atk_0': ('Wrist_R', (-9.2, 10.35, -0.5), (-10.9, 10.05, -0.3), 1.4),
                       'Atk_1': ('Wrist_L', (9.2, 10.35, -0.5), (10.9, 10.05, -0.3), 1.4)},
        # its own voice, steps and blows, in the Lizalfos's clip slots (see the Lynel's)
        'sounds': {'bars': 'Giant', 'picks': {
            'Run': ['Giant_Run_L00', 'Giant_Run_R00', 'Giant_Run_L01'],
            'FootStep': ['Giant_Walk_L00', 'Giant_Walk_R00', 'Giant_Walk_L01'],
            'ProvokeVoice': ['GiantVo_Angry0', 'GiantVo_Angry1', 'GiantVo_Notice'],
            'PreAttackVoice': ['GiantVo_PreShort0', 'GiantVo_PreShort1', 'GiantVo_PreShort2'],
            'AttackVoice': ['GiantVo_Attack0', 'GiantVo_Attack1', 'GiantVo_Attack2'],
            'JumpVoice': ['GiantVo_AttackSwing0', 'GiantVo_AttackSwing1', 'GiantVo_HipDrop'],
            'DamageVoice': ['GiantVo_SmallDamage0', 'GiantVo_SmallDamage1'],
            'BigDamageVoice': ['GiantVo_Damage0', 'GiantVo_Damage1', 'GiantVo_DamageEye0'],
            'RepelledVoice': ['GiantVo_Guard0', 'GiantVo_Guard1'],
            'RepelledLongVoice': ['GiantVo_DamageEye1', 'GiantVo_Cry0'],
            'DeadVoice': ['GiantVo_Dead0', 'GiantVo_Cry1', 'GiantVo_Lie'],
            'AppearVoice': ['GiantVo_Search0', 'GiantVo_Search4', 'GiantVo_Notice'],
            'UproarVoice': ['GiantVo_Cry0', 'GiantVo_Angry1'], 'Uproar2Voice': ['GiantVo_Angry0', 'GiantVo_Cry1'],
            'FaintEndVoice': ['GiantVo_PullOut', 'GiantVo_Sleep1'],
            'ChainNoise': ['Giant_Angry_L00', 'Giant_Angry_R00', 'Giant_Angry_L01'],
            'Attack': ['Giant_AttackHandClap'], 'AttackJump': ['Giant_DownHip'], 'StepJump': ['Giant_Run_R01'],
            'Land': ['Giant_Down00', 'Giant_Down01'], 'RagdollLand': ['Giant_Down00', 'Giant_Down01']}},
        'slots': {
            'Giant__Mt_Skin': 'Body_Model_low__Mt_Body', 'Giant__Mt_Mucosa': 'Body_Model_low__Mt_Body',
            'Beard__Mt_Hair': 'Body_Model_low__Mt_Body',
            'Accessories__Mt_Cloth': 'Aplon_Model_0_low__Mt_Armor', 'Vest__Mt_Cloth': 'Aplon_Model_0_low__Mt_Armor',
            'Giant__Mt_Cloth': 'Aplon_Model_0_low__Mt_Armor',
            'EyeballModel__Mt_Eye': 'Eye_Model_low__Mt_Eye',
        },
        'textures': {
            'Mt_Body': {'Alb': ('albedo', 'Enemy_Giant_Red_Body_Alb'), 'Nrm': ('normal', 'Enemy_Giant_Blue_Body_Nrm'),
                        'Rgh': ('const', 165), 'AO': ('const', 230)},
            'Mt_Armor': {'Alb': ('albedo', 'Enemy_Giant_Red_Cloth_Alb'),
                         'Nrm': ('normal', 'Enemy_Giant_Blue_Cloth_Nrm'), 'Rgh': ('const', 210),
                         'AO': ('const', 220)},
            'Mt_Eye': {'Alb': ('albedo', 'Enemy_Giant_Red_Eye_Alb'), 'Nrm': ('normal', 'Enemy_Giant_Blue_Eye_Nrm'),
                       'Rgh': ('const', 76)},
        },
        'anims': [
            (r'^JumpWeaponAttack', 'Attack_Hand_Double_1'), (r'^WeaponAttack', 'Attack_Hand_R'),
            (r'^PreAttackJump', 'Attack_Foot_1'), (r'^AttackJump', 'Attack_Hipdrop'),
            (r'^Appear', 'Find'), (r'^BattleMode', 'Find'), (r'^Provoke', 'Angry'), (r'^Wait_Uproar', 'Angry'),
            (r'Damage', 'Damage_Body'), (r'^Repelled', 'Damage_Foot_L'), (r'^Die_', 'Dead_Stand'),
            (r'^QuickWaitTurn_Reverse', 'Turn_R_Battle'), (r'^QuickWaitTurn', 'Turn_L_Battle'),
            (r'^SideStep|^SideWalk_L|_L(30|45|90|135)', 'Walk_Curve_L'),
            (r'^SideWalk_R|_R(30|45|90|135)', 'Walk_Curve_R'), (r'^SideWalk_Brake', 'Wait_Battle'),
            (r'^Stun_St|^Stun_Air', 'Damage_Eye_Start'), (r'^Stun_Ed|^Stun_Land', 'Damage_Eye_End'),
            (r'^Stun_', 'Damage_Eye_Loop'),
            (r'^Run|^Return_Jump', 'Run'), (r'^Walk', 'Walk'), (r'^Wait$', 'Wait'), (r'', 'Wait_Battle'),
        ],
        'own_length': r'^Die_',
        'speeds': {'WalkSpeed': 'Walk', 'JogSpeed': 'Run', 'RunSpeed': 'Run', 'DashSpeed': 'Run'},
        'areas': [('HyruleField', 'HyruleField_Adult'), ('HyruleField', 'HyruleField_Child')],
        'count': 2,
        'beside': ('Enemy_Tektite_Blue', 'Enemy_Tektite_Red', 'Enemy_Octorok', 'Enemy_Peahat'),
        # as a child, the places beside a Tektite are in the lake (a Hinox sinks to its bottom): the grass by the
        # Peahat larvae east of the field's middle instead
        'at': {'HyruleField_Child': [(132.0, 154.6, 136.0), (-107.2, 147.8, 58.6)]},
        'about': 'the Hinox, about 12 m tall, with its own skeleton and animations, on the Lizalfos\'s behaviour: it '
                 'notices Link, closes in, sidesteps and strikes with its hands; bombs, arrows and the sword hurt it '
                 'as they hurt the Lizalfos. In Hyrule Field, child and adult.',
    },
    'Lynel': {
        'actor': 'Enemy_BotW_Lynel',
        # the Lizalfos's behaviour, a sword fighter that fights in the open: the Stalfos of this game lives in
        # dungeon rooms and takes itself away in the field as soon as it sees Link, and the Moblin keeps to the
        # rails of its patrol
        'base': 'Enemy_Lizalfos',
        'botw_model': 'Enemy_Lynel',
        'botw_name': 'Lynel',
        'botw_anims': 'Enemy_Lynel_Animation',
        'size_bone': 'Head',
        # the Lizalfos keeps 1.6 m from Link and starts its attacks within 4.5 m: the Lynel's body is 3 m long
        'distance_scale': 2.2,
        # the Lynel's human arms and chest carry the Man_ names (its Arm_ bones are the horse's forelegs)
        'bone_map': {'Clavicle_L': 'Man_Clavicle_L', 'Arm_1_L': 'Man_Arm_1_L', 'Arm_2_L': 'Man_Arm_2_L',
                     'Wrist_L': 'Man_Wrist_L', 'Clavicle_R': 'Man_Clavicle_R', 'Arm_1_R': 'Man_Arm_1_R',
                     'Arm_2_R': 'Man_Arm_2_R', 'Wrist_R': 'Man_Wrist_R', 'Spine_2': 'Man_Spine_2', 'Neck_2': 'Neck'},
        # the Lizalfos's body shape is its upright torso on Spine_2, which is the man's chest here (3 m up, out of a
        # sword's reach): the horse's barrel (z -1.5 to 1.5, 1.35 to 2.75 m up, 0.75 m to each side) gets one that
        # also reaches down to the height of Link's sword. It hangs from the root, whose frame is the actor's own.
        'hit_shapes': {'Tgt_0': ('Root', (0.0, 1.85, -1.3), (0.0, 1.85, 1.3), 1.0),
                       # the Lizalfos's attack shapes, its sword along the Item bone and its right elbow, become the
                       # Lynel's sword (the blade reaches 1.2 m along the hand's weapon bone, forward at rest) and the
                       # reach of its blows before its forelegs at Link's height, on the root (the actor's frame): its
                       # sword is held 3.7 m up and the Lizalfos's swings, timed for a Lizalfos, pass over Link
                       'Atk_0': ('Weapon_R', (-2.75, 3.72, 1.2), (-2.75, 3.72, 2.4), 0.4),
                       'Atk_1': ('Root', (0.0, 0.9, 1.2), (0.0, 0.9, 3.4), 0.9)},
        'params': {'Component/PerceptionReceiverParam/': {
            'DisableIfInsideEnemyForbidArea': 0, 'NoReceiveEmitInEnemyForbidArea': 0,
            'SightParam.DiscoverHorizontalRange': 25.0, 'SightParam.DiscoverVerticalRangeMin': -8.0,
            'SightParam.DiscoverVerticalRangeMax': 8.0, 'SightParam.DiscoverVerticalCloseRangeMin': -8.0,
            'SightParam.DiscoverVerticalCloseRangeMax': 8.0}},
        # its own voice and blows, in the Lizalfos's clip slots (the last part of each clip's name, its number
        # dropped): the Lizalfos's sound links say when each plays. Its footsteps stay the Lizalfos's.
        'sounds': {'bars': 'Lynel', 'picks': {
            'Run': ['Lynel_Vo_Movement_00', 'Lynel_Vo_Movement_01', 'Lynel_Vo_Movement_02'],
            'ProvokeVoice': ['Lynel_Vo_Roar_Wait_Blow_00', 'Lynel_Vo_Roar_Wait_Blow_01', 'Lynel_Vo_Roar_Wait_Blow_02'],
            'PreAttackVoice': ['Lynel_Vo_Attack_Pre_00', 'Lynel_Vo_Attack_Pre_01', 'Lynel_Vo_Attack_Pre_Long_00'],
            'AttackVoice': ['Lynel_Vo_Attack_00', 'Lynel_Vo_Attack_01', 'Lynel_Vo_Attack_02'],
            'JumpVoice': ['Lynel_Vo_MoveAttackGear1', 'Lynel_Vo_MoveAttackGear2', 'Lynel_Vo_MoveAttackGear3'],
            'DamageVoice': ['Lynel_Vo_SmallDamage_00', 'Lynel_Vo_SmallDamage_01'],
            'BigDamageVoice': ['Lynel_Vo_Damage_00', 'Lynel_Vo_Damage_01', 'Lynel_Vo_Damage_02'],
            'RepelledVoice': ['Lynel_Vo_ShortScream_00', 'Lynel_Vo_ShortScream_01'],
            'RepelledLongVoice': ['Lynel_Vo_Rodeo_00', 'Lynel_Vo_Rodeo_01'],
            'DeadVoice': ['Lynel_Vo_Die', 'Lynel_Vo_Down_00', 'Lynel_Vo_Down_01'],
            'AppearVoice': ['Lynel_Vo_Normal_00', 'Lynel_Vo_Normal_01', 'Lynel_Vo_Normal_02'],
            'UproarVoice': ['Lynel_Vo_Howl0', 'Lynel_Vo_Howl1'],
            'Uproar2Voice': ['Lynel_Vo_Roar_Wait_Blow_07', 'Lynel_Vo_Damage_03'],
            'FaintEndVoice': ['Lynel_Vo_Question00', 'Lynel_Vo_Brake'],
            'ChainNoise': ['Lynel_Shake_00', 'Lynel_Shake_01', 'Lynel_ArmRotate_00'],
            'Attack': ['Lynel_AttackSpear', 'Lynel_AttackSpear2'],
            'AttackJump': ['Lynel_JumpAttack'], 'StepJump': ['Lynel_BackStep'],
            'Land': ['Lynel_Land'], 'RagdollLand': ['Lynel_Land']}},
        'slots': {
            'Lynel_Body__Mt_Body': 'Body_Model_low__Mt_Body', 'Lynel_Eye_L__Mt_Eye': 'Eye_Model_low__Mt_Eye',
            'Lynel_Body__Mt_Hair': 'Aplon_Model_0_low__Mt_Armor',
            'Lynel_Armor_Arm__Mt_Armor': 'Aplon_Model_0_low__Mt_Armor',
        },
        # its sword and shield, separate models in BotW, held by its hands' weapon bones
        'parts': [
            {'file': 'Weapon_Sword_041', 'model': 'Weapon_Sword_041', 'attach': 'Weapon_R', 'scale': 1.6,
             'slots': {'*': 'Aplon_Model_0_low__Mt_Armor'}},
            {'file': 'Weapon_Shield_041', 'model': 'Weapon_Shield_041', 'attach': 'Weapon_L', 'scale': 1.6,
             'slots': {'*': 'Aplon_Model_0_low__Mt_Armor'}},
        ],
        # the Lizalfos has three materials: the mane, the armour, the sword and the shield share its armour's, each
        # in a cell of one texture: (shape or part model, (x, y, width, height) of the texture, its colour and
        # normal textures, the BotW file they are in when not the creature's)
        'atlas': {
            'Mt_Armor': {
                'size': (1024, 1024, 512, 512),   # colour, then normal map
                'cells': [
                    ('Weapon_Sword_041', (0.0, 0.0, 0.5, 0.5), 'Weapon_Sword_041_Alb', 'Weapon_Sword_041_Nrm',
                     'Weapon_Sword_041'),
                    ('Lynel_Armor_Arm__Mt_Armor', (0.5, 0.0, 0.25, 0.25), 'Lynel_Junior_Armor_Alb',
                     'Lynel_Senior_Armor_Nrm', None),
                    ('Weapon_Shield_041', (0.75, 0.0, 0.25, 0.25), 'Weapon_Shield_041_Alb', 'Weapon_Shield_041_Nrm',
                     'Weapon_Shield_041'),
                    ('Lynel_Body__Mt_Hair', (0.0, 0.5, 0.75, 0.3125), 'Lynel_Junior_Hair_Alb',
                     'Lynel_Senior_Hair_Nrm', None),
                ],
            },
        },
        'textures': {
            'Mt_Body': {'Alb': ('albedo', 'Lynel_Junior_Body_Alb'), 'Nrm': ('normal', 'Lynel_Senior_Body_Nrm'),
                        'Rgh': ('const', 175), 'AO': ('const', 235)},
            'Mt_Eye': {'Alb': ('albedo', 'Lynel_Junior_Eye_Alb'), 'Nrm': ('normal', 'Lynel_Senior_Eye_Nrm'),
                       'Rgh': ('const', 60)},
            'Mt_Armor': {'Alb': ('atlas', 'albedo'), 'Nrm': ('atlas', 'normal'), 'Rgh': ('const', 130),
                         'Mtl': ('const', 90), 'AO': ('const', 235)},
        },
        'anims': [
            (r'^JumpWeaponAttack', 'Attack_Jump_Sword_End'), (r'^WeaponAttack', 'Attack_Near_Sword_1'),
            (r'^PreAttackJump', 'Attack_Jump_Sword_Start'), (r'^AttackJump', 'Attack_Jump_Sword_Loop'),
            (r'^Appear', 'Appear_Sword'), (r'^BattleMode', 'Wait_To_Wait_Battle_Sword'), (r'^Provoke', 'Howling'),
            (r'^Wait_Uproar', 'Search_Sword'),
            (r'Damage', 'Damage'), (r'^Repelled', 'Rebound_Sword'), (r'^Die_', 'Dead_Sword'),
            (r'^QuickWaitTurn_Reverse', 'Turn_R_Sword'), (r'^QuickWaitTurn', 'Turn_L_Sword'),
            (r'^SideStep|^SideWalk_L|_L(30|45|90|135)', 'Body_Move_Gear_1_Slide_L_Sword'),
            (r'^SideWalk_R|_R(30|45|90|135)', 'Body_Move_Gear_1_Slide_R_Sword'),
            (r'^SideWalk_Brake', 'Brake_Sword'),
            (r'^Stun_St|^Stun_Air', 'Stun_Sword_Start'), (r'^Stun_Ed|^Stun_Land', 'Stun_Sword_End'),
            (r'^Stun_', 'Stun_Sword_Loop'),
            (r'^Return_Jump_St', 'Back_Step_Start'), (r'^Return_Jump_Lp', 'Back_Step_Loop'),
            (r'^Return_Jump', 'Back_Step_Sword_End'),
            (r'^RunJump_St', 'Jump_Sword_Start'), (r'^RunJump_Lp', 'Jump_Sword_Loop'),
            (r'^RunJump', 'Jump_Sword_End'),
            (r'^Run', 'Body_Move_Gear_3_Sword'), (r'^Walk', 'Body_Move_Gear_1_Sword'),
            (r'', 'Wait_Battle_Sword'),
        ],
        'own_length': r'^Die_|^Provoke',
        'speeds': {'WalkSpeed': 'Body_Move_Gear_1_Sword', 'JogSpeed': 'Body_Move_Gear_2_Sword',
                   'RunSpeed': 'Body_Move_Gear_3_Sword', 'DashSpeed': 'Body_Move_Gear_3_Sword'},
        'areas': [('HyruleField', 'HyruleField_Adult'), ('HyruleField', 'HyruleField_Child')],
        'count': 1,
        'beside': ('Enemy_Peahat', 'Enemy_Octorok', 'Enemy_Tektite_Red', 'Enemy_Tektite_Blue'),
        'offset': -8.0,
        # beside its adult neighbour it lands in a stream bed; the grass 18 m east of there instead. As a child, a
        # place beside a Tektite is in the lake: the grass by the Peahat in the south of the field
        'at': {'HyruleField_Adult': [(-93.0, 146.4, 24.0)], 'HyruleField_Child': [(-4.1, 146.7, 358.5)]},
        'about': 'the red-maned Lynel, about 5 m tall, with its own skeleton and animations and its Lynel sword '
                 'and shield, on the Lizalfos\'s behaviour: it notices Link, closes in, sidesteps, leaps and swings '
                 'its sword. In Hyrule Field, child and adult.',
    },
    'Talus': {
        'actor': 'Enemy_BotW_Talus',
        'base': 'Enemy_Amos',
        'botw_model': 'Enemy_Golem',
        'botw_name': 'Golem',
        'botw_anims': 'Enemy_Golem_Animation',
        'size': 4.6,   # 10.5 m against the Armos's 2.3 m
        'bone_map': {},
        # drawn twice: the Armos shows one or the other of these as it sleeps and wakes
        # its own rumble, steps and breaking apart, in the Armos's clip slots (the last part of each clip's name, its
        # number dropped; the Armos's landing on its hops and as it falls apart share 'Land')
        'sounds': {'bars': 'Golem', 'picks': {
            'Disguise': ['Golem_Lie01'], 'GetUp': ['Golem_Unite01'], 'GetUpVoice': ['Golem_Rub00', 'Golem_Quake01'],
            'Drag': ['Golem_RubShort01', 'Golem_RubShort02', 'Golem_RubShort03'],
            'Land': ['Golem_FootstepNormal01', 'Golem_FootstepNormal02', 'Golem_BodyPress01'],
            'Weak': ['Golem_RubShort04', 'Golem_FootstepNormal02', 'Golem_RubShort02'],
            'Explosion': ['Golem_Separate01'], 'DieVoice': ['Golem_ArmBreak01'], 'Crack': ['Golem_Damaged01'],
            'StunEnd': ['Golem_RubHigh01', 'Golem_RubHigh02']}},
        'slots': {'*': ('Active_Body_low__Mt_Body_Active', 'Disguised_Base_Model__Mt_Body_Disguised')},
        'parts': [
            {'file': 'Enemy_Golem', 'model': f'Golem_{bone}_Rock', 'attach': bone,
             'slots': {'*': ('Active_Body_low__Mt_Body_Active', 'Disguised_Base_Model__Mt_Body_Disguised')}}
            for bone in ('Arm_1_L', 'Arm_1_R', 'Arm_2_L', 'Arm_2_R')
        ],
        # the stone Talus takes its look from the ground's material textures, which are not the enemy's; the
        # Frost Talus's rock texture stands in for them
        'textures': {
            plan: {'Alb': ('albedo', 'Golem_FldObj_PushRock_A_Alb', 'Enemy_Golem_Ice'),
                   'Nrm': ('normal', 'Golem_FldObj_PushRock_A_Nrm', 'Enemy_Golem_Ice'),
                   'Rgh': ('const', 220), 'AO': ('const', 230)}
            for plan in ('Mt_Body_Active', 'Mt_Body_Disguised')
        },
        'anims': [
            (r'^Disguise', 'Get_Up@0'), (r'^GetUp', 'Get_Up'),
            (r'^BasicAttack_St', 'Attack_Rpunch'), (r'^BasicAttack_Lp', 'Attack_Run'),
            (r'^BasicAttack_Ed', 'Wait_Battle'), (r'^Die_St', 'Dead'), (r'^Die_Ed', 'Dead@99'),
            (r'^SmallDamage', 'Damage'), (r'^Stun_St', 'Damge_Weak'), (r'^Stun_Ed', 'Wait_Battle'),
            (r'^Turn', 'Turn_L'), (r'', 'Wait_Battle'),
        ],
        'own_length': r'^GetUp|^Die_St|^BasicAttack_St|^Stun_St',
        'areas': [('HyruleField', 'HyruleField_Adult'), ('HyruleField', 'HyruleField_Child')],
        'count': 1,
        'beside': ('Enemy_Octorok', 'Enemy_Peahat', 'Enemy_Tektite_Red', 'Enemy_Tektite_Blue'),
        'offset': 14.0,
        # the places tested on dry ground (the lake in the child's field has Tektites and Octoroks beside it)
        'at': {'HyruleField_Adult': [(11.0, 148.6, 293.6)], 'HyruleField_Child': [(-231.4, 137.8, 307.8)]},
        # the Armos shatters into pieces of a fracture model made for its own model; the game fails to make the
        # actor when that model is another one
        'drop': ('DestructionRef',),
        'about': 'the stone Talus, about 10 m tall, with its own skeleton and animations, on the Armos\'s '
                 'behaviour: it sits as a pile of rocks until Link comes near, rises, and comes after him with its '
                 'punch. In Hyrule Field, child and adult. Its rock texture is the Frost Talus\'s (the stone one '
                 'takes its look from the ground\'s textures).',
    },
    'Molduga': {
        'actor': 'Enemy_BotW_Molduga',
        'base': 'Enemy_Leever',
        'botw_model': 'Enemy_Sandworm',
        'botw_name': 'Sandworm',
        'botw_anims': 'Enemy_Sandworm_Animation',
        'size': 9.0,   # its body about 13 m across against the Leever's 1.4 m
        'sink': 3.5,   # lowered into the sand, its back and fins out, as it swims in BotW
        # its collision at the full size is a shell of 13 m buried in the ground, which holds it fast: the Leever's
        # burrowing and surfacing need a body that fits under the sand
        'physics_size': 3.0,
        # its own voice and the sand it throws up, in the Leever's clip slots (see the Talus's; the Leever's sand
        # sounds as it sinks and as it moves share 'Sand', and its turning loops loop)
        'sounds': {'bars': 'Enemy_Sandworm', 'picks': {
            'Appear': ['Sandworm_Appear', 'Sandworm_VoLiseUp1'], 'Disappear': ['Sandworm_Disappear'],
            'Sand': ['Sandworm_SeSandSplash', 'Sandworm_Move'],
            'AttackStart': ['Sandworm_VoSwimAngry1', 'Sandworm_VoSwimAngry2'],
            'AttackRotateLoop': ['Sandworm_VoSwimAngry3'], 'RotateLoop': ['Sandworm_Move'],
            'DamageVoice': ['Sandworm_VoDamageShort1', 'Sandworm_VoDamageShort2'], 'DeadVoice': ['Sandworm_VoDie1'],
            'Repel': ['Sandworm_VoUmekiShort1', 'Sandworm_VoUmekiShort2'], 'StunEnd': ['Sandworm_VoGroggyEnd']}},
        'bone_map': {},
        'slots': {'Body__Mt_Body': 'BodySpikes__Mt_Body', 'Eye_1__Mt_Eye': 'Eyes__Mt_Head'},
        'textures': {
            'Mt_Body': {'Alb': ('albedo', 'Sandworm_Body_Alb'), 'Nrm': ('normal', 'Sandworm_Body_Nrm'),
                        'Rgh': ('const', 200), 'AO': ('const', 235)},
            'Mt_Head': {'Alb': ('albedo', 'Sandworm_Eye_Alb'), 'Nrm': ('normal', 'Sandworm_Eye_Nrm'),
                        'Rgh': ('const', 60), 'AO': ('const', 255)},
        },
        'anims': [
            (r'^Appear', 'Swim_Angry_To_Swim_Angry_Wait'), (r'^Run', 'Swim_Fast'),
            (r'^Attack_St', 'Attack_L'), (r'^Attack_Ed', 'Attack_R'), (r'^Attack', 'Swim_Angry'),
            (r'^Disappear', 'Dive'), (r'^Damage', 'Damage'), (r'^Die', 'Dead'), (r'^Repel', 'Damage_Groggy_Small'),
            (r'^Stun_St', 'Groggy_Start'), (r'^Stun.*Lp', 'Groggy_Loop'), (r'^Stun_Ed', 'Groggy_End'),
            (r'', 'Swim_Normal'),
        ],
        'own_length': r'^Die',
        'speeds': {'WalkSpeed': 'Swim_Normal', 'JogSpeed': 'Swim_Fast', 'RunSpeed': 'Swim_Fast',
                   'DashSpeed': 'Swim_Fast'},
        # made by a spawner of its own, as the desert's Leevers are: the Leever's AI keeps a Leever the area's spawner
        # did not make under the sand, and one marked as placed (the blackboard's IsFixedPlacement, as the game's own
        # placed Leever has) takes itself away as soon as it is made outside a placement
        'areas': [],
        'spawner': {'like': 'Enemy_Leever_HountedWasteland',
                    'areas': ['HountedWasteland_Adult', 'DesertColossus_Adult', 'DesertColossus_Child'],
                    # one at a time, 24 to 30 m from Link, a new one 10 to 20 seconds after the last is gone; never
                    # the Big Leever the Leever spawner makes after twenty Leevers are beaten
                    'set': {'CreateActorCountMaxAtOnce': 1, 'CreateActorCountMinAtOnce': 1,
                            'MinExistActorCountToCreate': 1, 'StartIntervalMinExistActorCount': 1,
                            'CreateDistance': 24.0, 'CreateDistanceRandom': 6.0, 'IntervalToCreateFrame': 600.0,
                            'IntervalRandomToCreateFrame': 600.0, 'StrongActorCreatingDefeatCount': 1000000}},
        # The Leever's distances are a 1.4 m creature's: it sinks again once Link is 19 m away, and starts its attack
        # 5 m from him; the Molduga's are a creature 34 m long's
        'params': {'GameParameter/LeeverParam/': {'PreparePopupTimeRand': 0.0, 'DisappearDistance': 70.0,
                                                  'DisappearDistanceInSquadMode': 70.0, 'PlayAttackASDistance': 12.0}},
        'about': 'the Molduga, about 34 m long, with its own skeleton and animations, on the Leever\'s '
                 'behaviour: it rises from the sand near Link, chases him and sinks again. In the Haunted '
                 'Wasteland (adult) and at the Desert Colossus (child and adult).',
    },
}

README = """BotW enemies: enemies of Breath of the Wild in this game, made from your own copy of that game.

{enemies}
Each enemy has its own skeleton, model, animations and sounds (its voice, steps and blows from Breath of the Wild),
and fights with the behaviour of an enemy of this game (named above): when it attacks and how it reacts are that
enemy's, and its sounds play where that enemy's would.

Keep the N64 Graphics mod's "N64 actors" option off for these: that mod swaps enemy models by the enemy they are made
from and would draw the N64 model in their place.

Made by tools/make_botw_enemies_mod.py. Turn it off on the Mods tab to remove them.
"""


def f32(v):
    return struct.unpack('<f', struct.pack('<I', v[1]))[0] if v[0] == T_FLOAT else float(v[1])


def fl(x):
    return (T_FLOAT, struct.unpack('<I', struct.pack('<f', x))[0])


def botw_file(botw, rel):
    p = Path(botw) / rel
    if not p.is_file():
        raise SystemExit(f'the BotW RomFS has no {rel}')
    return p.read_bytes()


# ---- the skeleton

def transpose(r):
    return [[r[c][k] for c in range(3)] for k in range(3)]


def mul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def build_skeleton(base, src, spec):
    """The creature's skeleton with a bone for each base bone it has none of that name for. Returns Rec(skel_flags,
    bones, smooth, rigid, size: the creature's size over the base's, by_name, extra: the added bones' names)."""
    if spec.get('size'):
        size = spec['size']
    elif spec.get('size_bone'):
        b = spec['size_bone']
        size = src.bones[src.by_name[b]].world[1][3] / base.bones[base.by_name[b]].world[1][3]
    else:   # the highest bone of each
        size = max(b.world[1][3] for b in src.bones) / max(b.world[1][3] for b in base.bones)
    euler = bool(src.skel_flags & 0x1000)
    bones = []
    for bo in src.bones:
        r = bb.Rec()
        r.name, r.parent, r.flags = bo.name, bo.parent, bo.flags
        r.scale, r.rot, r.trans, r.world = list(bo.scale), list(bo.rot), list(bo.trans), bo.world
        bones.append(r)
    by_name = {b.name: i for i, b in enumerate(bones)}
    match = {}   # base bone index -> creature bone index of the same name (or bone_map's)
    for j, bo in enumerate(base.bones):
        name = spec['bone_map'].get(bo.name, bo.name)
        if name in by_name:
            match[j] = by_name[name]
    if 0 not in match:
        raise ValueError(f'{spec["botw_model"]}: no bone for the base root {base.bones[0].name}')
    s = [[size if r == c else 0.0 for c in range(4)] for r in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
    index = dict(match)   # base bone index -> bone index in the new skeleton
    extra = []
    for j, bo in enumerate(base.bones):
        if j in index:
            continue
        a = bo.parent
        while a not in match:
            a = base.bones[a].parent
        wc, wb = bones[match[a]].world, base.bones[a].world
        move = bb.mat_mul(bb.mat_mul(wc, s), bb.mat_inv(wb))
        pos = bb.xform(move, [bo.world[k][3] for k in range(3)])
        _, rc, _ = bb.decompose(wc)
        _, rb_a, _ = bb.decompose(wb)
        _, rb, _ = bb.decompose(bo.world)
        rot = mul3(mul3(rc, transpose(rb_a)), rb)
        world = [rot[k] + [pos[k]] for k in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
        parent = index[bo.parent]
        local = bb.mat_mul(bb.mat_inv(bones[parent].world), world)
        sc, rl, tr = bb.decompose(local)
        r = bb.Rec()
        r.name, r.parent = bo.name, parent
        r.scale, r.trans = sc, tr
        r.rot = bb.euler_of(rl) + [1.0] if euler else bb.quat_of(rl)
        r.world = bb.mat_mul(bones[parent].world, bb.local_matrix(r.scale, r.rot, r.trans, euler))
        r.flags = (0x1001 if euler else 1) | bb.bone_hints(r.scale, r.rot, r.trans, euler) | (bo.flags & 0x00800000)
        index[j] = len(bones)
        bones.append(r)
        extra.append(bo.name)
    sk = bb.Rec()
    sk.skel_flags, sk.bones, sk.smooth, sk.rigid = src.skel_flags, bones, list(src.smooth), list(src.rigid)
    sk.size, sk.extra = size, extra
    sk.by_name = {b.name: i for i, b in enumerate(bones)}
    return sk


# ---- the geometry

def quantize_weights(inf, k):
    """The k strongest influences [(matrix, weight)] with weights in 255ths that add up to 255."""
    inf = sorted(inf, key=lambda x: -x[1])[:k]
    total = sum(w for _, w in inf) or 1.0
    q = [(m, int(round(w / total * 255))) for m, w in inf]
    q = [(m, w) for m, w in q if w] or [(inf[0][0], 255)]
    q[0] = (q[0][0], q[0][1] + 255 - sum(w for _, w in q))
    return q


def median_defaults(f, vtx):
    """The per-component median of the vertex colours of a base vertex buffer set (a BotW vertex has none)."""
    out = {}
    vs = f.vertices(vtx)
    for name, vals in vs.items():
        if name.startswith('_c'):
            out[name] = [sorted(x[c] for x in vals)[len(vals) // 2] for c in range(len(vals[0]))]
    return out


def source_vertices(f, m, s, attach, skel, scale=1.0):
    """The vertices of a BotW shape as dicts, each influence (kind, bone in the new skeleton, weight): kind 's' (the
    position in model space) or 'r' (the position in that bone's space). attach: the creature bone a part of another
    model hangs from (its vertices are in that bone's space)."""
    v = m.vtx[s.vtx]
    vs = f.vertices(v)
    palette = m.smooth + m.rigid
    ns = len(m.smooth)
    n_inf = max(v.skin, 1)
    out = []
    for i in range(v.count):
        n, t = vs['_n0'][i][:3], (vs['_t0'][i][:3] if '_t0' in vs else [1.0, 0.0, 0.0])
        hand = 1.0
        if '_b0' in vs:
            hand = 1.0 if sum(a * b for a, b in zip(bb.cross(n, t), vs['_b0'][i][:3])) >= 0 else -1.0
        if attach is not None:
            inf = [('r', skel.by_name[attach], 1.0)]
        elif v.skin == 0:   # in the shape's bone's space
            inf = [('r', skel.by_name[m.bones[s.bone].name], 1.0)]
        else:
            ii = vs['_i0'][i]
            ww = vs['_w0'][i] if '_w0' in vs and n_inf > 1 else [1.0]
            inf = []
            for k in range(n_inf):
                if ww[k] > 0:
                    j = int(ii[k])
                    bone = skel.by_name[m.bones[palette[j]].name]
                    inf.append(('r' if v.skin == 1 and j >= ns else 's', bone, ww[k]))
        out.append({'_p0': [x * scale for x in vs['_p0'][i][:3]], '_n0': n, '_t0': t, 'hand': hand,
                    '_u0': vs['_u0'][i][:2] if '_u0' in vs else [0.0, 0.0], 'inf': inf})
    return out


def to_slot(vx, rigid_slot, skel):
    """A vertex made fit for a base shape of rigid (one matrix, bone space) or smooth (model space) skinning."""
    inf = vx['inf']
    kind, bone = inf[0][0], inf[0][1]
    if rigid_slot:
        if kind == 'r':
            return vx
        bone = max(inf, key=lambda x: x[2])[1]   # the strongest influence's bone space
        w = bb.mat_inv(skel.bones[bone].world)
        return dict(vx, _p0=bb.xform(w, vx['_p0']), _n0=bb.xform(w, vx['_n0'], 0), _t0=bb.xform(w, vx['_t0'], 0),
                    inf=[('r', bone, 1.0)])
    if kind == 's':
        return vx
    w = skel.bones[bone].world   # bone space to model space
    return dict(vx, _p0=bb.xform(w, vx['_p0']), _n0=bb.normalize(bb.xform(w, vx['_n0'], 0)),
                _t0=bb.normalize(bb.xform(w, vx['_t0'], 0)), inf=[('s', bone, 1.0)])


def uv_fit(verts, vtx, name, log):
    """(function, format) fitting texture coordinates to the base shape's format. A normalized format (0 to 1, or -1
    to 1) cannot repeat a texture: a 16-bit one is then changed to 16-bit floats, which take the same four bytes and
    hold the coordinates as they are, moved by whole repeats towards 0 (a texture looks the same a whole repeat
    further) to keep their precision. format is the new format, or None when the base's holds them. Another normalized
    format has them moved and scaled into it, the same for all the shape's vertices (the texture repeats less often)."""
    f = next((f for n, f, _, _ in vtx.attrs if n == '_u0'), None)
    if f is None or f & 0xff not in (1, 2):
        return (lambda u: u), None
    lo_ok = 0.0 if f & 0xff == 1 else -1.0
    us = [vx['_u0'] for vx in verts]
    lo = [min(u[k] for u in us) for k in range(2)]
    hi = [max(u[k] for u in us) for k in range(2)]
    if all(lo[k] >= lo_ok and hi[k] <= 1.0 for k in range(2)):
        return (lambda u: u), None
    if f >> 8 == 0x12:   # two 16-bit components
        shift = [-float(math.floor((lo[k] + hi[k]) / 2)) for k in range(2)]
        log(f'  {name}: texture coordinates kept as 16-bit floats (they repeat the texture)')
        return (lambda u: [u[k] + shift[k] for k in range(2)]), 0x1205
    span = max(hi[k] - lo[k] for k in range(2)) or 1.0
    scale = min(1.0, (1.0 - lo_ok) / span)
    shift = [lo_ok - lo[k] * scale if lo[k] * scale < lo_ok or hi[k] * scale > 1.0 else 0.0 for k in range(2)]
    log(f'  {name}: texture coordinates scaled by {scale:.2f} to fit its format')
    return (lambda u: [u[k] * scale + shift[k] for k in range(2)]), None


def atlas_uvs(verts, key, cell, spec, log):
    """A shape's UVs moved into its atlas cell (x, y, width, height of the texture, v down from the top): the whole
    repeats of its texture that its UVs run over are laid side by side in the cell (spec['_tiles'][key])."""
    us = [vx['_u0'][0] for vx in verts]
    vs = [vx['_u0'][1] for vx in verts]
    u0, v0 = math.floor(min(us) + 1e-3), math.floor(min(vs) + 1e-3)
    nu = max(1, math.ceil(max(us) - 1e-3) - u0)
    nv = max(1, math.ceil(max(vs) - 1e-3) - v0)
    if spec['_tiles'].setdefault(key, (nu, nv)) != (nu, nv):
        raise ValueError(f'{key}: shapes sharing an atlas cell repeat their texture differently')
    x, y, cw, ch = cell
    for vx in verts:
        u, v = vx['_u0'][:2]
        vx['_u0'] = [x + min(max(u - u0, 0.0), nu) / nu * cw, y + min(max(v - v0, 0.0), nv) / nv * ch]
    log(f'  {key}: UVs into the atlas cell at {x:g},{y:g} ({nu}x{nv} repeats)')


def build_shapes(base_f, base_m, botw, skel, spec, log):
    """({base shape name: Rec(vertices, count, indices, skin_bones, radius, center, extent)}, (box min, box max) of
    the drawn geometry in the bind pose, {base material names drawn}). Sets skel.smooth and skel.rigid: the
    creature's matrix palette with the bones added that a base shape's way of skinning needs."""
    gathered = {}   # base shape name -> [vertex dicts], [indices]
    rigid_slot = {s.name: base_m.vtx[s.vtx].skin == 1 for s in base_m.shapes}
    files = {}
    cells = {c[0]: c[1] for a in spec.get('atlas', {}).values() for c in a['cells']}
    spec['_tiles'] = {}
    main = {'file': spec['botw_model'], 'model': spec.get('botw_name', spec['botw_model']), 'slots': spec['slots']}
    for part in [main] + spec.get('parts', []):
        if part['file'] not in files:
            files[part['file']] = bb.Bfres(bb.yaz0(botw_file(botw, f'Model/{part["file"]}.sbfres')))
        f = files[part['file']]
        m = next(x for x in f.models if x.name == part['model'])
        for s in m.shapes:
            target = part['slots'].get(s.name) or part['slots'].get('*')
            if not target:
                log(f'  {part["model"]}: shape {s.name} is not drawn')
                continue
            src = source_vertices(f, m, s, part.get('attach'), skel, part.get('scale', 1.0))
            key = s.name if s.name in cells else part['model'] if part['model'] in cells else None
            if key:
                atlas_uvs(src, key, cells[key], spec, log)
            for t in (target if isinstance(target, tuple) else (target,)):
                verts, idx = gathered.setdefault(t, ([], []))
                first = len(verts)
                verts += [to_slot(vx, rigid_slot[t], skel) for vx in src]
                idx += [first + k for k in f.indices(s.meshes[0])]
    # the palette: the creature's, then bones a base shape's skinning needs
    smooth, rigid = list(skel.smooth), list(skel.rigid)
    for verts, _ in gathered.values():
        for vx in verts:
            for kind, bone, _ in vx['inf']:
                lst = smooth if kind == 's' else rigid
                if bone not in lst:
                    lst.append(bone)
    added = (len(smooth) - len(skel.smooth), len(rigid) - len(skel.rigid))
    if any(added):
        log(f'  matrix palette: {added[0]} smooth and {added[1]} rigid bones added for the base\'s shapes')
    skel.smooth, skel.rigid = smooth, rigid
    sidx = {b: i for i, b in enumerate(smooth)}
    ridx = {b: len(smooth) + i for i, b in enumerate(rigid)}
    out = {}
    pts = []
    drawn = set()
    for s in base_m.shapes:
        v = base_m.vtx[s.vtx]
        stride = v.strides[0]
        kb = max(v.skin, 1)
        verts, idx = gathered.get(s.name, ([], []))
        if not verts:   # one empty triangle
            verts = [{'_p0': [0.0, 0.0, 0.0], '_n0': [0.0, 1.0, 0.0], '_t0': [1.0, 0.0, 0.0], 'hand': 1.0,
                      '_u0': [0.0, 0.0], 'inf': [('s', smooth[0], 1.0)]}]
            idx = [0, 0, 0]
        else:
            drawn.add(base_m.mats[s.mat].name)
        defaults = median_defaults(base_f, v)
        data = bytearray(stride * len(verts))
        used = set()
        uv, uv_format = uv_fit(verts, v, s.name, log)
        for k, vx in enumerate(verts):
            inf = [((sidx if kind == 's' else ridx)[bone], w) for kind, bone, w in vx['inf']]
            q = quantize_weights(inf, kb)
            used.update(bone for _, bone, _ in vx['inf'])
            vals = {'_p0': vx['_p0'], '_n0': bb.normalize(vx['_n0']) + [1.0],
                    '_t0': bb.normalize(vx['_t0']) + [vx['hand']], '_u0': uv(vx['_u0']),
                    '_i0': [m for m, _ in q] + [0] * 4, '_w0': [w / 255 for _, w in q] + [0.0] * 4}
            for name, f, off, _ in v.attrs:
                val = vals.get(name)
                if val is None:
                    val = vals['_u0'] if name.startswith('_u') else defaults.get(name, [0.0, 0.0, 0.0, 1.0])
                enc = bb.encode_attr(val, uv_format if name == '_u0' and uv_format else f)
                data[k * stride + off:k * stride + off + len(enc)] = enc
            if s.name in gathered:
                kind, bone, _ = vx['inf'][0]
                pts.append(bb.xform(skel.bones[bone].world, vx['_p0']) if kind == 'r' else vx['_p0'])
        g = bb.Rec()
        g.vertices, g.count, g.indices, g.skin_bones = bytes(data), len(verts), idx, sorted(used)
        g.formats = {'_u0': uv_format} if uv_format else {}
        out[s.name] = g
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    span = max(hi[k] - lo[k] for k in range(3))
    for g in out.values():   # large enough for any pose: never culled
        g.center = [(lo[k] + hi[k]) / 2 for k in range(3)]
        g.extent = [span for _ in range(3)]
        g.radius = span * 2
    return out, (lo, hi), drawn


# ---- textures

def build_textures(fs, botw, spec, base_m, drawn, project, files, log):
    """{material name: texture names} of the drawn materials, the textures made into files."""
    base = spec['base']
    tex_name = 'BotW_' + spec['name']
    if len(tex_name) > len(base):
        raise ValueError(f'{tex_name} is longer than {base}')
    sources = {}

    def texture(how):
        file = how[2] if len(how) > 2 else spec.get('botw_tex', spec['botw_model'])
        if file not in sources:
            sources[file] = tx.BotwTextures(botw_file(botw, f'Model/{file}.Tex.sbfres'))
        return sources[file], sources[file].tex[how[1]]
    out = {}
    made = {}
    for mt in base_m.mats:
        if mt.name not in drawn:
            continue
        plan = spec['textures'].get(mt.name, {})
        names = []
        for old in mt.textures:
            rel = f'Tex/{old}.bntx.zs'
            if base not in old or rel not in fs.files:   # shared, or no file of its own: kept
                names.append(old)
                continue
            new = old.replace(base, tex_name)   # a texture file's name is no longer than its template's
            names.append(new)
            if new in made:
                continue
            kind = old.rsplit('_', 1)[1]
            how = plan.get(kind) or (('const', CONSTANTS[kind]) if kind in CONSTANTS else None)
            if not how:
                raise ValueError(f'{mt.name}: nothing makes the texture {old}')
            if how[0] == 'const':
                fmt, mips, th, chan, data = tx.constant_texture(how[1])
                w = h = 64
            elif how[0] == 'atlas':
                a = spec['atlas'][mt.name]
                w, h = a['size'][:2] if how[1] == 'albedo' else a['size'][2:]
                cells = []
                for key, (x, y, cw, ch), alb, nrm, file in a['cells']:
                    nu, nv = spec['_tiles'][key]
                    src, r = texture(('', alb if how[1] == 'albedo' else nrm) + ((file,) if file else ()))
                    cells.append((round(x * w), round(y * h), round(cw * w), round(ch * h), nu, nv, src, r))
                fmt, mips, th, chan, data = tx.atlas_texture(w, h, how[1] == 'normal', cells)
            else:
                src, r = texture(how)
                fmt, mips, th, chan, data = (tx.convert_albedo if how[0] == 'albedo' else tx.convert_normal)(src, r)
                w, h = r.w, r.h
            template, _ = fs.zs(rel)
            files[f'Tex/{new}.bntx.zs'] = fs.compress(tx.game_bntx(template, new, fmt, w, h, mips, th, chan, data),
                                                      None)
            made[new] = True
            log(f'  {new}: {how[0]} {how[1]!s} ({w}x{h}, format {fmt:#06x}, {mips} levels)')
        out[mt.name] = names
    return out


# ---- animations

def scaled_curve(cv, k):
    c = bb.Curve()
    for a in ('flags', 'nkey', 'target', 'scale', 'offset', 'delta', 'kind', 'keys'):
        setattr(c, a, getattr(cv, a))
    c.frames = [x * k for x in cv.frames]
    c.start, c.end = cv.start * k, cv.end * k
    return c


ROOT_CURVES = 0xffc0   # a bone animation's flags: which components have curves


def still_bone(bo, scale, rot, trans):
    r = bb.Rec()
    r.name, r.begin, r.curves = bo.name, bo.begin, []
    r.flags = (bo.flags & ~ROOT_CURVES) | 0x38
    r.base = list(scale) + list(rot) + list(trans)
    return r


def convert_animation(src, base_anim, skel, root, hold=None, own=False, sink=0.0):
    """The creature animation src as base_anim: its name and loop setting, and (one that does not loop, unless own) its
    length. root: the bone whose motion is taken out (and which is lowered by sink). hold: a frame of src held for the
    base's length (a base animation of no frames holds src's first)."""
    a = bb.Rec()
    a.name = base_anim.name
    loop = base_anim.flags & 4
    a.flags = (src.flags & ~4) | loop
    bind = {b.name: b for b in skel.bones}
    if base_anim.frames == 0 or hold is not None:
        a.frames = base_anim.frames
        p = bb.pose(src, min(hold or 0, src.frames))
        a.bones = []
        for bo in src.bones:
            sc, rt, tr = p[bo.name]
            b = bind[bo.name]
            sc = [x if x is not None else y for x, y in zip(sc, b.scale)]
            rt = [x if x is not None else y for x, y in zip(rt, b.rot)]
            tr = [x if x is not None else y for x, y in zip(tr, b.trans)]
            if bo.name == root:
                tr = [b.trans[0], b.trans[1] - sink, b.trans[2]]
                rt = list(b.rot)
            a.bones.append(still_bone(bo, sc, rt, tr))
        return a
    keep = loop or own
    k = 1.0 if keep else base_anim.frames / src.frames
    a.frames = src.frames if keep else base_anim.frames
    a.bones = []
    for bo in src.bones:
        if bo.name == root:
            b = bind[bo.name]
            a.bones.append(still_bone(bo, b.scale, b.rot, [b.trans[0], b.trans[1] - sink, b.trans[2]]))
            continue
        r = bb.Rec()
        r.name, r.flags, r.begin, r.base = bo.name, bo.flags, bo.begin, bo.base
        r.curves = [scaled_curve(cv, k) for cv in bo.curves]
        a.bones.append(r)
    return a


def root_speed(anim, root):
    """Metres a second the root bone moves forward in an animation (30 frames a second)."""
    p0 = bb.pose(anim, 0, {root})[root][2]
    p1 = bb.pose(anim, anim.frames, {root})[root][2]
    if p0[2] is None or p1[2] is None or not anim.frames:
        return None
    return math.hypot(p1[0] - p0[0], p1[2] - p0[2]) / anim.frames * 30


def build_animations(fs, botw, spec, skel, project, files, log):
    """The animation file and its events for the actor. Returns {MovementParam field: speed}."""
    base = spec['base']
    template, d = fs.zs(f'Model/{base}_Animation.anim.bfres.zs')
    base_anims = bb.read_anims(template)
    src = {a.name: a for a in bb.read_anims(bb.yaz0(botw_file(botw, f'Model/{spec["botw_anims"]}.sbfres')))}
    root = skel.bones[0].name
    out = []
    used = {}
    own = spec.get('own_length')
    for ba in base_anims:
        name = next(n for pat, n in spec['anims'] if re.search(pat, ba.name))
        name, _, hold = name.partition('@')
        if name not in src:
            raise ValueError(f'{spec["botw_anims"]} has no animation {name}')
        out.append(convert_animation(src[name], ba, skel, root, int(hold) if hold else None,
                                     bool(own and re.search(own, ba.name)), spec.get('sink', 0.0)))
        used.setdefault(name + ('@' + hold if hold else ''), []).append(ba.name)
    log(f'  animations: {len(out)} ({len(used)} of the creature\'s): '
        + '; '.join(f'{n} as {", ".join(b)}' for n, b in sorted(used.items())))
    anim_project = f'{project}_Animation'
    data = bb.write_anim_file(template, f'{anim_project}.anim', out)
    files[f'Model/{anim_project}.anim.bfres.zs'] = fs.compress(data, d)
    ev = f'AnimationEvent/Animation/{base}_Animation.anim.baev.zs'
    if ev in fs.files:
        files[f'AnimationEvent/Animation/{anim_project}.anim.baev.zs'] = fs.raw(ev)
    speeds = {}
    for field, name in spec.get('speeds', {}).items():
        v = root_speed(src[name], root)
        if v:
            speeds[field] = v
    return speeds


# ---- packs (SARC)

def sarc_hash(name, mult):
    h = 0
    for c in name.encode():
        h = (h * mult + c) & 0xFFFFFFFF
    return h


def sarc_build(template, members):
    """A SARC of members [(name, bytes, alignment)], the header's fields and the data start's alignment as in
    template."""
    mult = bb.u32(template, 0x14 + 8)
    ver = bb.u16(template, 0x10)
    members = sorted(members, key=lambda m: sarc_hash(m[0], mult))
    names = bytearray()
    name_at = []
    for n, _, _ in members:
        name_at.append(len(names))
        names += n.encode() + b'\0'
        names += bytes(bb.align(len(names), 4) - len(names))
    sfat = bytearray(b'SFAT' + struct.pack('<HHI', 0xc, len(members), mult))
    data = bytearray()
    for (n, body, a), at in zip(members, name_at):
        start = bb.align(len(data), a)
        data += bytes(start - len(data)) + body
        sfat += struct.pack('<4I', sarc_hash(n, mult), 0x01000000 | at // 4, start, start + len(body))
    head_len = 0x14 + len(sfat) + 8 + len(names)
    base_align = max(a for _, _, a in members)
    tb = bb.u32(template, 0xc)
    while base_align < 0x2000 and tb % (base_align * 2) == 0:
        base_align *= 2
    base = bb.align(head_len, base_align)
    out = bytearray(b'SARC' + struct.pack('<HHIIHH', 0x14, 0xFEFF, 0, base, ver, 0))
    out += sfat + b'SFNT' + struct.pack('<HH', 8, 0) + names
    out += bytes(base - len(out)) + data
    struct.pack_into('<I', out, 8, len(out))
    return bytes(out)


def member_align(start):
    a = 1
    while a < 0x2000 and start and start % (a * 2) == 0:
        a *= 2
    return max(a, 4)


def ref_forms(path):
    """The ways a pack member is named from another: '?<path>', 'Work/<path, .gyml>' and the path itself."""
    forms = ['?' + path, path]
    if path.endswith('.bgyml'):
        forms.append('Work/' + path[:-6] + '.gyml')
    return forms


def walk_strings(node, fn):
    """node with fn applied to each string value."""
    if isinstance(node, dict):
        return {k: walk_strings(v, fn) for k, v in node.items()}
    if isinstance(node, list):
        return [walk_strings(v, fn) for v in node]
    if node[0] == T_STR:
        return (T_STR, fn(node[1]))
    return node


SHAPE_KEYS = {'Center', 'Max', 'Min', 'CenterA', 'CenterB', 'Radius', 'HalfExtents', 'HeightHead', 'HeightShoulder',
              'HeightStep', 'CapsuleCenterA', 'CapsuleCenterB', 'CapsuleRadius', 'Offset'}


def scale_lengths(node, s, keys=SHAPE_KEYS):
    """node with the lengths under keys scaled by s (and AutoCalc's volume and inertia by s^3 and s^2)."""
    def sc(v, f):
        if isinstance(v, dict):
            return {k: sc(x, f) for k, x in v.items()}
        if isinstance(v, list):
            return [sc(x, f) for x in v]
        if v[0] == T_FLOAT and f32(v) > -0.5:   # -1 marks "not set"
            return fl(f32(v) * f)
        return v
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in keys:
                out[k] = sc(v, s)
            elif k == 'Volume':
                out[k] = sc(v, s ** 3)
            elif k == 'Tensor':
                out[k] = sc(v, s * s)
            else:
                out[k] = scale_lengths(v, s, keys)
        return out
    if isinstance(node, list):
        return [scale_lengths(v, s, keys) for v in node]
    return node


def set_field(root, path, v, n):
    """root (a BYML dictionary) with the field at path (keys, outermost first) set to v: a float, or a value of the
    field's own type."""
    k = path[0]
    if k not in root:
        raise ValueError(f'{n} has no {k}')
    if len(path) > 1:
        return {**root, k: set_field(root[k], path[1:], v, n)}
    if isinstance(v, (list, dict)):   # a list or dictionary goes in as it is
        return {**root, k: v}
    return {**root, k: fl(v) if isinstance(v, float) else (root[k][0], v)}


def shape_key(n, base):
    """The name of a Phive ShapeParam member after the base's name: Tgt_0 for Phive/ShapeParam/<base>_Tgt_0...."""
    stem = n.rsplit('/', 1)[1].split('.', 1)[0]
    return stem[len(base) + 1:] if stem.startswith(base + '_') else stem


def hit_capsule(root, skel, bone, a, b, r):
    """A ShapeParam (root, the base's) made one capsule of radius r from a to b, points of the creature's rest pose,
    in the frame of the creature's bone that carries it."""
    if bone not in skel.by_name:
        raise ValueError(f'the skeleton has no bone {bone}')
    inv = bb.mat_inv(skel.bones[skel.by_name[bone]].world)
    la, lb = bb.xform(inv, a), bb.xform(inv, b)
    vec = lambda v: {k: fl(x) for k, x in zip('XYZ', v)}   # noqa: E731
    old = (root.get('Capsule') or root.get('Sphere') or [{}])[0]
    cap = {'CenterA': vec(la), 'CenterB': vec(lb), 'MaterialPresets': old.get('MaterialPresets', []), 'Radius': fl(r)}
    lo = [min(x, y) - r for x, y in zip(la, lb)]
    hi = [max(x, y) + r for x, y in zip(la, lb)]
    length = math.dist(la, lb)
    auto = {**root.get('AutoCalc', {}), 'Center': vec([(x + y) / 2 for x, y in zip(la, lb)]), 'Min': vec(lo),
            'Max': vec(hi), 'Volume': fl(math.pi * r * r * (length + 4 * r / 3))}
    return {**{k: v for k, v in root.items() if k not in ('Capsule', 'Sphere', 'Box')}, 'AutoCalc': auto,
            'Capsule': [cap]}


def enemy_pack(fs, spec, project, skel, speeds, log):
    """(path, file) of the new actor's pack: the base's with the changed members renamed after the actor."""
    base, actor = spec['base'], spec['actor']
    pack = spec.get('pack', base)
    size = skel.size
    data, d = fs.zs(f'Pack/Actor/{pack}.pack.zs')
    members = darklink.sarc_members(data)
    trees = {}
    opaque = {}   # members of a BYML form randomizer does not read: checked below to name nothing renamed
    for n, body, _ in members:
        if n.endswith('.bgyml'):
            try:
                trees[n] = randomizer.byml_read(body)
            except ValueError:
                opaque[n] = body
    changed = {}
    found = set()   # the blackboard keys set
    placed = set()   # the hit shapes made
    scaled_dist = []   # the members whose distances were scaled

    def edit(n, root):
        trees[n] = (trees[n][0], root)
        changed[n] = True
    param = f'Actor/{pack}.engine__actor__ActorParam.bgyml'
    if param not in trees:
        raise ValueError(f'{pack}: the pack has no {param}')
    if spec.get('drop'):
        ver, root = trees[param]
        comps = root['Components']
        missing = [k for k in spec['drop'] if k not in comps]
        if missing:
            raise ValueError(f'{base}: its ActorParam has no {", ".join(missing)}')
        trees[param] = (ver, {**root, 'Components': {k: v for k, v in comps.items() if k not in spec['drop']}})
        log(f'  components left out: {", ".join(spec["drop"])}')
    for n, (ver, root) in list(trees.items()):
        if n.startswith('Component/ModelInfo/'):
            root = dict(root)
            root['ModelProjectName'] = (T_STR, project)
            root['FmdbName'] = (T_STR, root['FmdbName'][1])
            edit(n, root)
        elif n.startswith('Component/AnimationParam/'):
            edit(n, {**root, 'AnimationResources': [{'ModelProjectName': (T_STR, f'{project}_Animation')}]})
        elif n.startswith('Phive/ShapeParam/') and shape_key(n, base) in spec.get('boxes', {}):
            if len(root.get('Box', ())) != 1:
                raise ValueError(f'{n} is not one box')
            c, h = spec['boxes'][shape_key(n, base)]
            vec = lambda v: {k: fl(x) for k, x in zip('XYZ', v)}   # noqa: E731
            auto = {**root.get('AutoCalc', {}), 'Center': vec(c), 'Min': vec([a - b for a, b in zip(c, h)]),
                    'Max': vec([a + b for a, b in zip(c, h)]), 'Volume': fl(8 * h[0] * h[1] * h[2])}
            edit(n, {**root, 'AutoCalc': auto, 'Box': [{**root['Box'][0], 'Center': vec(c), 'HalfExtents': vec(h)}]})
            placed.add(shape_key(n, base))
        elif n.startswith('Phive/ShapeParam/') and shape_key(n, base) in spec.get('hit_shapes', {}):
            edit(n, hit_capsule(root, skel, *spec['hit_shapes'][shape_key(n, base)]))
            placed.add(shape_key(n, base))
        elif n.startswith('Phive/RigidBodyControllerSensorParam/') and (spec['bone_map'] or spec.get('hit_shapes')):
            units = [{**u, 'TrackingBoneName': (T_STR, spec['bone_map'].get(u['TrackingBoneName'][1],
                                                                            u['TrackingBoneName'][1]))}
                     for u in root['RigidBodyControllerUnitAry']]
            group = shape_key(n, base)   # the controller of the shapes <group>_0, <group>_1, ... (unit k carries _k)
            for k, u in enumerate(units):
                if f'{group}_{k}' in spec.get('hit_shapes', {}):
                    units[k] = {**u, 'TrackingBoneName': (T_STR, spec['hit_shapes'][f'{group}_{k}'][0])}
            if units != root['RigidBodyControllerUnitAry']:
                edit(n, {**root, 'RigidBodyControllerUnitAry': units})
        elif n.startswith('Phive/ShapeParam/') or n.startswith('Phive/Ragdoll/Structure/'):
            edit(n, scale_lengths(root, spec.get('physics_size', size)))
        elif n.startswith('Component/ActorPositionCalculatorParam/'):
            edit(n, scale_lengths(root, size, {'Offset', 'BowLockOnCameraLookPosOffset',
                                               'CursorNoticeIconBasePosOffset', 'NaviHintCameraLookAtOffset'}))
        elif n.startswith('Phive/ControllerSetParam/') and any(k in root for k in ('ClothList', *BASE_BONE_CONTROL)):
            # the base mesh's cloth, and the controllers that move the base's own bones after the animation (its
            # bone modifiers and foot IK): run on the creature's skeleton they leave the model drawn nowhere
            edit(n, {k: ([] if k in ('Cloth', 'ClothAdvandecOption', 'ClothList', 'ClothReaction') else v)
                     for k, v in root.items() if k not in BASE_BONE_CONTROL})
        elif n.startswith('Component/Blackboard/BlackboardParamTable/') and spec.get('blackboard'):
            root, hit = dict(root), False
            for k, rows in root.items():
                if k.startswith('BlackboardParam') and isinstance(rows, list):
                    for i, r in enumerate(rows):
                        key = r.get('BBKey', (0, ''))[1]
                        if key in spec['blackboard']:
                            root[k] = rows = list(rows)
                            v = spec['blackboard'][key]
                            rows[i] = {**r, 'InitVal': (r['InitVal'][0], v) if isinstance(r['InitVal'], tuple) else v}
                            found.add(key)
                            hit = True
            if hit:
                edit(n, root)
        elif spec.get('distance_scale') and any(n.startswith(f) for f in DISTANCE_MEMBERS):
            count = [0]

            def scaled(node, key=''):
                if isinstance(node, dict):
                    return {k: scaled(v, k) for k, v in node.items()}
                if isinstance(node, list):
                    return [scaled(v, key) for v in node]
                if ('Distance' in key or key == 'Dist') and isinstance(node, tuple) and node[0] == T_FLOAT \
                        and f32(node) != -1.0:
                    count[0] += 1
                    return fl(f32(node) * spec['distance_scale'])
                return node
            root = scaled(root)
            if count[0]:
                edit(n, root)
                scaled_dist.append(f'{n.split("/")[-1].split(".")[0]} ({count[0]})')
        elif any(n.startswith(f) for f in spec.get('params', {})):
            root = dict(root)
            for f, fields in spec['params'].items():
                if n.startswith(f):
                    for k, v in fields.items():
                        root = set_field(root, k.split('.'), v, n)
            edit(n, root)
        elif n.startswith('BSA/MovementParam/') and speeds:
            root = dict(root)
            for k, v in speeds.items():
                if k in root:
                    root[k] = fl(v)
            edit(n, root)
    wanted = set(spec.get('hit_shapes', ())) | set(spec.get('boxes', ()))
    if wanted - placed:
        raise ValueError(f'{base}: no shape {", ".join(sorted(wanted - placed))}')
    for k in sorted(set(spec.get('boxes', ())) & placed):
        log(f'  box {k}: centre {spec["boxes"][k][0]}, half extents {spec["boxes"][k][1]}')
    for k in sorted(set(spec.get('hit_shapes', ())) & placed):
        bone, a, b, r = spec['hit_shapes'][k]
        log(f'  hit shape {k}: a capsule of radius {r} from {a} to {b}, on {bone}')
    if scaled_dist:
        log(f'  distances times {spec["distance_scale"]}: {", ".join(scaled_dist)}')
    missing = set(spec.get('blackboard', ())) - found
    if missing:
        raise ValueError(f'{base}: no blackboard table has {", ".join(sorted(missing))}')
    if spec.get('blackboard'):
        log('  blackboard: ' + ', '.join(f'{k} = {v}' for k, v in spec['blackboard'].items()))
    # every changed member gets a name of its own, and so does each member that names a renamed one
    # (a pack other than the base's holds the base's ActorParam too, as the parent of its own: renamed after the
    # base, it would take the actor's ActorParam's name and be its own parent)
    renames = {param: f'Actor/{actor}.engine__actor__ActorParam.bgyml'}
    while True:
        for n in changed:
            if n not in renames:
                folder, _, file = n.rpartition('/')
                new = n.replace(base, actor) if base in file else f'{folder}/{actor}_{file}'
                if new in renames.values():
                    new = f'{folder}/{actor}_{file}'
                renames[n] = new
        forms = {}
        for old, new in renames.items():
            if old == param and param not in changed:   # renamed only as the pack's entry, named by nothing
                continue
            for a, b in zip(ref_forms(old), ref_forms(new)):
                forms[a] = b
        grew = False
        for n, (ver, root) in trees.items():
            new_root = walk_strings(root, lambda s: forms.get(s, s))
            if new_root != root:
                trees[n] = (ver, new_root)
                if n not in changed:
                    changed[n] = True
                    grew = True
        if not grew:
            break
    for n, body in opaque.items():
        for old in renames:
            if any(f.encode() in body for f in ref_forms(old)[:1] + ref_forms(old)[2:]):
                raise ValueError(f'{n} names {old}, which is renamed, and cannot be read')
    renames[param] = f'Actor/{actor}.engine__actor__ActorParam.bgyml'
    out = []
    for n, body, start in members:
        if n in changed or n == param:
            ver, root = trees[n]
            out.append((renames[n], randomizer.byml_write(ver, root), member_align(start)))
        else:
            out.append((n, body, member_align(start)))
    log(f'  pack of {base}: {len(out)} members, {len(changed)} renamed for the actor: '
        + ', '.join(sorted(renames[n].rsplit('/', 1)[0] for n in changed)))
    return f'Pack/Actor/{actor}.pack.zs', fs.compress(sarc_build(data, out), d)


# ---- sounds

BARSLIST = 'Sound/ResourceListPack/BarslistPack.Product.111.byml.zs'


def build_sounds(fs, botw, spec, files_in, log):
    """The actor's own sound archive and its row in the archive list: the base's archive with the clips that
    spec['sounds']['picks'] names made from the creature's own sounds. The base's sound links (its SLink user) still
    play them, by the base's clip names, now from the actor's archive. Returns {path: compressed bytes}."""
    snd = spec['sounds']
    pack = spec.get('pack', spec['base'])
    ver, root = randomizer.byml_read(bs.byml_swap(table_data(fs, BARSLIST, files_in)))
    table = root['BarslistTable']
    if pack not in table:
        raise ValueError(f'{BARSLIST} has no row for {pack}')
    names = [n[1] for n in table[pack]['Bars']]
    own = f'{pack}.bars'
    if own not in names:
        raise ValueError(f'{pack}: its archive list {names} has no {own}')
    path = f'Sound/Resource/{own}.zs'
    base, d = fs.zs(path)
    src = botw_file(botw, f'Sound/Resource/{snd["bars"]}.bars')
    clips = bs.botw_clips(src)
    wanted = {c for cs in snd['picks'].values() for c in cs}
    missing = sorted(wanted - set(clips))
    if missing:
        raise ValueError(f'{snd["bars"]}.bars has no {", ".join(missing)}')
    pcm = {c: bs.fwav_pcm(src, clips[c][1]) for c in sorted(wanted)}
    data, n = bs.rebuild_bars(base, snd['picks'], pcm, log)
    mine = f'{spec["actor"]}.bars'
    table[spec['actor']] = {'Bars': [(T_STR, mine if x == own else x) for x in names]}
    if isinstance(root.get('LargeBars'), dict) and own in root['LargeBars']:
        root['LargeBars'][mine] = root['LargeBars'][own]
    _, ld = fs.zs(BARSLIST)
    log(f'  sounds: {n} clips of {own} made from {len(wanted)} of {snd["bars"]}.bars ({len(data)} bytes)')
    return {f'Sound/Resource/{mine}.zs': fs.compress(data, d),
            BARSLIST: fs.compress(bs.byml_swap(randomizer.byml_write(ver, root)), ld)}


# ---- tables and placements

def table_data(fs, path, files_in):
    _, d = fs.zs(path)
    return fs.zstd.decompress(files_in[path], zstd_dict=d) if path in files_in else fs.zs(path)[0]


def rsdb_rows(fs, spec, project, bounds, heap_scale, files_in):
    """RSDB/ActorInfo and RSDB/GameActorInfo with a row for the actor (a copy of the base's), each read from the mod
    being written when it already has the table (another enemy of this mod), else from the game."""
    out = {}
    for table in ('ActorInfo', 'GameActorInfo'):
        path = f'RSDB/{table}.Product.111.rstbl.byml.zs'
        _, d = fs.zs(path)
        ver, rows = randomizer.byml_read(table_data(fs, path, files_in))
        ids = [r['__RowId'][1] for r in rows]
        base = rows[ids.index(spec.get('pack', spec['base']))]
        rows = [r for r in rows if r['__RowId'][1] != spec['actor']]
        row = dict(base)
        row['__RowId'] = (T_STR, spec['actor'])
        if table == 'ActorInfo':
            row['ActorName'] = (T_STR, spec['actor'])
            row['ModelProjectName'] = (T_STR, project)
            if 'AnimationResources' in row:
                row['AnimationResources'] = [{'ModelProjectName': (T_STR, f'{project}_Animation')}]
            lo, hi = bounds
            for key, v in (('ModelAabbMin', lo), ('ModelAabbMax', hi)):
                if key in row:
                    row[key] = {k: fl(x) for k, x in zip('XYZ', v)}
            for key in ('InstanceHeapSize', 'PreActorRenderInfoHeapSize'):
                if key in row:
                    row[key] = (row[key][0], int(row[key][1] * heap_scale) + 0x10000 & ~0xff)
        rows.append(row)
        rows.sort(key=lambda r: r['__RowId'][1].encode())
        out[path] = fs.compress(randomizer.byml_write(ver, rows), d)
    return out


TAG_PATH = 'RSDB/Tag.Product.111.rstbl.byml.zs'


class TagTable:
    """RSDB/Tag: PathList (each actor as the three strings 'Work/Actor/', its name and
    '.engine__actor__ActorParam.gyml', in the order of the whole names), TagList, BitTable (bit actor * tags + tag, low
    bit first) and RankTable (for each tag and each 32 actors k: u32 the number of actors before 32 * (k + 1) that
    have it)."""

    def __init__(self, data):
        self.ver, root = randomizer.byml_read(data)
        p = [s[1] for s in root['PathList']]
        self.paths = [tuple(p[i:i + 3]) for i in range(0, len(p), 3)]
        self.tags = [s[1] for s in root['TagList']]
        bits = root['BitTable'][1]
        nt = len(self.tags)
        self.on = [{t for t in range(nt) if bits[(a * nt + t) >> 3] >> ((a * nt + t) & 7) & 1}
                   for a in range(len(self.paths))]
        if self.rank() != root['RankTable'][1] or self.bits() != bits:
            raise ValueError('RSDB/Tag is not laid out as expected')

    def bits(self):
        nt = len(self.tags)
        out = bytearray((len(self.paths) * nt + 7) // 8)
        for a, on in enumerate(self.on):
            for t in on:
                out[(a * nt + t) >> 3] |= 1 << ((a * nt + t) & 7)
        return bytes(out)

    def rank(self):
        na = len(self.paths)
        blocks = (na + 31) // 32
        out = []
        for t in range(len(self.tags)):
            n = 0
            for k in range(blocks):
                n += sum(1 for a in range(32 * k, min(32 * k + 32, na)) if t in self.on[a])
                out.append(n)
        return struct.pack('<%dI' % len(out), *out)

    def index(self, name):
        for i, p in enumerate(self.paths):
            if p[1] == name:
                return i
        return None

    def add(self, name, like):
        i = self.index(name)
        if i is not None:
            del self.paths[i], self.on[i]
        on = set(self.on[self.index(like)])
        p = ('Work/Actor/', name, '.engine__actor__ActorParam.gyml')
        keys = [''.join(x).encode() for x in self.paths]
        at = next((k for k, x in enumerate(keys) if x > ''.join(p).encode()), len(keys))
        self.paths.insert(at, p)
        self.on.insert(at, on)
        return sorted(self.tags[t] for t in on)

    def write(self):
        root = {'PathList': [(T_STR, s) for p in self.paths for s in p], 'TagList': [(T_STR, t) for t in self.tags],
                'BitTable': (randomizer.T_BIN, self.bits()), 'RankTable': (randomizer.T_BIN, self.rank())}
        return randomizer.byml_write(self.ver, root)


def tag_row(fs, spec, files_in):
    """RSDB/Tag with the actor given the base's tags."""
    t = TagTable(table_data(fs, TAG_PATH, files_in))
    tags = t.add(spec['actor'], spec.get('pack', spec['base']))
    return {TAG_PATH: fs.compress(t.write(), fs.zs(TAG_PATH)[1])}, tags


def placements(fs, spec, files_in, log):
    """{path: file} of the areas' Dynamic placement files with the actor added beside enemies there."""
    out = {}
    for folder, area in spec['areas']:
        path = f'Banc/{folder}/{area}_Dynamic.bcett.byml.zs'
        _, d = fs.zs(path)
        ver, root = randomizer.byml_read(table_data(fs, path, files_in))
        actors = root['Actors']
        actors[:] = [a for a in actors if a['Gyaml'][1] != spec['actor']]
        hashes = {a['Hash'][1] for a in actors}
        fixed = spec.get('at', {}).get(area)
        if fixed:   # places given as they are (on the ground, no offset)
            ground = [{'Translate': [fl(c) for c in (x, y - 1.0, z)]}
                      for x, y, z in fixed]
        else:
            ground = [a for a in actors if a['Gyaml'][1] in spec['beside'] and 'Links' not in a]
        if not ground:   # the static file's enemies give the places then
            _, sroot = randomizer.byml_read(fs.zs(f'Banc/{folder}/{area}_Static.bcett.byml.zs')[0])
            ground = [a for a in sroot['Actors'] if a['Gyaml'][1] in spec['beside']]
        if not ground:
            raise ValueError(f'{area}: no enemy to stand beside')
        # spread out: each next place the one farthest from those taken and from this mod's other enemies
        pos = [[f32(x) for x in a['Translate']] for a in ground]
        taken = [[f32(x) for x in a['Translate']] for a in actors if a['Gyaml'][1].startswith('Enemy_BotW_')]
        picked = []
        while len(picked) < min(spec['count'], len(pos)):
            others = taken + [pos[j] for j in picked]
            picked.append(max(range(len(pos)), key=lambda i: min(
                [(pos[i][0] - o[0]) ** 2 + (pos[i][2] - o[2]) ** 2 for o in others] or [-i])))
        for n, i in enumerate(picked):
            seed = hashlib.sha256(f'{MOD_NAME}/{spec["actor"]}/{area}/{n}'.encode()).digest()
            h = int.from_bytes(seed[:8], 'little')
            while h in hashes:
                h = (h + 1) & 0xFFFFFFFFFFFFFFFF
            hashes.add(h)
            x, y, z = pos[i]
            x += 0.0 if fixed else spec.get('offset', 6.0)
            actors.append({
                'Gyaml': (T_STR, spec['actor']),
                'Hash': (randomizer.T_UINT64, h),
                'Phive': {'Placement': {'ID': (randomizer.T_UINT64, h)}},
                'Rotate': [fl(0.0), fl((n * 2.1) % 6.2831), fl(0.0)],
                'SRTHash': (randomizer.T_UINT, int.from_bytes(seed[8:12], 'little')),
                'Translate': [fl(c) for c in (x, y + 1.0, z)],
            })
            where = '(a set place)' if fixed else f'beside {ground[i]["Gyaml"][1]}'
            log(f'  {area}: {spec["actor"]} at ({x:.1f}, {y + 1:.1f}, {z:.1f}) {where}')
        out[path] = fs.compress(randomizer.byml_write(ver, root), d)
    return out


AUTO_PARAMS = 'Scene/Component/AutoPlacementMgrParam'


def spawner(fs, spec, files_in, log):
    """{path: file} of an enemy spawner for the actor and the areas' scene settings with it added. A spawner (an
    AutoPlacementMgrParam named in an area's GameBancParam EnemyAutoPlacementParams) makes its actor near Link from time
    to time, as the desert's Leevers are made; spec['spawner']: 'like', the spawner it starts from ($parent), 'set',
    its fields to change, and 'areas'."""
    sp = spec['spawner']
    name = f'{AUTO_PARAMS}/{spec["actor"]}.game__scene__AutoPlacementMgrParam.bgyml'
    like = f'{AUTO_PARAMS}/{sp["like"]}.game__scene__AutoPlacementMgrParam.bgyml'
    if like not in fs.files:
        raise ValueError(f'the game has no {like}')
    root = {'$parent': (T_STR, f'Work/{like[:-len(".bgyml")]}.gyml'),
            'ActorName': (T_STR, f'Work/Actor/{spec["actor"]}.engine__actor__ActorParam.gyml')}
    for k, v in sp['set'].items():
        root[k] = fl(v) if isinstance(v, float) else (randomizer.T_INT, v)
    ver = randomizer.byml_read(fs.raw(like))[0]
    out = {name: randomizer.byml_write(ver, root)}
    ref = (T_STR, f'Work/{name[:-len(".bgyml")]}.gyml')
    for area in sp['areas']:
        path = f'Banc/GameBancParam/{area}.game__banc__GameBancParam.bgyml'
        raw = files_in.get(path, fs.raw(path) if path in fs.files else None)
        if raw is None:
            raise ValueError(f'the game has no {path}')
        ver, scene = randomizer.byml_read(raw)
        lst = [x for x in scene.get('EnemyAutoPlacementParams', []) if x[1] != ref[1]]
        out[path] = randomizer.byml_write(ver, {**scene, 'EnemyAutoPlacementParams': lst + [ref]})
        log(f'  {area}: a spawner of {spec["actor"]} beside {", ".join(x[1].split("/")[-1].split(".")[0] for x in lst)}')
    return out


# ---- making the mod

def make_enemy(fs, botw, name, files, log, work=None):
    spec = dict(ENEMIES[name], name=name)
    base, actor = spec['base'], spec['actor']
    project = actor
    log(f'{name} ({actor}, behaviour of {base}):')
    src_f = bb.Bfres(bb.yaz0(botw_file(botw, f'Model/{spec["botw_model"]}.sbfres')))
    src_m = next(m for m in src_f.models if m.name == spec.get('botw_name', spec['botw_model']))
    base_bytes, d = fs.zs(f'Model/{base}.{base}.bfres.zs')
    base_f = bb.Bfres(base_bytes)
    base_m = base_f.models[0]
    skel = build_skeleton(base_m, src_m, spec)
    log(f'  skeleton: {len(src_m.bones)} bones of {spec["botw_model"]} and {len(skel.extra)} more for base bones; '
        f'{skel.size:.2f} times the base\'s size')
    shapes, bounds, drawn = build_shapes(base_f, base_m, botw, skel, spec, log)
    for s in base_m.shapes:
        g = shapes[s.name]
        log(f'  {s.name}: {g.count} vertices, {len(g.indices) // 3} triangles')
    new = bb.Rec()
    new.skel_flags, new.bones, new.smooth, new.rigid = skel.skel_flags, skel.bones, skel.smooth, skel.rigid
    new.shapes = shapes
    new.textures = build_textures(fs, botw, spec, base_m, drawn, project, files, log)
    new.file_name = f'{project}.{base}'
    model = bb.rebuild_model(base_bytes, new)
    if work:
        Path(work).mkdir(parents=True, exist_ok=True)
        (Path(work) / f'{project}.{base}.bfres').write_bytes(model)
    files[f'Model/{project}.{base}.bfres.zs'] = fs.compress(model, d)
    speeds = build_animations(fs, botw, spec, skel, project, files, log)
    if speeds:
        log('  speeds: ' + ', '.join(f'{k} {v:.2f}' for k, v in speeds.items()))
    path, data = enemy_pack(fs, spec, project, skel, speeds, log)
    files[path] = data
    heap = max(1.0, len(skel.bones) / len(base_m.bones)) * 1.5
    files.update(rsdb_rows(fs, spec, project, bounds, heap, files))
    tag_files, tags = tag_row(fs, spec, files)
    files.update(tag_files)
    log(f'  tags: {", ".join(tags) or "none"}')
    files.update(placements(fs, spec, files, log))
    if spec.get('spawner'):
        files.update(spawner(fs, spec, files, log))
    if spec.get('sounds'):
        files.update(build_sounds(fs, botw, spec, files, log))


def make(botw, game, out=None, log=print, work=None, enemies=None):
    fs = n64voice.RomFS(Path(game) / 'romfs')
    mod = Path(out) if out else Path(game) / 'mods' / MOD_NAME
    enemies = list(enemies or ENEMIES)
    files = {}
    for name in enemies:
        make_enemy(fs, botw, name, files, log, work)
    for path, data in files.items():
        dest = mod / 'romfs' / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    lines = ''.join(textwrap.fill(f'- {n} (actor {ENEMIES[n]["actor"]}, behaviour of {ENEMIES[n]["base"]}): '
                                  f'{ENEMIES[n]["about"]}', 118, subsequent_indent='  ') + '\n' for n in enemies)
    (mod / 'README.txt').write_text(README.format(enemies=lines), encoding='utf-8')
    log(f'{mod}: {len(files)} files')
    return mod


# ---- checks and preview

def mod_file(fs, mod, rel, like=None):
    _, d = fs.zs(like or rel)
    return fs.zstd.decompress((Path(mod) / 'romfs' / rel).read_bytes(), zstd_dict=d)


def check(game, mod, enemies=None, log=print):
    """Read back the mod's files and compare them with the game's."""
    fs = n64voice.RomFS(Path(game) / 'romfs')
    root = Path(mod) / 'romfs'
    ok = True

    def need(cond, what):
        nonlocal ok
        if not cond:
            log(f'  PROBLEM: {what}')
            ok = False
    for name in enemies or ENEMIES:
        spec = ENEMIES[name]
        base, actor = spec['base'], spec['actor']
        project = actor
        log(f'{name}:')
        # pack: every member another names is there or is a game member the base's pack did not change
        source = spec.get('pack', base)
        pack = mod_file(fs, mod, f'Pack/Actor/{actor}.pack.zs', f'Pack/Actor/{source}.pack.zs')
        got = {n: b for n, b, _ in darklink.sarc_members(pack)}
        want = {n: b for n, b, _ in darklink.sarc_members(fs.zs(f'Pack/Actor/{source}.pack.zs')[0])}
        param = got.get(f'Actor/{actor}.engine__actor__ActorParam.bgyml')
        need(param, 'no ActorParam')
        renamed = [n for n in want if n not in got]
        stale = []
        for n, b in got.items():
            if n.endswith('.bgyml'):
                strs = set()
                try:
                    walk_strings(randomizer.byml_read(b)[1], lambda s: strs.add(s) or s)
                except ValueError:
                    continue
                stale += [f'{n} -> {s}' for s in strs for old in renamed if s in ref_forms(old)]
        need(not stale, f'references to renamed members: {stale[:4]}')
        need(all(got[n] == want[n] for n in want if n in got and n.endswith('.ainb')), 'AI changed')
        _, ap = randomizer.byml_read(param)
        comp = ap['Components']
        _, info = randomizer.byml_read(got[comp['ModelInfoRef'][1][1:]])
        _, anim = randomizer.byml_read(got[comp['AnimationRef'][1][1:]])
        anim_project = anim['AnimationResources'][0]['ModelProjectName'][1]
        log(f'  pack: {len(got)} members, {len(renamed)} renamed; model {info["ModelProjectName"][1]}.'
            f'{info["FmdbName"][1]}, animations {anim_project}')
        need(info['ModelProjectName'][1] == project and anim_project == f'{project}_Animation', 'model names')
        # model
        model = mod_file(fs, mod, f'Model/{project}.{base}.bfres.zs', f'Model/{base}.{base}.bfres.zs')
        f = bb.Bfres(model)
        m = f.models[0]
        sections, ptrs = bb.relocations(model)
        need(all(bb.u64(model, p) < len(model) for p in ptrs), 'pointers out of the file')
        need(bb.dic_names(model, bb.u64(model, m.skel_at + 8)) == [b.name for b in m.bones], 'bone dictionary')
        nmat = len(m.smooth) + len(m.rigid)
        log(f'  model: {len(model)} bytes, {len(m.bones)} bones ({len(m.smooth)} smooth, {len(m.rigid)} rigid)')
        for s in m.shapes:
            v = m.vtx[s.vtx]
            vs = f.vertices(v)
            idx = f.indices(s.meshes[0])
            bad_i = sum(1 for q in vs.get('_i0', []) for k in range(max(v.skin, 1)) if q[k] >= nmat)
            wsum = [round(sum(q[:v.skin]) * 255) for q in vs.get('_w0', [])] if v.skin > 1 else []
            log(f'    {s.name}: {v.count} vertices, {len(idx) // 3} triangles, matrices out of range {bad_i}, '
                f'weights adding to 255 {sum(1 for w in wsum if w == 255)}/{len(wsum)}')
            need(max(idx) < v.count and not bad_i and all(w == 255 for w in wsum), f'{s.name} geometry')
            for me in s.meshes[1:]:
                need(f.indices(me) == idx, f'{s.name} level of detail')
        for mt in m.mats:
            for t in mt.textures:
                rel = f'Tex/{t}.bntx.zs'
                if 'BotW_' + name in t:
                    if not (root / rel).is_file():
                        need(False, f'texture {t} missing')
                        continue
                    b = fs.zstd.decompress((root / rel).read_bytes())
                    tt = bb.u64(b, bb.u64(b, 0x28))
                    fmt, (w, h), mips, th = bb.u32(b, tt + 0x1c), struct.unpack_from('<2I', b, tt + 0x24), \
                        bb.u16(b, tt + 0x16), b[tt + 0x34]
                    lv, total = tx.levels(w, h, fmt >> 8, mips, th)
                    good = bb.sname(b, bb.u64(b, tt + 0x60)) == t and total == bb.u32(b, tt + 0x50) \
                        and bb.u32(b, 0x1c) == len(b)
                    need(good, f'texture {t} inconsistent')
        # animations: every base animation, every bone in the skeleton
        abytes = mod_file(fs, mod, f'Model/{anim_project}.anim.bfres.zs', f'Model/{base}_Animation.anim.bfres.zs')
        anims = bb.read_anims(abytes)
        base_anims = bb.read_anims(fs.zs(f'Model/{base}_Animation.anim.bfres.zs')[0])
        names = {b.name for b in m.bones}
        need({a.name for a in anims} == {a.name for a in base_anims}, 'animation names')
        need(all(bo.name in names for a in anims for bo in a.bones), 'animated bones not in the skeleton')
        own = spec.get('own_length')
        need(all(a.frames == ba.frames or ba.flags & 4 or (own and re.search(own, ba.name))
                 for a, ba in zip(anims, base_anims)), 'animation lengths')
        need(bb.dic_names(abytes, bb.u64(abytes, 0x60)) == [a.name for a in anims], 'animation dictionary')
        log(f'  animations: {len(anims)} in {anim_project}, all bones in the skeleton')
        if f'AnimationEvent/Animation/{base}_Animation.anim.baev.zs' in fs.files:
            need((root / f'AnimationEvent/Animation/{anim_project}.anim.baev.zs').is_file(), 'animation events')
        # tables
        for table in ('ActorInfo', 'GameActorInfo'):
            rel = f'RSDB/{table}.Product.111.rstbl.byml.zs'
            _, rows = randomizer.byml_read(mod_file(fs, mod, rel))
            ids = [r['__RowId'][1] for r in rows]
            need(actor in ids and ids == sorted(ids, key=lambda s: s.encode()), f'{table} row')
        t = TagTable(mod_file(fs, mod, TAG_PATH))
        a, b = t.index(actor), t.index(base)
        need(a is not None and t.on[a] == t.on[b], 'tag row')
        log(f'  RSDB: rows in ActorInfo and GameActorInfo; tags {sorted(t.tags[x] for x in t.on[a])}')
        for folder, area in spec['areas']:
            rel = f'Banc/{folder}/{area}_Dynamic.bcett.byml.zs'
            _, r = randomizer.byml_read(mod_file(fs, mod, rel))
            mine = [x for x in r['Actors'] if x['Gyaml'][1] == actor]
            log(f'  {area}: {len(mine)} at ' + '; '.join(
                '(' + ', '.join(f'{f32(c):.1f}' for c in x['Translate']) + ')' for x in mine))
            need(len(mine) >= 1, f'{area} placements')
    log('check: ' + ('all consistent' if ok else 'PROBLEMS above'))
    return ok


def png(path, w, h, rgb):
    raw = b''.join(b'\0' + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def draw(views, path, size=384):
    """A PNG of meshes [(triangles of model-space points, colour)], one picture per view side by side, each from the
    front and the side, lit from the viewer, all at one scale."""
    pts = [p for meshes in views for tris, _ in meshes for t in tris for p in t]
    lo = [min(p[k] for p in pts) for k in range(3)]
    hi = [max(p[k] for p in pts) for k in range(3)]
    span = max(hi[1] - lo[1], hi[0] - lo[0], hi[2] - lo[2]) * 1.05
    cols = 2 * len(views)
    w, h = size * cols, size
    rgb = bytearray(b'\x20' * (w * h * 3))
    zb = [1e9] * (w * h)
    for vi, meshes in enumerate(views):
        for side in range(2):
            cell = vi * 2 + side
            for tris, col in meshes:
                for t in tris:
                    q = []
                    for p in t:
                        x, z = (p[0] - (lo[0] + hi[0]) / 2, p[2]) if side == 0 else (p[2] - (lo[2] + hi[2]) / 2, -p[0])
                        q.append((x / span * size + size / 2 + cell * size, (hi[1] - p[1]) / span * size + size * 0.025,
                                  -z))
                    e1 = [q[1][k] - q[0][k] for k in range(3)]
                    e2 = [q[2][k] - q[0][k] for k in range(3)]
                    nz = e1[0] * e2[1] - e1[1] * e2[0]
                    if abs(nz) < 1e-9:
                        continue
                    n = bb.normalize(bb.cross(e1, e2))
                    shade = 0.25 + 0.75 * abs(n[2])
                    xs, ys = [p[0] for p in q], [p[1] for p in q]
                    for y in range(max(int(min(ys)), 0), min(int(max(ys)) + 1, h)):
                        for x in range(max(int(min(xs)), cell * size), min(int(max(xs)) + 1, (cell + 1) * size)):
                            a = ((q[1][0] - x) * (q[2][1] - y) - (q[2][0] - x) * (q[1][1] - y)) / nz
                            b = ((q[2][0] - x) * (q[0][1] - y) - (q[0][0] - x) * (q[2][1] - y)) / nz
                            c = 1 - a - b
                            if a < 0 or b < 0 or c < 0:
                                continue
                            z = a * q[0][2] + b * q[1][2] + c * q[2][2]
                            if z < zb[y * w + x]:
                                zb[y * w + x] = z
                                rgb[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes(int(v * shade) for v in col)
    png(path, w, h, rgb)


COLOURS = [(200, 170, 140), (120, 150, 210), (210, 120, 120), (140, 200, 140), (210, 200, 110), (180, 130, 200),
           (120, 200, 200), (230, 150, 90), (160, 160, 160)]


def posed_meshes(f, m, anim=None, frame=0):
    """[(triangles, colour)] of a model, skinned in an animation's pose at a frame (None: the bind pose)."""
    euler = bool(m.skel_flags & 0x1000)
    p = bb.pose(anim, frame) if anim else {}
    world = []
    for bo in m.bones:
        sc, rt, tr = p.get(bo.name, ([None] * 3, [None] * 4, [None] * 3))
        sc = [x if x is not None else y for x, y in zip(sc, bo.scale)]
        rt = [x if x is not None else y for x, y in zip(rt, bo.rot)]
        tr = [x if x is not None else y for x, y in zip(tr, bo.trans)]
        local = bb.local_matrix(sc, rt, tr, euler)
        world.append(bb.mat_mul(world[bo.parent], local) if bo.parent >= 0 else local)
    palette = m.smooth + m.rigid
    skin = [bb.mat_mul(world[i], bb.mat_inv(m.bones[i].world)) for i in m.smooth] + [world[i] for i in m.rigid]
    out = []
    for k, s in enumerate(m.shapes):
        v = m.vtx[s.vtx]
        vs = f.vertices(v)
        pos = []
        for i, q in enumerate(vs['_p0']):
            if v.skin == 0:
                pos.append(bb.xform(world[s.bone], q))
                continue
            ii = vs['_i0'][i]
            ww = vs['_w0'][i] if v.skin > 1 else [1.0]
            acc = [0.0, 0.0, 0.0]
            for j in range(max(v.skin, 1)):
                if ww[j] <= 0:
                    continue
                mtx = skin[int(ii[j])]
                if v.skin == 1 and int(ii[j]) < len(m.smooth):
                    mtx = bb.mat_mul(world[palette[int(ii[j])]], bb.mat_inv(m.bones[palette[int(ii[j])]].world))
                acc = [a + ww[j] * b for a, b in zip(acc, bb.xform(mtx, q))]
            pos.append(acc)
        idx = f.indices(s.meshes[0])
        tris = [[pos[idx[i]], pos[idx[i + 1]], pos[idx[i + 2]]] for i in range(0, len(idx) - 2, 3)]
        out.append((tris, COLOURS[k % len(COLOURS)]))
    return out


# the base animations drawn, those of each base that it has
PREVIEW_ANIMS = ('Walk_Lp', 'Run_Lp', 'Attack_0', 'Die_B', 'Walk_F', 'Attack', 'Attack_Jump', 'Die', 'Disguise',
                 'GetUp', 'BasicAttack_St', 'Run', 'Appear', 'Disappear')


def preview(game, mod, out, enemies=None, log=print):
    """PNGs of each converted model: the bind pose, and frames of animations."""
    fs = n64voice.RomFS(Path(game) / 'romfs')
    Path(out).mkdir(parents=True, exist_ok=True)
    for name in enemies or ENEMIES:
        spec = ENEMIES[name]
        base, project = spec['base'], spec['actor']
        model = mod_file(fs, mod, f'Model/{project}.{base}.bfres.zs', f'Model/{base}.{base}.bfres.zs')
        f = bb.Bfres(model)
        m = f.models[0]
        anims = {a.name: a for a in bb.read_anims(mod_file(fs, mod, f'Model/{project}_Animation.anim.bfres.zs',
                                                           f'Model/{base}_Animation.anim.bfres.zs'))}
        p = Path(out) / f'{name}_bind.png'
        draw([posed_meshes(f, m)], p)
        log(str(p))
        for an in PREVIEW_ANIMS + tuple(spec.get('preview', ())):
            a = anims.get(an)
            if not a:
                continue
            frames = [0, a.frames // 3, 2 * a.frames // 3] if a.frames else [0]
            p = Path(out) / f'{name}_{an}.png'
            draw([posed_meshes(f, m, a, fr) for fr in frames], p, 256)
            log(f'{p} (frames {frames})')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--botw', required=True, type=Path,
                    help="Breath of the Wild's unpacked RomFS (Model/, Actor/, ...)")
    ap.add_argument('--game', required=True, type=Path, help='the game folder (its romfs folder is read)')
    ap.add_argument('--out', type=Path, help=f'the mod folder to write (default: <game>/mods/{MOD_NAME})')
    ap.add_argument('--only', help='comma-separated enemies to make (default: all of ' + ', '.join(ENEMIES) + ')')
    ap.add_argument('--work', type=Path, help='a folder for the decompressed converted models (for inspection)')
    ap.add_argument('--check', action='store_true', help='read back the written files and print a summary')
    ap.add_argument('--preview', type=Path, help='write PNG drawings of the converted models here')
    a = ap.parse_args()
    enemies = a.only.split(',') if a.only else list(ENEMIES)
    mod = make(a.botw, a.game, a.out, work=a.work, enemies=enemies)
    if a.check and not check(a.game, mod, enemies):
        sys.exit(1)
    if a.preview:
        preview(a.game, mod, a.preview, enemies)


if __name__ == '__main__':
    main()
