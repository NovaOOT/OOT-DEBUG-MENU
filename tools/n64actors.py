"""The N64 Graphics mod's enemies and other characters (N64_ACTORS): which N64 skeleton stands in for a remake model,
and how each of its limbs follows the remake's bones. tools/make_n64_mod.py writes the result as n64/actors.bin;
runtime/n64actors.c reads it.

A remake model is known at run time by its skeleton, the names of its bones hashed (bone_hash), and by its own name
hashed the same way, which tells apart models that share a skeleton (the red and blue Tektites). Each N64 limb that
draws something rides one remake bone rigidly. The fit puts both skeletons in one pose, the remake's bind pose and the
N64 skeleton on the first frame of an animation of its own, sharing the model's origin, the N64 one scaled to the
remake's size; each limb takes the bone whose joints lie nearest the middle of its vertices. A limb's matrix is then
that bone's bind matrix inverted times the limb's place in the shared pose, so at run time the bone's matrix times it
places the limb (runtime/n64actors.c n64actors_world_ready).

actors.bin, little-endian:

    header   'N64A', u32 version (3), u32 number of entries
    entry    u32 bone hash, u32 the model's name hash, u16 the remake skeleton's bone count, u16 N64 object number,
             u16 the skeleton's first node in that object's file, u16 limb count, then per limb (64 bytes) u16 bone
             (0xFFFF: the limb draws nothing), u16 the drawn limb it hangs on (its index in the entry; 0xFFFF none),
             12 f32 (the rows of a 3x4 matrix: N64 units in the limb's space to the bone's space) and 3 f32 (where
             its joint is in that limb's space in the bind pose: the runtime keeps it there, so the pieces stay joined
             however far the remake's bones part)

Standard library only.
"""
import math
import struct

import bfres_skel
import n64gfx
import n64obj

VERSION = 3
# remake model -> (N64 object number, skeleton index in the object, size against the extents' fit, {limb: the bone it
# rides} where the vote picks wrong[, texture set: make_n64_mod.py OBJECT_VARIANTS, 0 the usual[, fit's opts]]): each
# pair checked by eye against a picture of the N64 skeleton.
# the Stalchild's limbs on frame 3, turned to face the camera: legs 3-5 left, 6-8 right; arms 12-14 right, 15-17 left
STALCHILD = {1: 'Waist', 3: 'Leg_1_L', 4: 'Leg_2_L', 5: 'Ankle_L', 6: 'Leg_1_R', 7: 'Leg_2_R', 8: 'Ankle_R',
             9: 'Spine_2', 10: 'Head', 11: 'Head', 12: 'Arm_1_R', 13: 'Arm_2_R', 14: 'Wrist_R', 15: 'Arm_1_L',
             16: 'Arm_2_L', 17: 'Wrist_L', 18: 'Waist'}
SKULLWALLTULA = {4: 'Head', 3: 'Head', 22: ('Leg_A_1_L', 'Leg_A_5_L'), 7: ('Leg_B_1_L', 'Leg_B_5_L'),
                 13: ('Leg_C_1_L', 'Leg_C_5_L'), 10: ('Leg_D_1_L', 'Leg_D_5_L'), 25: ('Leg_A_1_R', 'Leg_A_5_R'),
                 19: ('Leg_B_1_R', 'Leg_B_5_R'), 16: ('Leg_C_1_R', 'Leg_C_5_R'), 28: ('Leg_D_1_R', 'Leg_D_5_R')}
CURSED = {4: 'Head', 3: 'Mouth', 22: ('Leg_A_1_L', 'Leg_A_3_L'), 7: ('Leg_B_1_L', 'Leg_B_3_L'),
          13: ('Leg_C_1_L', 'Leg_C_3_L'), 10: ('Leg_D_1_L', 'Leg_D_4_L'), 25: ('Leg_A_1_R', 'Leg_A_3_R'),
          19: ('Leg_B_1_R', 'Leg_B_4_R'), 16: ('Leg_C_1_R', 'Leg_C_3_R'), 28: ('Leg_D_1_R', 'Leg_D_4_R')}
# the ReDead's and the Gibdo's limbs on frame 3 (standing): legs 2-5 left, 7-10 right, arms 15-17 left, 19-21 right
REDEAD = {24: 'Waist', 2: 'Leg_1_L', 3: 'Leg_2_L', 5: 'Ankle_L', 7: 'Leg_1_R', 8: 'Leg_2_R', 10: 'Ankle_R',
          13: 'Spine_2', 15: 'Arm_1_L', 16: 'Arm_2_L', 17: 'Wrist_L', 19: 'Arm_1_R', 20: 'Arm_2_R', 21: 'Wrist_R',
          23: 'Neck'}
POE_SISTER = {8: 'Spine_1', 9: 'Head', 10: 'Spine_1', 1: 'Arm_1_L', 2: 'Wrist_L', 3: 'Arm_1_R', 4: 'Arm_2_R', 6: 'Wrist_R',
              7: 'Wrist_R'}
# Bongo Bongo's hand: the body on the wrist, from the thumb's side the thumb (A) and the fingers B to E, each finger's
# pieces from its base
BONGO_HAND = {1: 'Wrist', 22: 'Finger_A_1', 24: 'Finger_A_2', 25: 'Finger_A_2', 12: 'Finger_B_1', 14: 'Finger_B_2',
              15: 'Finger_B_3', 17: 'Finger_C_1', 19: 'Finger_C_2', 20: 'Finger_C_3', 7: 'Finger_D_1', 9: 'Finger_D_2',
              10: 'Finger_D_3', 4: 'Finger_E_1', 5: 'Finger_E_2'}
BUBBLE = {14: 'Head', 3: 'Jaw', 8: 'Wing_1_L', 7: 'Wing_2_L', 13: 'Wing_1_R', 12: 'Wing_2_R'}
POE_FIELD = {0: 'Head', 7: 'Spine_1', 8: 'Waist', 1: 'Arm_1_L', 2: 'Wrist_L', 3: 'Arm_1_R', 4: 'Arm_3_R', 5: 'Wrist_R',
             6: 'Weapon_R'}
IRON_KNUCKLE = {1: 'Spine_1', 2: 'Leg_1_R', 3: 'Leg_2_R', 4: 'Ankle_R', 5: 'Leg_1_L', 6: 'Leg_2_L', 7: 'Ankle_L',
                11: 'Head', 12: 'Head', 13: 'Arm_1_L', 14: 'Arm_2_L', 16: 'Wrist_L', 17: 'Wrist_L', 18: 'Arm_1_R',
                19: 'Arm_2_R', 20: 'Wrist_R', 21: 'Clavicle_R', 22: 'Clavicle_R', 23: 'Clavicle_L', 24: 'Clavicle_L',
                25: 'Spine_2', 26: 'Spine_2', 27: 'Spine_2', 28: 'Spine_1'}
FLARE_DANCER = {2: 'Leg_1_R', 4: 'Leg_2_R', 5: 'Leg_2_R', 6: 'Leg_1_L', 8: 'Leg_2_L', 9: 'Leg_2_L', 11: 'Spine_2',
                12: 'Core', 13: 'Arm_1_R', 14: 'Arm_2_R', 15: 'Wrist_R', 16: 'Arm_1_L', 17: 'Arm_2_L', 18: 'Wrist_L',
                19: 'Spine_2', 20: 'Head', 21: 'Head', 22: 'Hair_1', 23: 'Hair_1', 24: 'Hair_1', 25: 'Hair_1'}
POE_COMPOSER = {9: 'Head', 0: 'Spine_1', 10: 'Waist', 5: 'Arm_1_R', 6: 'Arm_2_R', 7: 'Wrist_R', 8: 'Weapon_R',
                1: 'Arm_1_L', 2: 'Arm_2_L', 3: 'Wrist_L', 4: 'Weapon_L'}
