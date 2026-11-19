"""The game builds the Switch patch supports, and every address it uses in each (offsets from main's base).

Found in the game's own main (an extracted exefs under build/): tools/check.py reads them back from the user's copy
and refuses a build whose words differ. The runtime's addresses for the same things are in runtime/debug_tools.c and
runtime/debug_warp.c, through RT_OOT_ADDR.

  build_id      main's module id (the first 20 bytes, hex), which names the IPS file
  mod0          main's MOD0 header; the module tells the builds apart by it
  nnmain        main's exported nnMain, which the module imports to find main's base
  text_end      the end of main's code; the page after it up to the read-only segment is executable padding
  bss_end       the end of main's own bss (from MOD0); the memory after it up to image_end is mapped and unused
  image_end     the end of main's mapping
  frame_site    the framework's per-frame update, right after its prologue: ldr x8, [x0, #0xa8]
  frame_word    that instruction, checked before the patch replaces it
  player_slot   main+: pointer to the object that holds the player actor (+0x480)
  warp_actor    the game's actor warp: (actor, &3x4 row-major pose, 0)
"""

PROGRAM_ID = '0400486024DEC000'
PATCH_FORMAT = 1
MARKER = b'OOTPC-SW'

BUILDS = {
    '1.1.1': dict(code=111, build_id='E0F3CCFAF50065AE4EA01EB5AEB13B1A45DA3348', mod0=0x3c4705c,
                  nnmain=0x1202cd0, text_end=0x3c461d0, bss_end=0x4e9a4c8, image_end=0x4ea0000,
                  frame_site=0x6e6bf8, frame_word=0xf9405408, player_slot=0x4e371d0, warp_actor=0xc6fb30),
    '1.0.0': dict(code=100, build_id='33FA6C2C23848282A77E8B88A0A93F4887C55920', mod0=0x3be705c,
                  nnmain=0x11c1fb0, text_end=0x3be6850, bss_end=0x4e2a068, image_end=0x4e30000,
                  frame_site=0x6b5920, frame_word=0xf9405408, player_slot=0x4dc7210, warp_actor=0xc35500),
}


def stub_addr(b):
    """Where the patch puts its marker (16 bytes) and the hook stub after it: the executable padding after main's
    code, 16-byte aligned."""
    return (b['text_end'] + 15) & ~15


def slot_addr(b):
    """The 8 bytes the module writes its frame function's address to, read by the stub: near the end of main's
    mapping, well past the end of main's own bss so no object of the game reaches it."""
    return b['image_end'] - 0x100
