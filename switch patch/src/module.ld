/* The module's layout: code, read-only data and read-write data, each starting on a page, as the NSO format has them
   (tools/elf2nso.py turns the three PT_LOAD segments into its text, ro and data segments). The dynamic tables go in
   the read-only segment, where elf2nso.py finds the symbol and string tables for the NSO header. */

OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(__module_start)

PHDRS
{
    text    PT_LOAD FLAGS(5);
    rodata  PT_LOAD FLAGS(4);
    data    PT_LOAD FLAGS(6);
    dynamic PT_DYNAMIC;
}

SECTIONS
{
    . = 0;
    .text : { KEEP(*(.text.crt0)) *(.text .text.*) } :text

    . = ALIGN(0x1000);
    .rodata : { KEEP(*(.rodata.mod0)) *(.rodata .rodata.*) } :rodata
    .note.gnu.build-id : { *(.note.gnu.build-id) } :rodata
    .dynsym : { *(.dynsym) } :rodata
    .dynstr : { *(.dynstr) } :rodata
    .hash : { *(.hash) } :rodata
    .gnu.hash : { *(.gnu.hash) } :rodata
    .rela.dyn : { *(.rela.dyn) *(.rela.plt) } :rodata
    .eh_frame_hdr : { HIDDEN(__eh_frame_hdr_start = .); *(.eh_frame_hdr) HIDDEN(__eh_frame_hdr_end = .); } :rodata
    .eh_frame : { KEEP(*(.eh_frame)) } :rodata

    . = ALIGN(0x1000);
    .data : { *(.data .data.*) } :data
    .dynamic : { *(.dynamic) } :data :dynamic
    .got : { *(.got) *(.got.plt) } :data
    .bss (NOLOAD) : { HIDDEN(__bss_start = .); *(.bss .bss.* COMMON) . = ALIGN(16); HIDDEN(__bss_end = .); } :data

    /DISCARD/ : { *(.comment) *(.note.GNU-stack) *(.interp) }
}