TABLE = {
    # the legs go round the body in the same order: front to back D, C, B, A
    'Enemy_Skulltula': (36, 0, 1.0, {7: 'Leg_B_2_L', 19: 'Leg_B_2_R'}),
    # the tail (limb 32, one rigid piece) on the remake's first tail bone, the hip armour (28, 29) on the hips
    'Enemy_Lizalfos': (27, 0, 1.0, {32: ('Tail_1', 'Tail_4'), 28: 'Waist', 29: 'Waist'}),
    'Enemy_Dinolfos': (27, 1, 1.0, {32: ('Tail_1', 'Tail_4'), 28: 'Waist', 29: 'Waist'}),
    'Enemy_Stalfos': (50, 0, 1.0, {}),
    'Enemy_Moblin': (48, 0, 1.0, {}),
    'Enemy_Tektite_Red': (22, 0, 1.0, {}),
    'Enemy_Tektite_Blue': (22, 0, 1.0, {}, 1),
    'Enemy_Dodongo': (12, 0, 1.0, {}),
    'Enemy_Guay': (8, 0, 1.0, {}),
    # the Iron Knuckle (object 155 is Ganondorf's): legs 2-4 right and 5-7 left, the axe arm 13-17 left, 18-20
    # right, the shoulder plates on the clavicles, posed on its sixth animation's first frame (standing)
    'Enemy_IronKnuckle_Silver': (262, 5, 1.0, IRON_KNUCKLE, 0, {'rerig': True, 'frame': 6}),
    'Enemy_MiddleBoss_IronKnuckle_Nabooru': (262, 5, 1.0, IRON_KNUCKLE, 1, {'rerig': True, 'frame': 6}),
    'Animal_Dog': (363, 0, 1.0, {}),
    'Enemy_Anubis': (214, 0, 1.0, {}),
    # Built too unlike the remake for the plain fit, so bent onto its bind pose (fit, opts): every main limb named.
    # The Club Moblin's and the Stalchild's N64 frames face away from the camera's side; the Stalchild's feet then
    # point back, and its skull leans from frame 3, the most upright.
    'Enemy_Wolfos': (387, 0, 1.0, {5: ('Tail_1', 'Tail_4')}, 0, {'rerig': True}),
    'Enemy_Moblin_Club': (48, 1, 1.0, {}, 0, {'rerig': True, 'turn': (0, 180, 0)}),
    'Enemy_Stalchild': (388, 0, 1.0, STALCHILD, 0, {'rerig': True, 'frame': 3, 'turn': (0, 180, 0),
                                                    'bend': {5: (0, 180, 0), 8: (0, 180, 0)}}),
    'Enemy_Stalchild_Big': (388, 0, 1.0, STALCHILD, 0, {'rerig': True, 'frame': 3, 'turn': (0, 180, 0),
                                                        'bend': {5: (0, 180, 0), 8: (0, 180, 0)}}),
    'Npc_Gerudo_GEU_Ganondorf_Adult': (225, 0, 1.0, {
        1: 'Spine_1', 2: 'Clavicle_L', 3: 'Arm_1_L', 4: 'Arm_2_L', 5: 'Wrist_L', 6: 'Clavicle_R', 7: 'Arm_1_R',
        8: 'Arm_2_R', 9: 'Wrist_R', 10: 'Neck', 11: 'Neck', 12: 'Neck', 13: 'Neck', 14: 'Neck', 15: 'Neck', 16: 'Neck',
        17: 'Neck', 18: 'Waist', 19: 'Leg_1_L', 20: 'Leg_2_L', 21: 'Ankle_L', 22: 'Leg_1_R', 23: 'Leg_2_R',
        24: 'Ankle_R'}, 0, {'rerig': True}),
    # the Poe on frame 3 (hood upright, the lantern in its right hand); its hood and cloak keep their N64 stand
    # the left arm is one straight limb from the shoulder to the hand, so the hand rides its bone too: on the wrist it
    # leaves the arm whenever the remake's elbow bends
    'Enemy_Poe': (9, 0, 1.0, {3: 'Spine_1', 4: 'Head', 10: 'Head', 9: 'Arm_1_L', 8: 'Arm_1_L', 19: 'Arm_1_R',
                              18: 'Arm_3_R', 15: 'Wrist_R', 17: 'Wrist_R'}, 0, {'rerig': True, 'frame': 3, 'still': (3, 4)}),
    # the hand's three fingers on the remake's outer and inner fingers, the thumb on its thumb (A)
    'Enemy_Floormaster': (11, 0, 1.0, {1: 'Spine_1', 18: 'Neck', 3: 'Finger_B_1', 5: 'Finger_B_2', 6: 'Finger_B_3',
                                       13: 'Finger_D_1', 15: 'Finger_D_2', 16: 'Finger_D_3', 8: 'Finger_E_1',
                                       10: 'Finger_E_2', 11: 'Finger_E_3', 20: 'Finger_A_1', 22: 'Finger_A_2',
                                       23: 'Finger_A_3'}, 0, {'rerig': True}),
    'Enemy_Octorok': (7, 0, 1.0, {4: 'Head1', 7: 'Mouth_1', 36: 'Spine_1', 14: 'Leg_Front_1_L', 13: 'Leg_Front_2_L',
                                  12: 'Leg_Front_3_L', 35: 'Leg_Back_1_L', 34: 'Leg_Back_2_L', 33: 'Leg_Back_3_L',
                                  28: 'Leg_Front_1_R', 27: 'Leg_Front_2_R', 26: 'Leg_Front_3_R', 21: 'Leg_Back_1_R',
                                  20: 'Leg_Back_2_R', 19: 'Leg_Back_3_R'}, 0, {'rerig': True, 'still': (4, 36)}),
    'Enemy_ScrubDeku': (74, 0, 1.0, {3: 'Head', 6: 'Mouth_1', 9: 'Head', 12: 'TopHead_1', 13: 'Spine_1',
                                     18: 'Leg_1_L', 17: 'Ankle_L', 23: 'Leg_1_R', 22: 'Ankle_R'},
                        0, {'rerig': True, 'still': (3, 13)}),
    # the Peahat keeps its N64 build: the body on the spine, the leaves on the mouth, the six blades on the shaft
    'Enemy_Peahat': (24, 0, 1.0, {2: 'Spine_1', 22: 'Mouth', 6: 'Shaft', 9: 'Shaft', 12: 'Shaft', 15: 'Shaft',
                                  18: 'Shaft', 21: 'Shaft'}),
    # the Deku Baba's N64 skeleton is its head alone, the mouth along z: turned to open upward, on top of a stem of its
    # three stem models (each stretched over a third of the remake's stem bones), its ground leaves at the root
    'Enemy_Dekubaba': (57, 0, 1.0, {3: 'Jaw_A', 6: 'Jaw_B'}, 0, {
        'rerig': True, 'turn': (-90, 0, 0), 'scale': 0.0002, 'models': (
            {'dl': 0x1828, 'bone': 'Spine_1', 'to': 'Spine_4'}, {'dl': 0x1628, 'bone': 'Spine_4', 'to': 'Spine_7'},
            {'dl': 0x1330, 'bone': 'Spine_7', 'to': 'Head'}, {'dl': 0x10F0, 'bone': 'Waist', 'turn': (0, 0, 0)})}),
    # the Keese has no N64 skeleton: its head, body, eyes and feet (head toward -x, back up) stand upright from the neck
    # as the remake binds it, each wing's three
    # panels (two drawings of each, one per side, flat in x and y) hang from its shoulder
    'Enemy_Keese': (13, None, 1.0, {}, 0, {'scale': 0.00016, 'turn': (-90, 0, -90), 'models': (
        {'dl': 0xD20, 'bone': 'Neck'}, {'dl': 0xF40, 'bone': 'Neck'}, {'dl': 0x1678, 'bone': 'Neck'},
        {'dl': 0x1038, 'bone': 'Waist'}, {'dl': 0x1100, 'bone': 'Waist'},
        {'dl': 0x11C8, 'bone': 'Arm_1_L', 'turn': (0, 0, 0)}, {'dl': 0x1290, 'bone': 'Arm_1_L', 'turn': (0, 0, 0)},
        {'dl': 0x1358, 'bone': 'Arm_1_L', 'turn': (0, 0, 0)}, {'dl': 0x15B0, 'bone': 'Arm_1_R', 'turn': (0, 180, 0)},
        {'dl': 0x14E8, 'bone': 'Arm_1_R', 'turn': (0, 180, 0)}, {'dl': 0x1420, 'bone': 'Arm_1_R', 'turn': (0, 180, 0)})}),
    # the Leever has no N64 skeleton either: its body (mouth toward +x) turned to open upward at the neck, its lower
    # cone pointing down from there, four of its five petals each stretched along a remake petal
    'Enemy_Leever': (23, None, 1.0, {}, 0, {'scale': 0.0007, 'models': (
        {'dl': 0x580, 'bone': 'Neck', 'turn': (0, 0, 90)}, {'dl': 0x1F8, 'bone': 'Neck', 'turn': (0, 0, -90)},
        {'dl': 0x758, 'bone': 'Petal_1_Front', 'to': 'Petal_2_Front'},
        {'dl': 0x808, 'bone': 'Petal_1_L', 'to': 'Petal_2_L'},
        {'dl': 0x960, 'bone': 'Petal_1_Back', 'to': 'Petal_2_Back'},
        {'dl': 0xAC0, 'bone': 'Petal_1_R', 'to': 'Petal_2_R'})}),
    # the Gold Skulltula's N64 model (its object 36 also serves the big Skulltula) clings to a wall (its legs in x and y, its back toward +z): laid flat as the remake
    # binds both kinds, each one-piece leg along its remake leg, front (A) to back (D)
    'Enemy_Skulltula_Gold': (36, 0, 1.0, SKULLWALLTULA, 0, {'turn': (-90, 0, 0)}),
    'Enemy_Skullwalltula': (36, 0, 1.0, SKULLWALLTULA, 0, {'turn': (-90, 0, 0)}),
    # the people the curse turned into Skulltulas: the same build with a man's face (its eyes: make_n64_mod.py
    # OBJECT_VARIANTS), bound hanging as the N64 frame stands
    'Npc_Hylia_MSP_001': (350, 0, 1.0, CURSED, 0, {}),
    'Npc_Hylia_MSP_002': (350, 0, 1.0, CURSED, 0, {}),
    # the Armos statue keeps its N64 build, at its size in the game (the remake's few bones span only its middle)
    'Enemy_Amos': (56, 0, 1.0, {3: 'Upper', 6: 'Hand_L', 9: 'Hand_R', 12: 'Base'}, 0, {'scale': 0.00033}),
    # the Shell Blade's halves on the remake's jaws, hinged at the back as there, at the shell's size
    'Enemy_ShellBlade': (181, 0, 1.0, {3: 'Mouth_Bottom', 7: 'Mouth_Bottom', 5: 'Mouth_Top', 6: 'Mouth_Top'}, 0,
                         {'scale': 0.00015}),
    # bosses. Gohma on frame 3 (standing): its front legs on the remake's raised arms, its hind legs on the legs, its
    # abdomen along the tail; its body keeps the N64 stand
    'Enemy_DungeonBoss_Gohma': (28, 0, 1.0, {
        2: 'Spine_1', 3: 'Spine_1', 4: 'Spine_1', 84: 'Cover', 32: 'Eyeball', 35: 'Eyeball', 38: 'Eyeball', 41: 'Chin',
        46: 'Tooth_1_L', 45: 'Tooth_2_L', 51: 'Tooth_1_R', 50: 'Tooth_2_R', 13: 'Tail_1', 12: 'Tail_2', 11: 'Tail_3',
        10: 'Tail_4', 29: 'Clavicle_R', 22: 'Leg_1_R', 21: 'Leg_2_R', 20: 'Ankle_R', 28: 'Armor_A_R', 25: 'Armor_B_R',
        81: 'Clavicle_L', 74: 'Leg_1_L', 73: 'Leg_2_L', 72: 'Ankle_L', 80: 'Armor_A_L', 77: 'Armor_B_L',
        58: 'Arm_1_L', 57: 'Arm_2_L', 56: 'Wrist_L', 65: 'Arm_1_R', 64: 'Arm_2_R', 63: 'Wrist_R'},
        0, {'rerig': True, 'frame': 3, 'still': (2,)}),
    # King Dodongo's N64 frame stands him up on his hind legs: bent down onto all fours as the remake binds him
    'Enemy_DungeonBoss_Dodongo': (25, 0, 1.0, {
        5: 'Head', 6: 'Head', 47: 'Spine_3', 32: 'Spine_2', 31: 'Spine_1', 16: 'Tail_1', 15: 'Tail_2', 14: 'Tail_3',
        23: 'Leg_1_L', 22: 'Leg_2_L', 21: 'Ankle_L', 30: 'Leg_1_R', 29: 'Leg_2_R', 28: 'Ankle_R', 46: 'Arm_1_L',
        45: 'Arm_2_L', 44: 'Wrist_L', 39: 'Arm_1_R', 38: 'Arm_2_R', 37: 'Wrist_R'}, 0, {'rerig': True}),
    # Phantom Ganon kneels on the N64 frame: stood up, his trident in his right hand
    'Npc_DungeonBoss_PhantomGanon': (55, 0, 1.0, {
        13: 'Spine_2', 16: 'Waist', 14: 'Head', 4: 'Arm_1_L', 5: 'Arm_2_L', 6: 'Wrist_L', 8: 'Arm_1_R', 9: 'Arm_2_R',
        10: 'Wrist_R', 12: 'Weapon_R', 18: 'Leg_1_L', 19: 'Leg_2_L', 20: 'Ankle_L', 22: 'Leg_1_R', 23: 'Leg_2_R',
        24: 'Ankle_R'}, 0, {'rerig': True}),
    # Dead Hand's neck reaches forward on the N64 frame: raised up the remake's neck, the head turned back to face out
    'Enemy_DeadHand': (166, 1, 1.0, {
        14: 'Waist', 1: 'Spine_1', 2: 'Spine_2', 3: 'Spine_3', 4: 'Arm_1_R', 5: 'Arm_2_R', 6: 'Wrist_R', 7: 'Arm_1_L',
        8: 'Arm_2_L', 9: 'Wrist_L', 10: 'Neck_1', 11: 'Neck_3', 12: 'Head', 13: 'Head'},
        0, {'rerig': True, 'bend': {12: (90, 0, 0)}}),
    # Kotake (the ice sister, her blue gem); the remake draws Koume with the same model. Her size is set: the
    # remake's broom bones reach far past her
    'Enemy_DungeonBoss_Kotake': (193, 0, 1.0, {
        0: 'Waist', 2: 'Leg_1_L', 3: 'Ankle_L', 5: 'Leg_1_R', 6: 'Ankle_R', 7: 'Spine_2', 8: 'Arm_1_L', 9: 'Arm_1_L',
        10: 'Arm_1_L', 11: 'Arm_2_L', 12: 'Wrist_L', 13: 'Weapon_L', 14: 'Arm_1_R', 15: 'Arm_1_R', 16: 'Arm_1_R',
        17: 'Arm_2_R', 18: 'Wrist_R', 19: 'Neck', 20: 'Head', 21: 'Head', 22: 'Head', 23: 'Head', 24: 'Head',
        25: 'Head'}, 0, {'rerig': True, 'scale': 0.0005}),
    # Twinrova (object 211, skeleton 2) is left out: the fit's vote scatters her hair over the body, so she needs a
    # limb map of her own first
    # Big Octo (Jabu-Jabu's Belly loads object 351): one body; limbs by the fit's vote, not yet seen in game
    'Enemy_MiddleBoss_BigOcto': (351, 0, 1.0, {}),
    # the ReDead (skeleton 1) and the Gibdo (skeleton 0) crouch on the N64 frame: stood up from frame 3, the head
    # on the remake's neck
    'Enemy_ReDead': (152, 1, 1.0, REDEAD, 0, {'rerig': True, 'frame': 3}),
    'Enemy_Gibdo': (152, 0, 1.0, REDEAD, 0, {'rerig': True, 'frame': 3}),
    # Darunia: his head and its joint (limb 15) both on the head, else the head turns to face the joint behind it
    'Npc_Goron_GOU_Darunia': (157, 0, 1.0, {
        1: 'Waist', 2: 'Leg_1_L', 3: 'Leg_2_L', 4: 'Ankle_L', 5: 'Leg_1_R', 6: 'Leg_2_R', 7: 'Ankle_R', 8: 'Spine_1',
        9: 'Arm_1_L', 10: 'Arm_2_L', 11: 'Wrist_L', 12: 'Arm_1_R', 13: 'Arm_2_R', 14: 'Wrist_R', 15: 'Head',
        16: 'Head'}, 0, {'rerig': True}),
    # Darunia's son: the N64 goron Link's build
    'Npc_Goron_GOU_DaruniaJr': (207, 0, 1.0, {
        1: 'Waist', 3: 'Leg_1_L', 4: 'Leg_2_L', 5: 'Ankle_L', 6: 'Leg_1_R', 7: 'Leg_2_R', 8: 'Ankle_R', 9: 'Spine_2',
        10: 'Arm_1_L', 11: 'Arm_2_L', 12: 'Wrist_L', 13: 'Arm_1_R', 14: 'Arm_2_R', 15: 'Wrist_R', 16: 'Head'},
        0, {'rerig': True}),
    # Kaepora Gaebora flies on the N64 frame: his wings along the remake's arms, the crests on his head
    'Npc_U_Kaepora': (305, 0, 1.0, {
        0: 'Spine_1', 1: 'Neck_1', 2: 'Head', 3: 'Head', 4: 'Head', 5: 'Arm_1_L', 6: 'Arm_2_L', 7: 'Wrist_L',
        8: 'Wing_J_1_L', 9: 'Arm_1_R', 10: 'Arm_2_R', 11: 'Wrist_R', 12: 'Wing_J_1_R', 13: 'Leg_1_R', 14: 'Leg_2_R',
        15: 'Ankle_R', 16: 'Leg_1_L', 17: 'Leg_2_L', 18: 'Ankle_L', 19: 'Tail_1'}, 0, {'rerig': True}),
    # a Hyrule soldier (the Soldier and ImperialSoldier actors): the castle guard with his spear; the remake's soldier
    # has one Leg bone under his tabard, so both N64 legs ride it
    'Npc_Hylia_MS_001': (151, 0, 1.0, {1: 'Spine_1', 2: 'Leg', 3: 'Leg', 4: 'Leg', 5: 'Leg', 6: 'Leg', 7: 'Leg',
                                       8: 'Spine_2', 9: 'Arm_1_L', 10: 'Arm_2_L', 11: 'Wrist_L', 12: 'Arm_1_R',
                                       13: 'Arm_2_R', 14: 'Wrist_R', 15: 'Head'}, 0, {'rerig': True}),
    # Pierre the scarecrow on his pole
    'Npc_U_Pierre': (340, 0, 1.0, {
        0: 'Global_Root', 23: 'Spine_1', 24: 'Skirt', 25: 'Skirt', 26: 'Skirt', 2: 'Spine_2', 4: 'Head', 5: 'Head',
        6: 'Head', 7: 'Head', 8: 'Head', 9: 'Head', 10: 'Head', 11: 'Arm_1_R', 13: 'Arm_2_R', 14: 'Arm_2_R',
        15: 'Arm_2_R', 16: 'Arm_2_R', 17: 'Arm_1_L', 19: 'Arm_2_L', 20: 'Arm_2_L', 21: 'Arm_2_L', 22: 'Arm_2_L'},
        0, {'rerig': True}),
    # Rauru: his robe reaches the ground from the root; his size set by his head's height
    'Npc_Hylia_MU_Rauru': (167, 0, 1.0, {1: 'Spine_1', 2: 'Arm_1_L', 3: 'Arm_2_L', 4: 'Wrist_L', 5: 'Arm_1_R',
                                         6: 'Arm_2_R', 7: 'Wrist_R', 8: 'Head', 9: 'Root'},
                           0, {'rerig': True, 'scale': 0.00028}),
    # the Poe sisters share one N64 model; each one's cloak colour is a texture set (make_n64_mod.py OBJECT_VARIANTS)
    'Enemy_MiddleBoss_Poe_Meg': (153, 0, 1.0, POE_SISTER, 0, {'frame': 2}),
    'Enemy_Poe_Joelle': (153, 0, 1.0, POE_SISTER, 1, {'frame': 2}),
    'Enemy_Poe_Beth': (153, 0, 1.0, POE_SISTER, 2, {'frame': 2}),
    'Enemy_Poe_Amy': (153, 0, 1.0, POE_SISTER, 3, {'frame': 2}),
    # Bongo Bongo's hands: the N64 object holds a left (skeleton 0, thumb toward -x) and a right one, limb for limb
    'Enemy_DungeonBoss_BongoBongo_LeftHand': (226, 0, 1.0, BONGO_HAND, 0, {'rerig': True}),
    'Enemy_DungeonBoss_BongoBongo_RightHand': (226, 1, 1.0, BONGO_HAND, 0, {'rerig': True}),
    # Barinade's body (skeleton 1): its core on the remake's core, the cap above, the small Bari round its base;
    # its jellyfish (skeleton 0)
    'Enemy_DungeonBoss_Barinade': (140, 1, 1.0, {
        19: 'Core_Center_2', 6: 'Core_Center_2', 61: 'Upper', 23: 'Ball_Under', 24: 'Ball_Under', 25: 'Ball_Under',
        28: 'Core_Center_3', 30: 'Core_Center_3', 34: 'Core_Center_3', 36: 'Core_Center_3', 40: 'Core_Center_3',
        42: 'Core_Center_3', 46: 'Core_Center_3', 48: 'Core_Center_3', 52: 'Core_Center_3', 54: 'Core_Center_3'},
        0, {'scale': 0.0024}),
    'Enemy_DungeonBoss_Barinade_Jellyfish': (140, 0, 1.0, {}),
    # the Bubble: the skull on the head, its jaw, each wing's two pieces
    'Enemy_Bubble': (93, 0, 1.0, BUBBLE),
    'Enemy_Bubble_Big': (93, 0, 1.0, BUBBLE),
    'Enemy_SkullKid': (266, 0, 1.0, {}),
    # the other scrubs: the Mad Scrub has the Deku Scrub's build; the Lost Woods stage's scrubs (370), their elder
    # (360, his arms on the remake's arm leaves) and the Business Scrub (369, its leaf crown on the top of its head)
    'Enemy_Scrub_Mad': (74, 0, 1.0, {3: 'Head', 6: 'Mouth_1', 9: 'Head', 12: 'TopHead_1', 13: 'Spine_1',
                                     18: 'Leg_1_L', 17: 'Ankle_L', 23: 'Leg_1_R', 22: 'Ankle_R'},
                        0, {'rerig': True, 'still': (3, 13)}),
    'Enemy_Scrub_ForestStage': (370, 0, 1.0, {0: 'Waist', 1: 'Spine_1', 2: 'Head', 3: 'Head', 4: 'TopHead_1',
                                              5: 'Head', 6: 'Mouth_1', 7: 'Spine_1', 8: 'Ankle_L', 9: 'Ankle_R'}),
    'Enemy_Scrub_ForestStage_Elder': (360, 0, 1.0, {
        0: 'TopHead_1', 7: 'Head', 8: 'Mouth_1', 15: 'Head', 16: 'Spine_1', 1: 'Leg_1_L', 2: 'Leg_2_L', 3: 'Ankle_L',
        4: 'Leg_1_R', 5: 'Leg_2_R', 6: 'Ankle_R', 9: 'Leaf_1_L', 10: 'Leaf_2_L', 11: 'Leaf_2_L', 12: 'Leaf_1_R',
        13: 'Leaf_2_R', 14: 'Leaf_2_R'}),
    'Enemy_Scrub_Business': (369, 0, 1.0, {0: 'Waist', 1: 'Spine_1', 2: 'TopHead', 3: 'TopHead', 4: 'TopHead',
                                           5: 'TopHead', 6: 'Mouth_1', 7: 'Wrist_R', 8: 'Wrist_L', 9: 'Spine_1',
                                           10: 'Ankle_L', 11: 'Ankle_R'}),
    # the Big Poe and Hyrule Field's Poes on the N64 field Poe, upright on frame 2, its lantern in its right hand
    'Enemy_Poe_Big': (109, 0, 1.0, POE_FIELD, 0, {'frame': 2}),
    'Enemy_Poe_Hyrule': (109, 0, 1.0, POE_FIELD, 0, {'frame': 2}),
    # the composer brothers, one N64 model: the hat on the head, the lantern in the right hand
    'Enemy_Poe_Sharp': (110, 0, 1.0, POE_COMPOSER, 0, {'rerig': True}),
    'Enemy_Poe_Flat': (110, 0, 1.0, POE_COMPOSER, 0, {'rerig': True}),
    # the Stinger: the body's front and back halves, the tail down the remake's tail, each fin's two pieces
    'Enemy_Stinger': (38, 0, 1.0, {11: 'Spine_1', 2: 'Waist', 3: 'Tail_1', 4: 'Tail_3', 5: 'Tail_5', 8: 'BackFin_1',
                                   9: 'BackFin_2', 13: 'Fin_1_L', 14: 'Fin_2_L', 16: 'Fin_1_R', 17: 'Fin_2_R'}),
    # the Like Like's N64 body is one piece: on the spine's base, its mouth's two halves on the head
    'Enemy_LikeLike': (139, 0, 1.0, {9: 'Spine_1', 4: 'Head', 7: 'Spine_5', 8: 'Head'}),
    'Enemy_Gohma_Larva': (30, 0, 1.0, {2: 'Spine_1', 5: 'Horn_1', 8: 'Eyeball', 15: 'Leg_1_L', 14: 'Leg_2_L',
                                       13: 'Ankle_L', 22: 'Leg_1_R', 21: 'Leg_2_R', 20: 'Ankle_R'}),
    # the small Floormasters a Floormaster splits into: the Floormaster's hand, smaller with the remake's model
    'Enemy_Floormaster_Little': (11, 0, 1.0, {1: 'Spine_1', 18: 'Neck', 3: 'Finger_B_1', 5: 'Finger_B_2',
                                              6: 'Finger_B_3', 13: 'Finger_D_1', 15: 'Finger_D_2', 16: 'Finger_D_3',
                                              8: 'Finger_E_1', 10: 'Finger_E_2', 11: 'Finger_E_3', 20: 'Finger_A_1',
                                              22: 'Finger_A_2', 23: 'Finger_A_3'}, 0, {'rerig': True}),
    'Enemy_Peahat_Larva': (24, 0, 1.0, {2: 'Spine_1', 22: 'Mouth', 6: 'Shaft', 9: 'Shaft', 12: 'Shaft', 15: 'Shaft',
                                        18: 'Shaft', 21: 'Shaft'}),
    # one of the arms that grow from the Dead Hand's floor: upper arm, forearm, hand
    'Enemy_DeadHand_Hand': (166, 0, 1.0, {0: 'Spine_1', 1: 'Spine_2', 2: 'Wrist'}),
    # Ganon, the beast: arms 2-7 left with the sword in 6, 8-13 right with 12, the head's parts (15-33) on the head,
    # legs 35-37 left and 38-40 right, the tail 41-45
    'Enemy_DungeonBoss_Ganon': (339, 0, 1.0, {
        1: 'Spine_2', 2: 'Clavicle_L', 3: 'Arm_1_L', 4: 'Arm_2_L', 5: 'Wrist_L', 6: 'Item_L', 7: 'Wrist_L',
        8: 'Clavicle_R', 9: 'Arm_1_R', 10: 'Arm_2_R', 11: 'Wrist_R', 12: 'Item_R', 13: 'Wrist_R', 14: 'Neck',
        **{k: 'Head' for k in range(15, 34)}, 34: 'Waist', 35: 'Leg_1_L', 36: 'Leg_2_L', 37: 'Ankle_L',
        38: 'Leg_1_R', 39: 'Leg_2_R', 40: 'Ankle_R', 41: 'Tail_1', 42: 'Tail_2', 43: 'Tail_3', 44: 'Tail_4',
        45: 'Tail_4'}, 0, {'rerig': True}),
    # Bongo Bongo's body, hanging head down as the remake's: the spine 1-3, neck 4-5, the eye and its lids (6-37) on the
    # head, arms 39-40 left and 42-43 right
    'Enemy_DungeonBoss_BongoBongo': (226, 2, 1.0, {
        1: 'Spine_1', 2: 'Spine_2', 3: 'Spine_3', 4: 'Neck_1', 5: 'Neck_2',
        **{k: 'Head' for k in range(6, 38) if k not in (7, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35)},
        39: 'Arm_1_L', 40: 'Wrist_L', 42: 'Arm_1_R', 43: 'Wrist_R'}),
    # the Biri and the Bari have no N64 skeleton: the bell and the body inside it stand on the base bone
    'Enemy_Biri': (33, None, 1.0, {}, 0, {'scale': 0.00028, 'models': (
        {'dl': 0x530, 'bone': 'Scale_base'}, {'dl': 0x448, 'bone': 'Scale_base'})}),
    'Enemy_Bari': (86, None, 1.0, {}, 0, {'scale': 0.00025, 'models': (
        {'dl': 0x27D8, 'bone': 'Scale_Base'}, {'dl': 0x2600, 'bone': 'Scale_Base'})}),
    # the flowers the Deku Scrubs hide in: the N64 scrub's ring of leaves
    'Enemy_ScrubDeku_Leaf': (74, None, 1.0, {}, 0, {'scale': 0.00014, 'models': ({'dl': 0x2298, 'bone': 'Leaf_Root'},)}),
    'Enemy_Scrub_Business_Leaf': (369, None, 1.0, {}, 0, {'scale': 0.00014, 'models': (
        {'dl': 0x2310, 'bone': 'Leaf_Root'},)}),
    # the Flare Dancer: its body of flames (the N64 one scrolls a fire texture through segments 8 and 9: here a fire's
    # colours), the core in the chest
    'Enemy_MiddleBoss_FlareDancer': (158, 0, 1.0, FLARE_DANCER, 0, {'rerig': True}),
    'Npc_Goron_GON_001': (201, 0, 1.0, {}),
    'Npc_Goron_GON_002': (201, 0, 1.0, {}),
    # Kokiri Forest: Saria, Mido, the boys and the girls (their eyes and colours: make_n64_mod.py OBJECT_VARIANTS)
    'Npc_Kokiri_WC_Saria': (188, 0, 1.0, {}),
    'Npc_Kokiri_MC_Mido': (251, 0, 1.0, {}),
    'Npc_Kokiri_MC_001': (196, 0, 1.0, {}),
    'Npc_Kokiri_MC_002': (196, 0, 1.0, {}),
    'Npc_Kokiri_MC_003': (196, 0, 1.0, {}),
    'Npc_Kokiri_MC_004': (196, 0, 1.0, {}),
    'Npc_Kokiri_WC_001': (195, 0, 1.0, {}),
    'Npc_Kokiri_WC_002': (195, 0, 1.0, {}),
    # Hyrule's people. Some N64 frames cross the arms (Ruto, Impa, the Gerudo): each arm's tip then lies on the far
    # side, so the arms are named by hand.
    'Npc_Hylia_WU_Zelda_Child': (29, 0, 1.0, {}),
    'Npc_Hylia_WU_Zelda_Adult': (401, 0, 1.0, {}),
    'Npc_Hylia_WC_Malon_Child': (224, 0, 1.0, {}),
    'Npc_Hylia_WN_Malon_Adult': (208, 0, 1.0, {}),
    'Npc_Hylia_MU_Talon': (136, 0, 1.0, {}),
    'Npc_Hylia_MU_Ingo_Adult': (192, 0, 1.0, {}),
    'Npc_Hylia_MU_Ingo_Child': (192, 0, 1.0, {}),
    'Npc_Hylia_MU_Dampe': (137, 0, 1.0, {}),
    'Npc_Hylia_WU_Impa': (135, 0, 1.0, {9: 'Arm_1_L', 10: 'Arm_2_L', 11: 'Wrist_L',
                                        12: 'Arm_1_R', 13: 'Arm_2_R', 14: 'Wrist_R'}),
    # Sheik's frame crosses her arms over her chest, so no limb tip lies out to her right: every limb is named
    'Npc_Hylia_WU_Sheik': (138, 0, 1.0, {1: 'Waist', 2: 'Leg_1_L', 3: 'Leg_2_L', 4: 'Ankle_L', 5: 'Leg_1_R', 6: 'Leg_2_R',
                                         7: 'Ankle_R', 8: 'Spine_2', 9: 'Arm_1_L', 10: 'Arm_2_L', 11: 'Wrist_L',
                                         12: 'Arm_1_R', 13: 'Arm_2_R', 14: 'Wrist_R', 15: 'Head'}),
    'Npc_Zora_ZN_001': (254, 0, 1.0, {}),
    'Npc_Zora_ZU_Ruto_Child': (163, 0, 1.2, {8: 'Arm_1_L', 9: 'Arm_2_L', 10: 'Wrist_L',
                                             11: 'Arm_1_R', 12: 'Arm_2_R', 13: 'Wrist_R'}),
    'Npc_Zora_ZU_Ruto_Adult': (202, 0, 1.0, {}),
    # the King's N64 hands hang from his body, not from arms: they stay on his chest
    'Npc_Zora_ZU_KingZora': (255, 0, 1.0, {8: 'Spine_2', 9: 'Spine_2'}),
    'Npc_Gerudo_GEU_Nabooru': (179, 0, 1.0, {}),
    'Npc_Gerudo_GEN_011': (361, 0, 1.0, {}),   # the veiled guard with two swords
    'Npc_Gerudo_GEN_001': (278, 0, 1.0, {8: 'Arm_1_L', 9: 'Arm_2_L', 10: 'Wrist_L', 11: 'Arm_1_R', 12: 'Arm_2_R', 13: 'Wrist_R'}),
    'Npc_Gerudo_GEN_002': (278, 0, 1.0, {8: 'Arm_1_L', 9: 'Arm_2_L', 10: 'Wrist_L', 11: 'Arm_1_R', 12: 'Arm_2_R', 13: 'Wrist_R'}),
    'Npc_Gerudo_GEN_003': (278, 0, 1.0, {8: 'Arm_1_L', 9: 'Arm_2_L', 10: 'Wrist_L', 11: 'Arm_1_R', 12: 'Arm_2_R', 13: 'Wrist_R'}),
    'Npc_Gerudo_GEN_004': (278, 0, 1.0, {8: 'Arm_1_L', 9: 'Arm_2_L', 10: 'Wrist_L', 11: 'Arm_1_R', 12: 'Arm_2_R', 13: 'Wrist_R'}),
    'Npc_Goron_GOU_Biggoron': (341, 0, 1.0, {0: 'Spine_2', 1: 'Arm_1_L', 2: 'Arm_2_L', 3: 'Wrist_L', 4: 'Arm_1_R',
                                             5: 'Arm_2_R', 6: 'Wrist_R', 7: 'Head', 8: 'Head'}),   # his upper body
    'Npc_Goron_GOU_Medigoron': (201, 0, 1.0, {}),   # the N64 game drew him as a big Goron
    'Npc_U_GreatFairy': (10, 0, 1.0, {}),
    'Npc_U_DekuTreeChild': (356, 0, 1.0, {}),
    'Npc_U_JabuJabu': (64, 0, 1.0, {}),
    # the cow's N64 body is one piece with its legs: on the spine, the head and jaw on theirs
    'Animal_Cow': (395, 0, 1.0, {0: 'Spine_2', 1: 'Head', 2: 'Chin', 3: 'Chin', 4: 'Lip_D'}),
    'Animal_Cucco': (19, 0, 1.0, {3: 'Waist', 5: 'Leg_2_L', 9: 'Leg_2_R', 7: 'Wing_Close_L', 11: 'Wing_Close_R',
                                  13: 'Spine', 14: 'Head'}),
    'Animal_Frog': (230, 0, 1.0, {}),
}
NO_BONE = 0xFFFF


