"""Plain words for what a save tree node's flag changes mean (launcher/savetree.py, ui/savetree.py).

The game names its flags like `IsGet.Item_BombBag_Small`, `HaveTalked.Npc_Kokiri_Mido_Child` or
`KakarikoVillage_ShiroArrived_MeetingPosition`. describe() turns each change into a sentence ("Got the Bomb Bag",
"Talked to Mido"), ranks them by how much they matter to a player, leaves out the flags the game changes on its own
while it runs (the player's position, loading states, timers), and makes the node's title from the most important
ones. Names it does not know are split into words ("ShiroArrived" -> "Shiro arrived").
"""
import re

ITEMS = {
    'Animal_Fish_Blue': 'a Fish', 'Animal_Insect': 'a Bug',
    'Armor_Adult_Gauntlet_Gold': 'the Golden Gauntlets', 'Armor_Adult_Gauntlet_Silver': 'the Silver Gauntlets',
    'Armor_Adult_Goron': 'the Goron Tunic', 'Armor_Adult_Zora': 'the Zora Tunic', 'Armor_Adult_HeavyBoot': 'the Iron Boots',
    'Armor_Adult_HoverBoot': 'the Hover Boots', 'Armor_Adult_Normal': 'the Kokiri Tunic',
    'Armor_Child_Gauntlet_Goron': "the Goron's Bracelet",
    'Armor_Child_Mask_Bunny': 'the Bunny Hood', 'Armor_Child_Mask_Gerudo': 'the Gerudo Mask',
    'Armor_Child_Mask_Goron': 'the Goron Mask', 'Armor_Child_Mask_Keaton': 'the Keaton Mask',
    'Armor_Child_Mask_Skull': 'the Skull Mask', 'Armor_Child_Mask_Spooky': 'the Spooky Mask',
    'Armor_Child_Mask_Truth': 'the Mask of Truth', 'Armor_Child_Mask_Zora': 'the Zora Mask',
    'Armor_GlassTruth': 'the Lens of Truth', 'Item_AgonyStone': 'the Stone of Agony', 'Item_AlarmCucco': 'the Cucco',
    'Item_Arrow_Fire': 'the Fire Arrows', 'Item_Arrow_Ice': 'the Ice Arrows', 'Item_Arrow_Light': 'the Light Arrows',
    'Item_Arrow_Normal': 'Arrows', 'Item_Bomb': 'Bombs', 'Item_BombBag_Small': 'the Bomb Bag',
    'Item_BombBag_Medium': 'the Big Bomb Bag', 'Item_BombBag_Large': 'the Biggest Bomb Bag', 'Item_Bombchu': 'Bombchus',
    'Item_Boomerang': 'the Boomerang', 'Item_Bottle': 'an empty Bottle', 'Item_Bottle_BigPoe': 'a Big Poe',
    'Item_Bottle_BlueFire': 'Blue Fire', 'Item_Bottle_BluePotion': 'a Blue Potion', 'Item_Bottle_Bug': 'a Bug',
    'Item_Bottle_Fairy': 'a Fairy', 'Item_Bottle_Fish': 'a Fish', 'Item_Bottle_GreenPotion': 'a Green Potion',
    'Item_Bottle_Letter': "Ruto's Letter", 'Item_Bottle_Milk': 'Lon Lon Milk', 'Item_Bottle_Poe': 'a Poe',
    'Item_Bottle_RedPotion': 'a Red Potion', 'Item_BrokenSword': "the Broken Goron's Sword",
    'Item_BulletBag_Small': 'the Bullet Bag', 'Item_BulletBag_Medium': 'the Bigger Bullet Bag',
    'Item_BulletBag_Large': 'the Biggest Bullet Bag', 'Item_ClaimCheck': 'the Claim Check',
    'Item_CojiroCucco': 'Cojiro', 'Item_DekuNut': 'Deku Nuts', 'Item_DekuSeed': 'Deku Seeds', 'Item_DekuStick': 'Deku Sticks',
    'Item_DinFire': "Din's Fire", 'Item_Dungeon_BossKey': 'the Boss Key', 'Item_Dungeon_Compass': 'the Compass',
    'Item_Dungeon_Map': 'the Dungeon Map', 'Item_Dungeon_SmallKey': 'a Small Key', 'Item_EyeDrop': 'the Eye Drops',
    'Item_EyeballFrog': 'the Eyeball Frog', 'Item_FaroreWind': "Farore's Wind", 'Item_FishingRod': 'the Fishing Rod',
    'Item_Hammer': 'the Megaton Hammer', 'Item_Hookshot': 'the Hookshot', 'Item_Longshot': 'the Longshot',
    'Item_MagicBean': 'a Magic Bean', 'Item_Medal_Fire': 'the Fire Medallion', 'Item_Medal_Forest': 'the Forest Medallion',
    'Item_Medal_Light': 'the Light Medallion', 'Item_Medal_Shadow': 'the Shadow Medallion',
    'Item_Medal_Spirit': 'the Spirit Medallion', 'Item_Medal_Water': 'the Water Medallion',
    'Item_Melody_EponaSong': "Epona's Song", 'Item_Melody_FireBolero': 'the Bolero of Fire',
    'Item_Melody_ForestMinuet': 'the Minuet of Forest', 'Item_Melody_LightPrelude': 'the Prelude of Light',
    'Item_Melody_SariaSong': "Saria's Song", 'Item_Melody_ScarecrowSong': "the Scarecrow's Song",
    'Item_Melody_ShadowNocturne': 'the Nocturne of Shadow', 'Item_Melody_SpiritRequiem': 'the Requiem of Spirit',
    'Item_Melody_StormSong': 'the Song of Storms', 'Item_Melody_SunSong': "the Sun's Song",
    'Item_Melody_TimeSong': 'the Song of Time', 'Item_Melody_WaterSerenade': 'the Serenade of Water',
    'Item_Melody_ZeldaLullaby': "Zelda's Lullaby", 'Item_NayruLove': "Nayru's Love",
    'Item_NutBag_Small': 'a bigger Deku Nut capacity', 'Item_NutBag_Medium': 'a bigger Deku Nut capacity',
    'Item_NutBag_Large': 'the biggest Deku Nut capacity', 'Item_Ocarina_Fairy': 'the Fairy Ocarina',
    'Item_Ocarina_Time': 'the Ocarina of Time', 'Item_OddMushroom': 'the Odd Mushroom', 'Item_OddPowder': 'the Odd Potion',
    'Item_PoacherSaw': "the Poacher's Saw", 'Item_PocketCucco': 'the Pocket Cucco', 'Item_PocketEgg': 'the Pocket Egg',
    'Item_Prescription': 'the Prescription', 'Item_Quiver_Small': 'the Quiver', 'Item_Quiver_Medium': 'the Big Quiver',
    'Item_Quiver_Large': 'the Biggest Quiver', 'Item_Scale_Gold': 'the Golden Scale', 'Item_Scale_Silver': 'the Silver Scale',
    'Item_StickBag_Small': 'a bigger Deku Stick capacity', 'Item_StickBag_Medium': 'a bigger Deku Stick capacity',
    'Item_StickBag_Large': 'the biggest Deku Stick capacity', 'Item_Stone_Fire': "Goron's Ruby",
    'Item_Stone_Forest': "Kokiri's Emerald", 'Item_Stone_Water': "Zora's Sapphire", 'Item_Token_Gerudo': "the Gerudo's Card",
    'Item_Token_Gold': 'a Gold Skulltula Token', 'Item_Wallet_Small': "the Adult's Wallet",
    'Item_Wallet_Medium': "the Giant's Wallet", 'Item_Wallet_Large': "the Tycoon's Wallet", 'Item_WeirdEgg': 'the Weird Egg',
    'Item_ZeldaLetter': "Zelda's Letter", 'Item_FairyBless_MP': 'Magic', 'Item_FairyBless_MPDouble': 'double Magic',
    'Item_FairyBless_DefenseDouble': 'double Defense', 'Weapon_Range_Bow': "the Fairy Bow",
    'Weapon_Range_SlingShot': "the Fairy Slingshot", 'Weapon_Shield_Deku': 'the Deku Shield',
    'Weapon_Shield_Hylian': 'the Hylian Shield', 'Weapon_Shield_Mirror': 'the Mirror Shield',
    'Weapon_Sword_Biggoron': "the Biggoron's Sword", 'Weapon_Sword_Giant': "the Giant's Knife",
    'Weapon_Sword_BrokenGiant': "the broken Giant's Knife", 'Weapon_Sword_Kokiri': 'the Kokiri Sword',
    'Weapon_Sword_Master': 'the Master Sword', 'Pouch_Range_BowAndArrow_Fire': 'the Fire Arrows',
    'Pouch_Range_BowAndArrow_Ice': 'the Ice Arrows', 'Pouch_Range_BowAndArrow_Light': 'the Light Arrows',
}

