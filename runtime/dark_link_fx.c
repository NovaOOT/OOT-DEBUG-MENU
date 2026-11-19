// Dark Link's standing effects on the player while the Dark Link mod plays with the remake's models (OOT-PC.ini [mods]
// DARK_LINK_EFFECTS, on by default, the Mods tab's "Dark Link effects"; off, effects already playing end by themselves
// or with the area).
//
// The Dark Link enemy (Enemy_MiddleBoss_DarkLink) has an effect user of its own in the game's effect links
// (ELink2/elink2.Product.*.belnk), and its own logic starts the effects it wears: the dark smoke round it
// (Intensification_Aura) and the red trail its eyes leave (Eye_Trail). The player keeps
// his own user (Player_Adult, thousands of keys: his sword, items and magic). The mod gives Dark Link's user to the
// adult's gear whose own effects matter least (tools/make_dark_link_mod.py LENDERS: the Hylian Shield, the Iron and
// Hover Boots), so the game makes that user's resource while he wears one of them; once a frame, from the
// player's reaction scheduler (debug_game.c n64_sword_wall, the game thread), a key whose last effect has ended is
// started on the player with his effect user instance lent that resource for the call, then given his own back. The
// effect takes its emitter from Dark Link's user and binds to the player's bones (the enemy's skeleton is his), so it
// follows him.
//
// The memory, as read in 1.1.1:
// - the emit (n64sword.c play_at): XLINK_EMIT_KEY (the XLink holder [[actor+0x270]+0x68], a pointer to the key, two
//   handles, 0 for the effect user) fills the effect handle (pool byte, index s16 at +2, serial u32 at +4); the
//   handle's instance is [[HANDLE_BASES]+8*pool] + [[HANDLE_SIZES]+8*pool] * index, live while its u32 at +0x20 is
//   the serial;
// - the holder's effect user instance at +0x70, its user resource at +0x58; a resource holds the address of its
//   user's data in the loaded file (at +0xf0 in 1.1.1: the first word of its first USER_RES_SPAN bytes that lies in a
//   loaded file's user table is taken);
// - the loaded file: "XLNK", its size at +4, the user count at +0x48, the users' name hashes (CRC-32, sorted) at +0x60
//   and their data after them (u64, 8-aligned: offsets in the file, addresses once the game has loaded it). The file is
//   found by going back from the player's user data to its header. Dark Link's resource is the one of a lending actor
//   (LENDERS) that holds Dark Link's data where the player's holds his. Without one of them worn, no effect starts.
#include <stdatomic.h>
#include <stdint.h>
#include <string.h>

#include "cfg.h"
#include "dark_link_fx.h"
#include "debug_tools.h"
#include "host.h"
#include "n64gfx.h"
#include "platform/platform.h"

#define XLINK_EMIT_KEY RT_OOT_ADDR(0x700043fd50ull, 0x700051e470ull)
#define HANDLE_BASES RT_OOT_ADDR(0x7004c7e260ull, 0x7004c185b8ull)
#define HANDLE_SIZES RT_OOT_ADDR(0x7004c7e268ull, 0x7004c185c0ull)
#define HOLDER_EFFECT 0x70       // the holder's effect user instance
#define INSTANCE_RES 0x58        // the instance's user resource
#define USER_RES_SPAN 0x600      // the user resource's words looked at for the data's address
#define FILE_BACK_MAX (8u << 20) // how far back from a user's data its file's header may be
#define XLNK 0x4b4e4c58u
#define DARK_USER_HASH 0xe9f45bcau   // CRC-32 of "Enemy_MiddleBoss_DarkLink"
#define RETRY_NS 500000000ull    // a key that started nothing is tried again this much later

// Only effects that play until they are stopped: a key whose effect has ended is started again, so a one-shot would
// play over and over. The enemy's Start_Emisson_Eye is one (the burst it makes once as it appears, under its feet):
// started again every few seconds it was a red flash at the player's feet.
static const char *const KEYS[] = {"Intensification_Aura", "Eye_Trail"};
#define NKEYS ((int)(sizeof KEYS / sizeof KEYS[0]))