def bone_hash(names):
    """FNV-1a, 32 bits, over each bone name and a zero byte (runtime/n64actors.c bone_hash)."""
    h = 0x811C9DC5
    for n in names:
        for c in n.encode('utf-8') + b'\0':
            h = ((h ^ c) * 0x01000193) & 0xFFFFFFFF
    return h


def rest_frame(d, limbs, which=0):
    """The first frame of the first animation (or the one numbered which, from 0) in the object d made for a skeleton of
    this many limbs: 3 + 3 * limbs s16 (the root's position, then each limb's rotation), or None. An animation header is s16 frames, u16 0, the
    segment 6 addresses of its values and of its indices, u16 the index below which an index reads a value per frame."""
    n = len(d)
    for o in range(0, n - 16, 4):
        frames, pad, vals, idx, limit = struct.unpack_from('>hHIIH', d, o)
        if not (1 <= frames <= 3000 and pad == 0 and vals >> 24 == 6 and idx >> 24 == 6):
            continue
        vals &= 0xFFFFFF
        idx &= 0xFFFFFF
        if vals & 1 or idx & 1 or idx + (limbs + 1) * 6 > n or vals >= n or limit > (n - vals) // 2:
            continue
        ix = struct.unpack_from('>%dH' % ((limbs + 1) * 3), d, idx)
        if any(vals + 2 * i + 2 > n for i in ix):
            continue
        if which:
            which -= 1
            continue
        return [struct.unpack_from('>h', d, vals + 2 * i)[0] for i in ix]
    return None