RACES = {'Hylia': 'a villager', 'Kokiri': 'a Kokiri', 'Zora': 'a Zora', 'Goron': 'a Goron', 'Gerudo': 'a Gerudo',
         'Kakashi': 'the Scarecrow', 'GuidePoe': 'the guide Poe'}
SKIP_WORDS = {'Npc', 'NPC', 'Obj', 'Enemy', 'Default', 'DefaultActor', 'Child', 'Adult', 'child', 'adult', 'Exp'}

# Flags the game changes by itself as it runs: never the title, and not listed among what happened.
HIDDEN = re.compile(r'^(Sequence_|System_|UI_|CaptionData\.|ActionGuide_|DebugParam\.|PlayerStatus\.Current(Pos|Dir)$|'
                    r'Player_Rot|IsEnableUse|flag [0-9a-f]{8}$|HavePlayedNaviBalloonMessage\.|FindTargetNearDoor\.)')
# Background life of a place: people walking their daily routes. Summed up as one line.
ROUTINE = re.compile(r'(Arrived|AriivedHome|ArrivedHome|_Action\d|_MeetingPosition|NoAttention|IsInTheCage|_Schedule|'
                     r'_AiSchedule|DoorClosed)')


def words(name):
    """'KakarikoVillage_ShiroArrived_MeetingPosition' -> 'Kakariko Village shiro arrived meeting position'."""
    parts = []
    for p in re.split(r'[_.]+', name):
        if not p or p in SKIP_WORDS or re.fullmatch(r'\d+|[0-9a-f]{4}', p):
            continue
        p = re.sub(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)', ' ', p)
        parts.append(p)
    text = ' '.join(parts).strip()
    return text[:1].upper() + text[1:] if text else name