static struct {
    uint64_t player;             // the player the handles belong to
    uint64_t dark, off;          // Dark Link's user data in the loaded file; where a user resource holds its data
    int failed;                  // the file or Dark Link's user was not found for this player
    uint8_t handle[NKEYS][8];    // the effect handle of each key's last start
    uint64_t tried_ns[NKEYS];    // when each key was last started
    int logged;
} S;

static uint64_t ptr(uint64_t a) {
    uint64_t v = 0;
    return a && plat_mem_read(a, &v, 8) ? v : 0;
}

// The instance a handle names, 0 when it names none or one no longer live.
static uint64_t handle_instance(const uint8_t *h) {
    int8_t pool = (int8_t)h[0];
    int16_t index;
    uint32_t serial, live = 0;
    memcpy(&index, h + 2, 2), memcpy(&serial, h + 4, 4);
    if (pool < 0 || index < 0) return 0;
    uint64_t base = ptr(ptr(HANDLE_BASES) + 8ull * (uint64_t)pool);
    uint64_t size = ptr(ptr(HANDLE_SIZES) + 8ull * (uint64_t)pool), inst = base + size * (uint64_t)index;
    return base && plat_mem_read(inst + 0x20, &live, 4) && live == serial ? inst : 0;
}

// Dark Link's user data in the file that holds own (a user's data), 0 when not found.
static uint64_t dark_user(uint64_t own) {
    uint32_t w[2];
    uint64_t file = 0;
    for (uint64_t a = own & ~7ull; a + FILE_BACK_MAX > own && a > 0x1000; a -= 8)
        if (plat_mem_read(a, w, 8) && w[0] == XLNK && a + w[1] > own) {
            file = a;
            break;
        }
    uint32_t n = 0;
    if (!file || !plat_mem_read(file + 0x48, &n, 4) || !n || n > 0x10000) return 0;
    int lo = 0, hi = (int)n - 1;
    while (lo <= hi) {   // the hashes are sorted
        int mid = (lo + hi) / 2;
        uint32_t h = 0;
        if (!plat_mem_read(file + 0x60 + 4ull * (uint64_t)mid, &h, 4)) return 0;
        if (h == DARK_USER_HASH) {
            uint64_t offs = (file + 0x60 + 4ull * n + 7) & ~7ull, off = ptr(offs + 8ull * (uint64_t)mid);
            if (off >= file && off < file + w[1]) return off;   // the loader made the offsets addresses
            return off && off < w[1] ? file + off : 0;
        }
        if (h < DARK_USER_HASH) lo = mid + 1;
        else hi = mid - 1;
    }
    return 0;
}

// The resource of Dark Link's user that the game made for the lending gear the adult wears (tools/make_dark_link_mod.py
// LENDERS): found by a helper thread among the objects of their names (dbg_find_named: two heap passes, a second or two), kept while it still
// holds Dark Link's data at the same place.
static const char *const LENDERS[] = {"Weapon_Shield_Hylian", "Armor_Adult_HeavyBoot", "Armor_Adult_HoverBoot"};
#define NLENDERS ((int)(sizeof LENDERS / sizeof LENDERS[0]))
#define SEARCH_EVERY_NS 5000000000ull
static _Atomic uint64_t dark_res, search_ns;
static _Atomic int searching;
static uint64_t want_dark;   // what the search looks for: written before the thread starts
static _Atomic uint64_t lend_off;   // where the lent resource holds it

static uint64_t effect_res(uint64_t actor) { return ptr(ptr(ptr(ptr(actor + 0x270) + 0x68) + HOLDER_EFFECT) + INSTANCE_RES); }