def _rot(x, y, z):
    """Row-major 3x3 of the N64 rotation z, then y, then x (Rz Ry Rx), angles in 1/65536 turns."""
    ax, ay, az = (v * math.pi / 32768 for v in (x, y, z))
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    return [[cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx]]


def _mul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _vec3(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def limb_places(skel, frame):
    """Each limb's rotation (row-major 3x3) and joint position in the model's space on that frame."""
    rots, pos = [], []
    for i, lb in enumerate(skel.limbs):
        r = _rot(*frame[3 + 3 * i:6 + 3 * i])
        if lb.parent < 0:
            rots.append(r)
            pos.append([float(frame[0]), float(frame[1]), float(frame[2])])
        else:
            rots.append(_mul3(rots[lb.parent], r))
            d = _vec3(rots[lb.parent], (lb.x, lb.y, lb.z))
            pos.append([pos[lb.parent][k] + d[k] for k in range(3)])
    return rots, pos


def _inverse(m):
    """The inverse of a 3x4 affine matrix (row-major, rotation and scale in the 3x3, translation in the last column)."""
    a = [row[:3] for row in m]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0]) +
           a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        raise ValueError('a bone has no inverse')
    inv = [[(a[(j + 1) % 3][(i + 1) % 3] * a[(j + 2) % 3][(i + 2) % 3] -
             a[(j + 1) % 3][(i + 2) % 3] * a[(j + 2) % 3][(i + 1) % 3]) / det for j in range(3)] for i in range(3)]
    t = [m[k][3] for k in range(3)]
    it = _vec3(inv, t)
    return [inv[i] + [-it[i]] for i in range(3)]


