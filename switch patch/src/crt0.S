// The module's header, as the system's dynamic loader expects of every module: the word at offset 4 of the code
// gives the MOD0 header's offset, and MOD0 gives (relative to itself) the dynamic section, the bss, the unwind table
// header and the space the loader keeps its record of the module in. The loader relocates the module and calls its
// DT_INIT function (module.c ootpc_module_init); nothing runs the code at offset 0.

        .section .text.crt0, "ax", %progbits
        .global __module_start
        .hidden __module_start
__module_start:
        b       1f
        .word   __mod0 - __module_start
1:      ret

        .section .rodata.mod0, "a", %progbits
        .balign 4
__mod0:
        .ascii  "MOD0"
        .word   _DYNAMIC - __mod0
        .word   __bss_start - __mod0
        .word   __bss_end - __mod0
        .word   __eh_frame_hdr_start - __mod0
        .word   __eh_frame_hdr_end - __mod0
        .word   __module_object - __mod0

        .section .bss.module_object, "aw", %nobits
        .balign 16
__module_object:
        .space  0x200