def place(area):
    """'KakarikoVillage_Child' -> 'Kakariko Village'."""
    a = re.sub(r'_(Child|Adult)$', '', area or '')
    return words(a) if a and a != '?' else 'somewhere'


def item(name):
    name = re.sub(r'\.StockNum$', '', name)
    return ITEMS.get(name) or words(re.sub(r'^(Item|Weapon|Armor|Pouch|Animal)_', '', name))


def person(npc):
    """'Npc_Kokiri_Mido_Child' -> 'Mido'; 'Npc_Hylia_017_Adult' -> 'a villager'."""
    parts = [p for p in npc.split('_') if p and p not in SKIP_WORDS and not p.isdigit() and p != 'Talk']
    if parts and parts[0] in RACES:
        race = parts.pop(0)
        named = [p for p in parts if not re.fullmatch(r'[A-Z]{2,3}|\d+', p)]
        return words('_'.join(named)) if named else RACES[race]
    return words('_'.join(parts)) if parts else 'someone'


def where(name):
    """' in Spirit Temple, room 7' for a flag named after a place and a room, ' in Spirit Temple' after a place alone."""
    m = re.match(r'([A-Z][A-Za-z]+?)(?:_Level)?_Room_?(\d+)', name)
    if m:
        return f' in {place(m.group(1))}, room {int(m.group(2))}'
    m = re.match(r'([A-Z][a-z]+(?:[A-Z][a-z]+)+)_', name)
    return f' in {place(m.group(1))}' if m else ''


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def sentence(typ, name, old, new):
    """(rank, text) for one change: rank 0 is hidden, 1 routine, higher matters more. None to leave it out."""
    if HIDDEN.search(name) or typ == 'Float' or (typ == 'Vector3'):
        return 0, None
    on = new == 'on'
    a, b = _int(old), _int(new)
    m = re.match(r'IsGet\.(.+)$', name)
    if m:
        if m.group(1).startswith('Shop_'):
            return (70, 'Bought something at a shop') if on else (0, None)
        if m.group(1).startswith(('Obj_', 'TBox_')):
            return (60, f'Found {words(m.group(1))}') if on else (0, None)
        return (90, f'Got {item(m.group(1))}') if on else (40, f'Lost {item(m.group(1))}')
    if name.startswith(('HavePlayedGetEvent.', 'HavePlayedGetEventAtShop.')):
        return 0, None   # the item's own IsGet says it
    if name == 'PlayerStatus.CurrentRupee' or name.endswith('Rupee'):
        if a is not None and b is not None:
            return 45, (f'Got {b - a} rupee{"s" if b - a != 1 else ""} ({b} now)' if b > a else
                        f'Spent {a - b} rupee{"s" if a - b != 1 else ""} ({b} left)')
    m = re.match(r'PouchData\.(.+?)(\.StockNum)?$', name)
    if m:
        if m.group(2) and a is not None and b is not None:
            return 35, f'{item(m.group(1))}: {a} → {b}'
        return (80, f'Got {item(m.group(1))}') if on else (30, f'No longer has {item(m.group(1))}')
    m = re.match(r'DungeonInfo\.(\w+)\.(\w+)$', name)
    if m:
        d, what = place(m.group(1)), m.group(2)
        if what.startswith('IsGet') and on:
            return 85, f'Got the {words(what[5:]).lower()} of {d}'
        if what == 'SmallKeyNum' and a is not None and b is not None:
            return 75, f'{"Got" if b > a else "Used"} a small key in {d} ({b} now)'
        if what == 'BossRetryNum':
            return 50, f'Tried the boss of {d} again'
        return 40, f'{d}: {words(what).lower()} {old} → {new}'
    m = re.match(r'IsVisitLocation\.(\w+)$', name)
    if m and on:
        return 88, f'First visit to {place(m.group(1))}'
    m = re.match(r'DefeatedEnemyNum\.(\w+)$', name)
    if m:
        foe = words(re.sub(r'^Enemy_(DungeonBoss_|MiddleBoss_)?', '', m.group(1)))
        boss = 'DungeonBoss' in m.group(1)
        return (95 if boss else 55), (f'Defeated {foe}' if boss else f'Defeated a {foe}')
    m = re.match(r'HaveDefeatedEnemy_(\w+)$', name)
    if m and on:
        return 55, f'Defeated {words(m.group(1))}'
    m = re.match(r'HaveTalked(_Special)?\.(.+)$', name)
    if m and on:
        who = m.group(2)
        npc = re.match(r'(.*?Npc_[A-Za-z]+_[A-Za-z0-9]+(?:_[A-Za-z]+)?)', who)
        if m.group(1):   # a story conversation: 'Get_StoneOfWater_Step01_Npc_Zora_KingZora_child_Talk_Event177'
            n = re.search(r'Npc_\w+?(?=_Talk|$)', who)
            return 65, f'Talked to {person(n.group()) if n else words(who)} about something new'
        return 60, f'Talked to {person(who)}' if npc or who.startswith(('Npc', 'Obj')) else f'Talked: {words(who)}'
    m = re.match(r'HavePlayedEvent\.(.+)$', name)
    if m and on:
        ev = m.group(1)
        if 'GameOver' in ev:
            return 70, 'Game over'
        if ev.startswith(('Dm_', 'DmF_')):
            return 78, f'Watched a cutscene ({words(ev[3:] if ev.startswith("Dm_") else ev[4:])})'
        return 58, f'Event: {words(ev)}'
    if name.startswith('NaviHint_') and on:
        return 25, f'Navi gave a hint ({words(name[9:].replace("_FirstTime_HasRead", "").replace("_HasRead", ""))})'
    if name.startswith('BlewOcarinaByGossipStone.') and on:
        return 50, 'Played the ocarina at a Gossip Stone'
    if name.startswith('Shop_SoldNum.'):
        return 50, f'Bought {words(name[13:])}'
    if name.startswith('GossipStoneDisappear.') and on:
        return 40, 'A Gossip Stone disappeared'
    if re.search(r'_IsAfter_|_Step\d|Complete|_Get_|^Get_|^Meet_|^Sage', name):
        if typ == 'Bool' and not on:
            return 0, None
        return 72, f'Story: {words(name)}'
    if ROUTINE.search(name):
        return 1, None
    if re.search(r'TBox', name) and on:
        return 62, f'Opened a chest{where(name) or f" ({words(name)})"}'
    if re.search(r'Cleared$|_Clear$', name) and on:
        return 64, f'Cleared a room{where(name) or f" ({words(name)})"}'
    if re.search(r'(Door|Shutter|Gate)(Open|_Open|Opened)', name) and on:
        return 52, f'Opened a door{where(name) or f" ({words(name)})"}'
    if re.search(r'Switch|Swich', name) and on:
        return 48, f'Pressed a switch{where(name) or f" ({words(name)})"}'
    if typ == 'Bool':
        # 'DodongoCavern_Room01_ReadStoneTablet' -> 'Read stone tablet in Dodongo Cavern, room 1'
        m = re.match(r'[A-Z][A-Za-z]+?(?:_Level)?_Room_?\d+_(.+)$', name)
        text = f'{words(m.group(1))}{where(name)}' if m else words(name)
        return 20, text + ('' if on else ' (undone)')
    if typ == 'String':
        return (0, None) if name.lower().startswith('bgm') or 'BGM' in new else (15, f'{words(name)}: {new}')
    if a is not None and b is not None:
        return 20, f'{words(name)}: {a} → {b}'
    return 10, f'{words(name)}: {old} → {new}'