def _seg_dist(p, a, b):
    ab = [b[k] - a[k] for k in range(3)]
    ll = sum(x * x for x in ab)
    t = 0.0 if ll < 1e-12 else max(0.0, min(1.0, sum((p[k] - a[k]) * ab[k] for k in range(3)) / ll))
    q = [a[k] + t * ab[k] for k in range(3)]
    return math.dist(p, q), q


def _usable(name):
    """A bone a limb may ride: not an effect or physics locator, nor cloth the game swings on its own."""
    return not (name.startswith(('Eff_', 'Ph_', 'Cl_', 'Z_')) or 'Pred' in name)


def _t3(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def _rot_between(a, b):
    """The shortest rotation (row-major 3x3) taking direction a onto direction b."""
    la, lb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    a = [x / la for x in a]
    b = [x / lb for x in b]
    v = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    c = sum(a[k] * b[k] for k in range(3))
    if c < -0.9999:   # half a turn about any axis across a
        ax = [0.0, -a[2], a[1]] if abs(a[0]) < 0.9 else [-a[2], 0.0, a[0]]
        n = math.sqrt(sum(x * x for x in ax))
        ax = [x / n for x in ax]
        return [[2 * ax[i] * ax[j] - (1 if i == j else 0) for j in range(3)] for i in range(3)]
    k = 1 / (1 + c)
    vx = [[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]]
    vv = _mul3(vx, vx)
    return [[(1 if i == j else 0) + vx[i][j] + k * vv[i][j] for j in range(3)] for i in range(3)]


def _horn(pairs):
    """The rotation (row-major 3x3) that best turns each direction a onto its b, for (a, b) pairs: the quaternion of
    the largest eigenvalue of the 4x4 matrix the pairs' cross sums make (found by squaring the matrix, shifted to be
    positive, until one column dominates)."""
    (xx, xy, xz), (yx, yy, yz), (zx, zy, zz) = [[sum(a[i] * b[j] for a, b in pairs) for j in range(3)] for i in range(3)]
    n = [[xx + yy + zz, yz - zy, zx - xz, xy - yx],
         [yz - zy, xx - yy - zz, xy + yx, zx + xz],
         [zx - xz, xy + yx, yy - xx - zz, yz + zy],
         [xy - yx, zx + xz, yz + zy, zz - xx - yy]]
    c = max(sum(abs(x) for x in row) for row in n) + 1e-9
    m = [[n[i][j] + (c if i == j else 0.0) for j in range(4)] for i in range(4)]
    for _ in range(60):
        m = [[sum(m[i][k] * m[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
        top = max(abs(x) for row in m for x in row) or 1.0
        m = [[x / top for x in row] for row in m]
    q = max(([m[i][j] for i in range(4)] for j in range(4)), key=lambda col: sum(x * x for x in col))
    ln = math.sqrt(sum(x * x for x in q)) or 1.0
    w, x, y, z = (v / ln for v in q)
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]]


def _rerig(skel, pick, rots, pos, s, joints, host, bend=None, still=()):
    """Each drawn limb's place (3x4, remake units) when the N64 model is bent onto the remake's bind pose rather than
    only hung on it: the limbs in a row that ride one bone make a group; the group's first joint goes onto its bone's
    joint, and the group turns (and, toward a single next group, stretches along the way there) so that the next
    groups' first joints point at their bones' joints. A group with no group after it keeps its parent group's turn.
    bend: {first limb of a group: (x, y, z) degrees} turns that group further about its joint (a skull the N64 frame
    holds bowed). host (_hosts): a joint that only hangs a model on a leaf of its own counts as drawn, with that
    model's bone, so that the models hung down a bare chain of joints (a Poe's lantern arm) still make groups. still:
    the first limbs of groups that keep their parent group's turn rather than aim (a body whose limbs reach out in
    another pose than the remake's).
    Used when the N64 creature's build is too far from the remake's for the plain fit (fit, opts rerig)."""
    limbs = skel.limbs
    P = [[s * x for x in p] for p in pos]
    drawn = pick
    pick = dict(pick)
    for c in drawn:
        if host[c] != c:
            pick.setdefault(host[c], drawn[c])

    def drawn_parent(l):
        p = limbs[l].parent
        while p >= 0 and p not in pick:
            p = limbs[p].parent
        return p

    def first(l):   # the first limb of l's group
        g, p = l, drawn_parent(l)
        while p >= 0 and pick[p] == pick[l]:
            g, p = p, drawn_parent(p)
        return g

    group = {l: first(l) for l in pick}
    still = {group[l] for l in still if l in group}
    tall = max(j[1] for j in joints) - min(j[1] for j in joints)   # a bone shorter than a twentieth of it aims nothing
    turn = {}   # group -> 3x3 (turn, then the stretch)
    places = {}
    eye = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for g in sorted(set(group.values())):
        pg = drawn_parent(g)
        down = [d for d in pick if group[d] == d and d != g and drawn_parent(d) >= 0 and group[drawn_parent(d)] == g]
        pairs = []
        for d in down if g not in still else ():
            u = [P[d][k] - P[g][k] for k in range(3)]
            w = [joints[pick[d]][k] - joints[pick[g]][k] for k in range(3)]
            if sum(x * x for x in u) > 1e-10 and sum(x * x for x in w) > (tall / 20) ** 2:
                pairs.append((u, w))
        if len(pairs) == 1:
            u, w = pairs[0]
            r = _rot_between(u, w)
            lw, lu = math.sqrt(sum(x * x for x in w)), math.sqrt(sum(x * x for x in u))
            k = max(0.5, min(2.0, lw / lu))
            v = [x / lw for x in w]
            st = [[(1.0 if i == j else 0.0) + (k - 1) * v[i] * v[j] for j in range(3)] for i in range(3)]
            turn[g] = _mul3(st, r)
        elif pairs:
            turn[g] = _horn([([x / math.sqrt(sum(y * y for y in u)) for x in u],
                              [x / math.sqrt(sum(y * y for y in w)) for x in w]) for u, w in pairs])
        else:
            turn[g] = _rot_of(turn[group[pg]]) if pg >= 0 else eye
        if g in (bend or {}):
            turn[g] = _mul3(_rot(*(a * 65536 / 360 for a in bend[g])), turn[g])
        t = joints[pick[g]]
        for x in drawn:
            if group[x] != g:
                continue
            lin = _mul3(turn[g], [[s * v for v in row] for row in rots[x]])
            at = _vec3(turn[g], [P[x][k] - P[g][k] for k in range(3)])
            places[x] = [lin[r] + [t[r] + at[r]] for r in range(3)]
    return places


def _rot_of(m):
    """The turn in a 3x3 that may also stretch (its nearest rotation, by the polar method)."""
    r = [row[:] for row in m]
    for _ in range(20):
        it = _t3([row[:3] for row in _inverse([row + [0.0] for row in r])])
        r = [[(r[i][j] + it[i][j]) / 2 for j in range(3)] for i in range(3)]
    return r


def _hosts(skel, pos0):
    """Each limb's host: itself, or, for a leaf that draws and sits at (or within 3% of the model's size of) its
    parent's joint, the parent. N64 skeletons often hang a joint's model on such a leaf of its own."""
    limbs = skel.limbs
    span = max(max(p[k] for p in pos0) - min(p[k] for p in pos0) for k in range(3)) or 1.0
    host = list(range(len(limbs)))
    for i, lb in enumerate(limbs):
        if lb.parent >= 0 and lb.dl and not any(c.parent == i for c in limbs) and                 math.sqrt(lb.x * lb.x + lb.y * lb.y + lb.z * lb.z) < 0.03 * span:
            host[i] = lb.parent
    return host


# The remake's humanoid skeletons name their main bones alike: each run of them, root to tip, as N64 limbs map onto it.
# (The clavicles are left out: some remake ones run a third of the arm, and the N64 upper arm would land on them. The
# toes too: an N64 foot is one limb from the ankle.)
HUMAN_RUNS = {
    'leg_L': ('Leg_1_L', 'Leg_2_L', 'Ankle_L'), 'leg_R': ('Leg_1_R', 'Leg_2_R', 'Ankle_R'),
    'arm_L': ('Arm_1_L', 'Arm_2_L', 'Wrist_L'), 'arm_R': ('Arm_1_R', 'Arm_2_R', 'Wrist_R'),
    'spine': ('Spine_1', 'Spine_2', 'Spine_3'), 'head': ('Neck', 'Neck_1', 'Neck_2', 'Neck_3', 'Head'),
    'hips': ('Waist',),
}


def _human_match(bones, skel, pos0, unders, drawn):
    """{limb: bone} for a humanoid: None unless the remake skeleton has the HUMAN_RUNS bones. On the N64 side the legs
    end in the two lowest-drawing tips on either side (+x is the model's left, as on the remake), the head in the
    highest near the middle, the arms in the tips farthest out to either side among the rest. The path to where the legs part (the
    hips) takes the remake's hips, the path on to where the arms part (the chest) its spine, and the runs from there
    its legs, arms and head: each limb the bone that covers the same share of the run's length, a run's tip its
    last bone."""
    names = {b.name: i for i, b in enumerate(bones)}
    if not all(n in names for n in ('Leg_1_L', 'Leg_1_R', 'Arm_1_L', 'Arm_1_R', 'Head')):
        return None
    limbs = skel.limbs
    nl = len(limbs)
    kids = [[j for j, c in enumerate(limbs) if c.parent == i] for i in range(nl)]
    host = _hosts(skel, pos0)
    tips = [i for i in range(nl) if host[i] == i and not [c for c in kids[i] if host[c] == c] and unders[i]]
    mid = {i: [sum(q[k] for q in unders[i]) / len(unders[i]) for k in range(3)] for i in tips}

    def path(i):
        out = []
        while i >= 0:
            out.append(i)
            i = limbs[i].parent
        return out[::-1]

    def parting(a, b):   # the last limb both paths share
        pa, pb = path(a), path(b)
        k = 0
        while k < min(len(pa), len(pb)) and pa[k] == pb[k]:
            k += 1
        return pa[k - 1] if k else None

    low = sorted(tips, key=lambda i: mid[i][1])
    legs = {}
    for i in low:
        side = 'L' if mid[i][0] > 0 else 'R'
        legs.setdefault(side, i)
        if len(legs) == 2:
            break
    if len(legs) < 2:
        return None
    hips = parting(legs['L'], legs['R'])
    if hips is None:
        return None
    # the rest: what does not hang under either leg
    leg_tops = {path(legs[side])[len(path(hips))] for side in 'LR' if len(path(legs[side])) > len(path(hips))}
    rest = [i for i in tips if not leg_tops & set(path(i))]
    if not rest:
        return None
    head = max(rest, key=lambda i: mid[i][1] - abs(mid[i][0]))   # high, and near the middle
    arms = {}
    for side, sign in (('L', 1), ('R', -1)):
        out = [i for i in rest if i != head and sign * mid[i][0] > 0]
        if out:
            arms[side] = max(out, key=lambda i: sign * mid[i][0])
    if len(arms) < 2:
        return None
    chest = parting(arms['L'], arms['R'])
    base = parting(hips, chest) if hips is not None and chest is not None else None
    if base is None:
        return None

    pick = {}

    def along(run, bone_names):
        bc = [names[n] for n in bone_names if n in names]
        run = [l for l in run if host[l] == l]
        if not bc or not run:
            return
        joints = [[bones[b].world[k][3] for k in range(3)] for b in bc]

        def shares(pts):
            d = [0.0]
            for a, b in zip(pts, pts[1:]):
                d.append(d[-1] + math.dist(a, b))
            return [x / d[-1] if d[-1] > 1e-9 else 0.0 for x in d]
        lf, bf = shares([pos0[l] for l in run] + ([mid[run[-1]]] if run[-1] in mid else [])), shares(joints)
        for k, l in enumerate(run):
            m = (lf[k] + lf[k + 1]) / 2 if k + 1 < len(lf) else 1.0
            pick[l] = bc[-1] if k == len(run) - 1 else bc[max(i for i in range(len(bc)) if bf[i] <= m + 1e-9)]

    # the root down to where the legs part is the hips; on from where the upper body leaves them, the spine
    ph, pc, pb = path(hips), path(chest), path(base)
    along(ph, HUMAN_RUNS['hips'] if 'Waist' in names else ('Skl_Root',))
    along(pc[len(pb):] if base != chest else [chest], HUMAN_RUNS['spine'])
    for side in 'LR':
        along(path(legs[side])[len(ph):], HUMAN_RUNS['leg_' + side])
        along(path(arms[side])[len(pc):], HUMAN_RUNS['arm_' + side])
    along(path(head)[len(pc):], HUMAN_RUNS['head'])
    for l in range(nl):   # the models hung at a joint go with it; only limbs that draw keep a bone
        if host[l] != l and host[l] in pick:
            pick[l] = pick[host[l]]
    return {l: b for l, b in pick.items() if l in drawn}


def fit(bones, skel, mesh, frame, scale=1.0, rides=None, opts=None):
    """(scale in metres per N64 unit, [(bone or None, 3x4 matrix) per limb]) for a remake skeleton (bfres_skel bones)
    and an N64 skeleton with its mesh posed on frame; scale multiplies the size the extents give, rides names the bone
    of some limbs.

    The N64 frame and the remake's bind pose may stand differently (a remake character is often bound with its arms
    out, the N64 frame has them down), and a limb that rides a bone keeps the turn it had against it in the shared
    pose. So the N64 skeleton is first posed like the bind pose: a limb that runs to a child limb riding another bone
    is turned to run along the remake's bone between the two bones' joints; the others keep their turn against their
    parent. A humanoid's main limbs take their bones by name (_human_match); a limb they leave out takes the bone the most of
    its vertices lie nearest in the shared pose. The vote and that pose are made again in turn, a few times.

    opts (a TABLE entry's sixth item) for a creature whose N64 build is far from the remake's: 'turn' (x, y, z degrees)
    turns the whole N64 frame first (a wall Skulltula drawn facing its wall), 'scale' sets the metres per N64 unit
    outright (instead of the extents' ratio times scale), 'frame' picks another animation's first frame (rest_frame),
    and 'rerig' bends the model onto the bind pose (_rerig, with 'bend' and 'still' its turns by hand)."""
    opts = opts or {}
    limbs = skel.limbs
    rots0, pos0 = limb_places(skel, frame)
    if opts.get('turn'):
        tr = _rot(*(a * 65536 / 360 for a in opts['turn']))
        rots0 = [_mul3(tr, r) for r in rots0]
        pos0 = [_vec3(tr, p) for p in pos0]
    local = {}   # each limb's vertices in its own space
    for v in mesh.verts:
        if v[9] < len(limbs):
            local.setdefault(v[9], []).append(v[:3])
    if not local:
        raise ValueError('the skeleton draws nothing')
    joints = [[b.world[k][3] for k in range(3)] for b in bones]
    segs = []   # (bone, a, b): each usable bone from its joint to each child's, or its joint alone
    for i, b in enumerate(bones):
        if not _usable(b.name) or (b.parent < 0 and len(bones) > 1):   # the root stays at the model's origin
            continue
        kids = [j for j, c in enumerate(bones) if c.parent == i]
        for j in kids or [i]:
            segs.append((i, joints[i], joints[j]))
    if not segs:
        raise ValueError('the remake skeleton has no usable bone')
    names = [b.name for b in bones]
    # a ride is a bone's name, or (name, toward): the limb rides name and its model is turned to run from name's joint
    # to toward's in the bind pose (a rigid N64 tail along a chain of remake tail bones)
    rides = {l: (r, None) if isinstance(r, str) else tuple(r) for l, r in (rides or {}).items()}
    for limb, (name, toward) in rides.items():
        if limb not in local or name not in names or (toward is not None and toward not in names):
            raise ValueError(f'limb {limb} draws nothing or the remake has no bone {name} or {toward}')
    aims = {l: names.index(t) for l, (_, t) in rides.items() if t is not None}
    kids = [[j for j, c in enumerate(limbs) if c.parent == i] for i in range(len(limbs))]
    # the bones the tree shapes give, before any vote (the vote fills in what they leave)
    pts0 = {l: [[pos0[l][k] + w[k] for k in range(3)] for w in (_vec3(rots0[l], q) for q in qs)]
            for l, qs in local.items()}
    centre = [None] * len(limbs)
    unders = [None] * len(limbs)   # the vertices each limb's subtree draws
    for i in reversed(range(len(limbs))):
        under = list(pts0.get(i, []))
        stack = list(kids[i])
        while stack:
            c = stack.pop()
            under += pts0.get(c, [])
            stack += kids[c]
        centre[i] = [sum(q[k] for q in under) / len(under) for k in range(3)] if under else pos0[i]
        unders[i] = under
    shaped = _human_match(bones, skel, pos0, unders, set(local)) or {}

    def placed(rots, pos):
        return {l: [[pos[l][k] + w[k] for k in range(3)] for w in (_vec3(rots[l], q) for q in qs)]
                for l, qs in local.items()}

    host = _hosts(skel, pos0)   # a joint's model hung on a leaf of its own belongs to the joint
    hung = [[c for c in kids[i] if host[c] == i] for i in range(len(limbs))]
    jkids = [[c for c in kids[i] if c not in hung[i]] for i in range(len(limbs))]

    def owner(l, pick):   # the bone of the limb, of a model hung at it, or of the first limb under it that draws
        for c in [l] + hung[l]:
            if c in pick:
                return pick[c]
        for c in jkids[l]:
            o = owner(c, pick)
            if o is not None:
                return o
        return None

    def repose(pick):
        rots, pos = [], []
        for l, lb in enumerate(limbs):
            b = owner(l, pick)
            if lb.parent < 0:
                rots.append(rots0[l])
                pos.append(pos0[l])
                continue
            p = lb.parent
            d = _vec3(rots[p], (lb.x, lb.y, lb.z))
            pos.append([pos[p][k] + d[k] for k in range(3)])
            r = _mul3(rots[p], _mul3(_t3(rots0[p]), rots0[l]))
            # the limb runs to its only child chain's first joint away from its own
            c = l
            while len(jkids[c]) == 1 and math.dist(pos0[c], pos0[l]) < 1e-3:
                c = jkids[c][0]
            cb = owner(c, pick) if c != l else None
            if len(jkids[l]) == 1 and c != l and b is not None and cb is not None and cb != b:
                run = _vec3(_t3(rots0[l]), [pos0[c][k] - pos0[l][k] for k in range(3)])
                want = [joints[cb][k] - joints[b][k] for k in range(3)]
                now = _vec3(r, run)
                if sum(x * x for x in want) > 1e-10 and sum(x * x for x in now) > 1e-6:
                    r = _mul3(_rot_between(now, want), r)
            if l in aims and b is not None:   # the model's run: from the joint to its vertices' middle
                qs = local[l]
                run = [sum(q[k] for q in qs) / len(qs) for k in range(3)]
                want = [joints[aims[l]][k] - joints[b][k] for k in range(3)]
                now = _vec3(r, run)
                if sum(x * x for x in want) > 1e-10 and sum(x * x for x in now) > 1e-6:
                    r = _mul3(_rot_between(now, want), r)
            rots.append(r)
        return rots, pos

    ext = lambda ps: math.dist([min(p[k] for p in ps) for k in range(3)], [max(p[k] for p in ps) for k in range(3)])
    rots, pos = rots0, pos0
    for _ in range(3):
        pts = placed(rots, pos)
        # scale: the remake's joints' extent over the N64 vertices' (the outermost joints sit at the tips)
        s = opts.get('scale') or ext(joints) / max(ext([q for ps in pts.values() for q in ps]), 1e-6) * scale
        # each vertex votes for the bone of the segment nearest it; the limb rides the bone with the most votes
        pick = {}
        for limb, ps in pts.items():
            votes = {}
            for q in ps:
                sq = [s * x for x in q]
                bone = min(((_seg_dist(sq, a, b)[0], i) for i, a, b in segs))[1]
                votes[bone] = votes.get(bone, 0) + 1
            pick[limb] = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        pick.update(shaped)
        for limb, (name, _) in rides.items():
            pick[limb] = names.index(name)
        rots, pos = repose(pick)
        # a humanoid's matched limbs start at their bones' joints: the N64 model moves as a whole to put them there on
        # average (its frame may stand it higher or lower than the remake's bind pose), so that a piece does not swing
        # wide of its bone
        # (a TABLE entry that names every main limb by hand, the tree shapes having found none, counts the same)
        matched = shaped or {l: r for l, (r, t) in rides.items()
                             if t is None and any(r in run for run in HUMAN_RUNS.values())}
        if matched:
            n = len(matched)
            shift = [sum(joints[pick[l]][k] / s - pos[l][k] for l in matched) / n for k in range(3)]
            pos = [[p[k] + shift[k] for k in range(3)] for p in pos]
    places = _rerig(skel, pick, rots, pos, s, joints, host, opts.get('bend'), opts.get('still', ())) if opts.get('rerig') else {}
    out = []
    for limb in range(len(limbs)):
        if limb not in pick:
            out.append((None, None))
            continue
        b = pick[limb]
        place = places.get(limb) or [[s * rots[limb][r][c] for c in range(3)] + [s * pos[limb][r]] for r in range(3)]
        inv = _inverse([row[:4] for row in bones[b].world[:3]])
        m = [[sum(inv[r][k] * place[k][c] for k in range(3)) + (inv[r][3] if c == 3 else 0.0) for c in range(4)]
             for r in range(3)]
        out.append((b, m))
    if places:   # what the caller draws with: the places as rotation and position in N64 units
        rots = [[[v / s for v in row[:3]] for row in places[l]] if l in places else rots[l] for l in range(len(limbs))]
        pos = [[row[3] / s for row in places[l]] if l in places else pos[l] for l in range(len(limbs))]
    return s, out, rots, pos


def place_models(bones, specs, meshes, s, turn=None):
    """[(bone, 3x4 matrix)] for models (root display lists) an entry draws beside or instead of a skeleton: a stem, a
    wing. specs: dicts with 'dl' (the list's offset in the object), 'bone' (the bone it rides, at whose joint its
    origin goes) and optionally 'to' (a bone: the model's longest side from its origin is turned to run from 'bone's
    joint to 'to's and stretched to reach it), 'turn' (x, y, z degrees: the model's own turn, else turn, the entry's),
    and 'at' (where its origin goes from the joint, N64 units in the turned model's space). meshes: the models' meshes
    by spec, in order. s: metres a unit."""
    names = [b.name for b in bones]
    joints = [[b.world[k][3] for k in range(3)] for b in bones]
    out = []
    for spec, mesh in zip(specs, meshes):
        b = names.index(spec['bone'])
        t = spec.get('turn', turn)
        base = _rot(*(a * 65536 / 360 for a in t)) if t else [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
        lin = [[s * x for x in row] for row in base]
        if spec.get('to'):
            vs = [_vec3(base, v[:3]) for v in mesh.verts]
            dirs = [[(1.0 if k == j else 0.0) * sg for k in range(3)] for j in range(3) for sg in (1, -1)]
            a = max(dirs, key=lambda u: max(sum(u[k] * v[k] for k in range(3)) for v in vs))
            length = max(sum(a[k] * v[k] for k in range(3)) for v in vs)
            w = [joints[names.index(spec['to'])][k] - joints[b][k] for k in range(3)]
            lw = math.sqrt(sum(x * x for x in w))
            if length > 1e-6 and lw > 1e-9:
                r = _rot_between(a, w)
                k = lw / (s * length)
                u = [x / lw for x in w]
                st = [[(1.0 if i == j else 0.0) + (k - 1) * u[i] * u[j] for j in range(3)] for i in range(3)]
                lin = _mul3(st, _mul3(r, lin))
        at = _vec3(lin, spec.get('at', (0, 0, 0)))
        place = [lin[r] + [joints[b][r] + at[r]] for r in range(3)]
        inv = _inverse([row[:4] for row in bones[b].world[:3]])
        out.append((b, [[sum(inv[r][k] * place[k][c] for k in range(3)) + (inv[r][3] if c == 3 else 0.0)
                         for c in range(4)] for r in range(3)]))
    return out


def _hangs(bones, skel, base, first, limbs):
    """Per entry limb (limbs: (bone, matrix) from node first on): None, or (the index of the nearest drawn limb up the
    N64 tree, its joint in that limb's space in the bind pose). Each bone's bind matrix is taken with unit columns, as
    the runtime takes the bones' turns without their own scale."""
    def placed(i):
        b, m = limbs[i]
        w = [row[:4] for row in bones[b].world[:3]]
        for c in range(3):
            ln = math.sqrt(sum(w[r][c] ** 2 for r in range(3))) or 1.0
            for r in range(3):
                w[r][c] /= ln
        return [[sum(w[r][k] * m[k][c] for k in range(3)) + (w[r][3] if c == 3 else 0.0) for c in range(4)]
                for r in range(3)]
    out = []
    for i, (b, _) in enumerate(limbs):
        limb = first + i - base
        if b is None or not 0 <= limb < len(skel.limbs):
            out.append(None)
            continue
        p = skel.limbs[limb].parent
        while p >= 0 and not (0 <= base + p - first < len(limbs) and limbs[base + p - first][0] is not None):
            p = skel.limbs[p].parent
        if p < 0:
            out.append(None)
            continue
        j = base + p - first
        me, up = placed(i), placed(j)
        try:
            inv = _inverse(up)
        except ValueError:   # a bone bound with a zero scale (a Floormaster's small ones): the limb is placed alone
            out.append(None)
            continue
        out.append((j, tuple(_vec3(inv, [me[k][3] for k in range(3)])[k] + inv[k][3] for k in range(3))))
    return out


def make(rom, fs, first_nodes, log=print, model_nodes=None):
    """actors.bin's bytes for the TABLE's models the game (fs: n64voice.RomFS) and the ROM both have. first_nodes:
    object number -> each skeleton's first node in that object's file (make_n64_mod.py export_object); model_nodes:
    object number -> {model offset: node} for the entries that also draw models (opts 'models').
    An entry whose skeleton index is None draws models alone; its opts then give 'scale'."""
    keep = rom.object(1)
    entries = []
    for model, (number, si, size, rides, *variant) in TABLE.items():
        try:
            data, _ = fs.zs(f'Model/{model}.{model}.bfres.zs')
            bones = bfres_skel.read(data)
            d = rom.object(number)
            skels = n64obj.find(d)[0] if d else []
            segs = n64gfx.Segments()
            segs.set(0x06, d)
            if keep:
                segs.set(0x04, keep)
            opts = variant[1] if len(variant) > 1 else {}
            limbs, at = [], {}   # (bone, matrix) per drawn node, by node
            if si is not None:
                # the mod's skeletons: the object's, then the first one again per further texture set
                node = si if not variant or not variant[0] else len(skels) + variant[0] - 1
                if si >= len(skels) or node >= len(first_nodes.get(number, [])):
                    raise ValueError(f'object {number} has no skeleton {si} (texture set {variant})')
                s = skels[si]
                mesh = n64obj.skeleton_mesh(segs, s)
                frame = rest_frame(d, len(s.limbs), opts.get('frame', 0)) or [0] * (3 + 3 * len(s.limbs))
                scale, limbs, _, _ = fit(bones, s, mesh, frame, size, rides, opts)
                at = {first_nodes[number][node] + l: lb for l, lb in enumerate(limbs)}
            else:
                scale = opts['scale']
            specs = opts.get('models', ())
            if specs:
                nodes = (model_nodes or {}).get(number, {})
                want = [sp['dl'] for sp in specs]
                if any(k not in nodes for k in want):
                    raise ValueError(f'object {number} has no model {[k for k in want if k not in nodes]}')
                meshes = [n64gfx.walk(segs, [0x06000000 | sp['dl']]) for sp in specs]
                for k, placed in zip(want, place_models(bones, specs, meshes, scale, opts.get('turn'))):
                    at[nodes[k]] = placed
            if not at:
                raise ValueError('nothing to draw')
            first = min(at)
            limbs = [at.get(n, (None, None)) for n in range(first, max(at) + 1)]
            hangs = _hangs(bones, s, first_nodes[number][node], first, limbs) if si is not None and not opts.get('loose')                 else [None] * len(limbs)
        except Exception as e:   # one model that does not fit must not stop the others
            log(f'actor {model}: {e}')
            continue
        body = struct.pack('<IIHHHH', bone_hash([b.name for b in bones]), bone_hash([model]), len(bones), number,
                           first, len(limbs))
        for (b, m), h in zip(limbs, hangs):
            body += struct.pack('<HH12f3f', NO_BONE if b is None else b, NO_BONE if h is None else h[0],
                                *([0.0] * 12 if m is None else [x for row in m for x in row]),
                                *(h[1] if h else (0.0, 0.0, 0.0)))
        entries.append(body)
        rode = ', '.join(f'{first + i}:{bones[b].name}' for i, (b, _) in enumerate(limbs) if b is not None)
        log(f'actor {model}: N64 object {number} skeleton {si}, {len(limbs)} nodes, {scale:.5f} m a unit; {rode}')
    return struct.pack('<4sII', b'N64A', VERSION, len(entries)) + b''.join(entries)