static void search_thread(void *arg) {
    (void)arg;
    uint64_t found[16], res = 0;
    int seen = 0;
    for (int t = 0; t < NLENDERS && !res; t++) {
        int n = dbg_find_named(LENDERS[t], found, 16);   // equipment is not of the generic actor class
        seen += n;
        for (int i = 0; i < n && !res; i++) {
            uint64_t r = effect_res(found[i]), words[USER_RES_SPAN / 8];
            if (!r || !plat_mem_read(r, words, sizeof words)) continue;
            for (int k = 0; k < USER_RES_SPAN / 8 && !res; k++)   // the user's name sits inline before it, so its place varies
                if (words[k] == want_dark) res = r, atomic_store(&lend_off, (uint64_t)k * 8);
        }
    }
    static int told;
    if (!told++) rt_log("dark link: %d lending actors found, %s", seen, res ? "one has Dark Link's effects" : "none has Dark Link's effects");
    atomic_store(&dark_res, res);
    atomic_store(&searching, 0);
}

void dark_link_fx_frame(Cpu *c) {
    if (!(RT_V111 || RT_V100) || !n64gfx_dark_remake() || !cfg_bool("OOTPC_DARK_LINK_EFFECTS")) return;
    uint64_t player = dbg_player_actor();
    if (!player || dbg_is_child() != 0) return;   // the child's skeleton is not the enemy's
    uint64_t holder = ptr(ptr(player + 0x270) + 0x68), inst = ptr(holder + HOLDER_EFFECT), res = ptr(inst + INSTANCE_RES);
    if (!holder || !inst || !res) return;
    if (player != S.player) memset(&S, 0, sizeof S), S.player = player;
    if (S.failed) return;
    if (!S.dark) {   // the player's user data, the file it is in and Dark Link's user there
        uint64_t words[USER_RES_SPAN / 8];
        if (!plat_mem_read(res, words, sizeof words)) return;
        for (int i = 0; i < USER_RES_SPAN / 8 && !S.dark; i++)
            if (words[i] > 0x100000000ull && (S.dark = dark_user(words[i]))) S.off = (uint64_t)i * 8;
        if (!S.dark) {
            S.failed = 1;
            rt_log("dark link: Dark Link's effect user not found; his effects stay off");
            return;
        }
    }
    uint64_t now = plat_now_ns(), lend = atomic_load(&dark_res);
    if (lend && ptr(lend + atomic_load(&lend_off)) != S.dark) atomic_store(&dark_res, lend = 0);
    if (!lend) {
        int idle = 0;
        if (now - atomic_load(&search_ns) > SEARCH_EVERY_NS && atomic_compare_exchange_strong(&searching, &idle, 1)) {
            atomic_store(&search_ns, now);
            want_dark = S.dark;
            if (!plat_thread_spawn(search_thread, NULL, "dark-link-fx", 0)) atomic_store(&searching, 0);
        }
        return;
    }
    for (int k = 0; k < NKEYS; k++) {
        if (handle_instance(S.handle[k]) || now - S.tried_ns[k] < RETRY_NS) continue;
        static const char *keyp;    // guest code reads host memory directly
        static uint8_t handles[16];
        static const uint8_t none[8] = {0xff, 0, 0xff, 0xff, 0, 0, 0, 0};
        keyp = KEYS[k];
        memcpy(handles, none, 8), memcpy(handles + 8, none, 8);
        uint64_t *slot = (uint64_t *)(uintptr_t)(inst + INSTANCE_RES);   // the game's memory is the process's own
        *slot = lend;
        rt_call(c, XLINK_EMIT_KEY, 4, holder, (uint64_t)(uintptr_t)&keyp, (uint64_t)(uintptr_t)handles, (uint64_t)0);
        *slot = res;
        memcpy(S.handle[k], handles, 8);
        S.tried_ns[k] = now;
        if (!(S.logged & (1 << k))) {
            S.logged |= 1 << k;
            rt_log("dark link: effect %s %s", KEYS[k], handle_instance(handles) ? "playing" : "started nothing");
        }
    }
}
