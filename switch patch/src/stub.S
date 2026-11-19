// The frame hook stub the IPS patch writes into the executable padding after main's code (tools/builds.py
// stub_addr). The framework's per-frame update branches here in place of its ldr x8, [x0, #0xa8]; the stub calls the
// module's frame function when the module has written its address to the slot (tools/builds.py slot_addr), then makes
// that load and branches back.
//
// The site is right after the update's prologue, so only its arguments are live there. The stub still keeps every
// register the frame function may change (x0-x18, x30, the flags, v0-v7 and v16-v31); x19-x29 and d8-d15 are kept by
// the frame function itself, as the procedure call standard requires.
//
// Linked once per game build at the stub's address (tools/build.py), with these symbols set:
//   hook_slot      the slot's address
//   frame_return   the instruction after the patched one
//   build_code     the game build (111, 100), kept in the marker for the module to check

        .section .text, "ax", %progbits
        .global _start
_start:
        .ascii  "OOTPC-SW"              // the marker the module looks for (tools/builds.py MARKER)
        .word   1                       // patch format (tools/builds.py PATCH_FORMAT)
        .word   build_code

        .equ    SAVE, 0x230
frame_stub:
        sub     sp, sp, #SAVE
        stp     x0, x1, [sp, #0x00]
        stp     x2, x3, [sp, #0x10]
        stp     x4, x5, [sp, #0x20]
        stp     x6, x7, [sp, #0x30]
        stp     x8, x9, [sp, #0x40]
        stp     x10, x11, [sp, #0x50]
        stp     x12, x13, [sp, #0x60]
        stp     x14, x15, [sp, #0x70]
        stp     x16, x17, [sp, #0x80]
        stp     x18, x30, [sp, #0x90]
        mrs     x9, nzcv
        str     x9, [sp, #0xa0]
        stp     q0, q1, [sp, #0xb0]
        stp     q2, q3, [sp, #0xd0]
        stp     q4, q5, [sp, #0xf0]
        stp     q6, q7, [sp, #0x110]
        stp     q16, q17, [sp, #0x130]
        stp     q18, q19, [sp, #0x150]
        stp     q20, q21, [sp, #0x170]
        stp     q22, q23, [sp, #0x190]
        stp     q24, q25, [sp, #0x1b0]
        stp     q26, q27, [sp, #0x1d0]
        stp     q28, q29, [sp, #0x1f0]
        stp     q30, q31, [sp, #0x210]

        adrp    x16, hook_slot
        ldr     x16, [x16, #:lo12:hook_slot]
        cbz     x16, 1f
        blr     x16                     // x0: the framework object, as the update got it
1:
        ldp     q30, q31, [sp, #0x210]
        ldp     q28, q29, [sp, #0x1f0]
        ldp     q26, q27, [sp, #0x1d0]
        ldp     q24, q25, [sp, #0x1b0]
        ldp     q22, q23, [sp, #0x190]
        ldp     q20, q21, [sp, #0x170]
        ldp     q18, q19, [sp, #0x150]
        ldp     q16, q17, [sp, #0x130]
        ldp     q6, q7, [sp, #0x110]
        ldp     q4, q5, [sp, #0xf0]
        ldp     q2, q3, [sp, #0xd0]
        ldp     q0, q1, [sp, #0xb0]
        ldr     x9, [sp, #0xa0]
        msr     nzcv, x9
        ldp     x18, x30, [sp, #0x90]
        ldp     x16, x17, [sp, #0x80]
        ldp     x14, x15, [sp, #0x70]
        ldp     x12, x13, [sp, #0x60]
        ldp     x10, x11, [sp, #0x50]
        ldp     x8, x9, [sp, #0x40]
        ldp     x6, x7, [sp, #0x30]
        ldp     x4, x5, [sp, #0x20]
        ldp     x2, x3, [sp, #0x10]
        ldp     x0, x1, [sp, #0x00]
        add     sp, sp, #SAVE
        ldr     x8, [x0, #0xa8]         // the instruction the patch replaced
        b       frame_return