def describe(node, parent=None):
    """(title, [(rank, text, raw), ...] most important first). parent: the node before it, for a change of place."""
    lines, routine = [], 0
    if parent is not None and place(parent.area) != place(node.area):
        lines.append((99, f'Went to {place(node.area)}', f'from {place(parent.area)}'))
    for ch in node.changes:
        typ, name, old, new = (list(ch) + ['', '', '', ''])[:4]
        rank, text = sentence(typ, name, old, new)
        raw = f'{name}: {old} → {new}'
        if rank == 1:
            routine += 1
        elif text:
            lines.append((rank, text, raw))
    lines.sort(key=lambda t: -t[0])
    seen, unique = set(), []
    for t in lines:   # the same sentence from two flags once
        if t[1] not in seen:
            seen.add(t[1])
            unique.append(t)
    lines = unique
    if routine:
        lines.append((1, f'People in {place(node.area)} went about their day', f'{routine} routine flags'))
    if node.kind == 'load' and node.parent < 0:
        title = f'Save loaded in {place(node.area)}'
    elif lines and lines[0][0] >= 20:
        top = [t[1] for t in lines if t[0] >= max(20, lines[0][0] - 25)][:2]
        title = ' · '.join(top)
        more = sum(1 for t in lines if t[0] >= 20) - len(top)
        if more > 0:
            title += f' (+{more})'
    elif routine:
        title = f'Time passed in {place(node.area)}'
    elif node.kind == 'manual':
        title = f'Saved in {place(node.area)}'
    elif node.kind == 'autosave':
        title = f'Autosave in {place(node.area)}'
    else:
        title = f'Small changes in {place(node.area)}'
    if node.n > len(node.changes):
        lines.append((0, f'… {node.n - len(node.changes)} more flags not recorded', ''))
    return title, lines
