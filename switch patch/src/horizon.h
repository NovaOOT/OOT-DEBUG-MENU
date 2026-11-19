// The system calls and SDK functions the module uses. The SDK functions are imported from the game's own sdk module,
// which exports them; tools/build.py refuses a module that imports anything the user's sdk or main does not export.
#pragma once

#include <stdint.h>

// ---- system calls (the game's NPDM allows both: tools/check.py)

typedef struct {
    uint64_t addr, size;
    uint32_t type, attr, perm, ipc_refs, device_refs, pad;
} MemInfo;

enum { PERM_R = 1, PERM_W = 2, PERM_X = 4 };

// QueryMemory (0x06): the mapping that holds addr. Returns 0 on success.
static inline uint32_t svc_query_memory(MemInfo *out, uint64_t addr) {
    register uint64_t x0 __asm__("x0") = (uint64_t)(uintptr_t)out;
    register uint64_t x1 __asm__("x1") = 0;
    register uint64_t x2 __asm__("x2") = addr;
    __asm__ volatile("svc 0x6"
                     : "+r"(x0), "+r"(x1), "+r"(x2)
                     :
                     : "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15", "x16",
                       "x17", "memory", "cc");
    return (uint32_t)x0;
}

// OutputDebugString (0x27): a line for the debugger, which emulators show in their log.
static inline void svc_output_debug_string(const char *s, uint64_t n) {
    register uint64_t x0 __asm__("x0") = (uint64_t)(uintptr_t)s;
    register uint64_t x1 __asm__("x1") = n;
    __asm__ volatile("svc 0x27"
                     : "+r"(x0), "+r"(x1)
                     :
                     : "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9", "x10", "x11", "x12", "x13", "x14", "x15",
                       "x16", "x17", "memory", "cc");
}

// ---- controller input (nn::hid)

// One controller's state, 0x28 bytes: the game reads 16 of them into 0x280 bytes (main+0x346966c on 1.1.1).
typedef struct {
    int64_t sampling;
    uint64_t buttons;
    int32_t stick_l[2], stick_r[2];
    uint32_t attributes;     // bit 0: connected
    uint32_t pad;
} NpadState;

_Static_assert(sizeof(NpadState) == 0x28, "NpadState is 0x28 bytes");

enum {
    BTN_A = 1u << 0, BTN_B = 1u << 1, BTN_X = 1u << 2, BTN_Y = 1u << 3,
    BTN_LS = 1u << 4, BTN_RS = 1u << 5, BTN_L = 1u << 6, BTN_R = 1u << 7,
    BTN_ZL = 1u << 8, BTN_ZR = 1u << 9, BTN_PLUS = 1u << 10, BTN_MINUS = 1u << 11,
    BTN_LEFT = 1u << 12, BTN_UP = 1u << 13, BTN_RIGHT = 1u << 14, BTN_DOWN = 1u << 15,
};

enum { NPAD_NO1 = 0, NPAD_HANDHELD = 0x20 };

#define SDK_IMPORT __attribute__((visibility("default")))

// nn::hid::GetNpadStates for the three styles the game reads: a Pro Controller, a pair of Joy-Con held together,
// and the console in handheld mode. Newest state first.
SDK_IMPORT void _ZN2nn3hid13GetNpadStatesEPNS0_16NpadFullKeyStateEiRKj(NpadState *out, int count, const uint32_t *id);
SDK_IMPORT void _ZN2nn3hid13GetNpadStatesEPNS0_16NpadJoyDualStateEiRKj(NpadState *out, int count, const uint32_t *id);
SDK_IMPORT void _ZN2nn3hid13GetNpadStatesEPNS0_17NpadHandheldStateEiRKj(NpadState *out, int count, const uint32_t *id);

// main's entry point, exported by main: its address less its offset in the build is main's base.
SDK_IMPORT extern char nnMain[];
