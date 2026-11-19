// Change monitor (debug_watch.h). Two helper threads: one reads every flag store of every kind whole every 4 ms (one
// memory read per store, debug_tools.c dbg_flag_store) and compares each entry's value and save copy with their last
// ones; the other counts the loaded actors by name (debug_warp.c dbg_each_actor, about a second a pass) and logs the
// names whose count changed. The flags thread also logs the area and the interface's screen as they change, and the
// event files the game loads come from the file layer (dbg_watch_rom_open). Nothing is left out of the capture file: a flag that changes more than NOISY times only
// stops being shown in the menu's list, so timers do not fill it.
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "debug_tools.h"
#include "debug_watch.h"
#include "platform/platform.h"

#define POLL_NS 4000000ull   // 4 ms: several looks per game frame, so a value held for one frame is seen
#define BINARY_EVERY 25      // Binary flags (up to a few hundred KiB each) are compared every 25th look, 100 ms
#define ACTOR_PAUSE_NS 500000000ull
#define NOISY 24
#define ACTOR_LINES 12   // more names changed in one pass than this: one line in the menu, the names in the file
#define NLOG 256
#define LINE 256
#define NAMES_FILE "debug_flag_names.json"
#define DESC_FILE "flag_descriptions.json"   // next to the game: {"flag name": "what it is", ...}, optional
#define REPORT_MAX 4096   // flags a report keeps
#define REPORT_VALUES 8   // values kept per flag
#define REPORT_PAUSE_NS 5000000000ull   // the report is rewritten at most this often while a capture runs
#define CAPTURE_DIR "flag_captures"   // next to the game: one file per start, changes-YYYYMMDD-HHMMSS.txt

static const char *const kType[DBG_FLAG_ALL] = {"bool", "int", "float", "string", "vec3", "enum",
                                               "uint", "uint64", "string16", "string32", "boolarr", "binary"};

static _Atomic int watch_on, flags_running, actors_running;
static _Atomic int want_reset, want_clear, want_ignore, actors_clear;
static _Atomic int ignored_n;
static _Atomic uint64_t t0;
static char state[96] = "off";

// ---- the log: a ring of lines, newest last, and the file
static PlatMutex log_lock;
static char log_ring[NLOG][LINE];
static int log_n, log_head;   // log_head: where the next line goes
static FILE *log_file;
static char log_path[96];   // the capture file of this start, "" before the first
static char report_path[96];   // its report, the same name ending in .md
static char capture_title[32];   // the start's local date and time, for the report's title
static _Atomic int flag_changes;   // flag lines logged since the start

// ring 0: the file only (the details of a summary line).
static void log_to(int ring, const char *fmt, va_list ap) {
    char line[LINE];
    double t = (double)(plat_now_ns() - atomic_load(&t0)) / 1e9;
    int k = snprintf(line, sizeof line, "%9.3f  ", t);
    vsnprintf(line + k, sizeof line - k, fmt, ap);
    plat_mutex_lock(&log_lock);
    if (ring) {
        memcpy(log_ring[log_head], line, LINE);
        log_head = (log_head + 1) % NLOG;
        if (log_n < NLOG) log_n++;
    }
    if (log_file) fprintf(log_file, "%s\n", line);
    plat_mutex_unlock(&log_lock);
}

static void log_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void log_line(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    log_to(1, fmt, ap);
    va_end(ap);
}

static void log_file_only(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void log_file_only(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    log_to(0, fmt, ap);
    va_end(ap);
}

static void log_flush(void) {
    plat_mutex_lock(&log_lock);
    if (log_file) fflush(log_file);
    plat_mutex_unlock(&log_lock);
}

// ---- events
void dbg_watch_event(const char *fmt, ...) {
    if (!atomic_load(&watch_on)) return;
    char text[LINE - 32];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    log_line("event  %s", text);
}

// The game's event files: Event/EventFlow/<name>.bfevfl.zs (a flow: the steps of a talk, a chest, a door, a cutscene),
// Event/EventSetting/<name> (its actors and camera) and Event/Movie/<name> (a video). Logged by name, without the folder
// and the extensions. A flow is read when the game first needs it, so one used again from memory gives no line.
void dbg_watch_rom_open(const char *path) {
    if (!atomic_load(&watch_on) || !path) return;
    static const struct { const char *dir, *what; } kDirs[] = {
        {"Event/EventFlow/", "flow"}, {"Event/EventSetting/", "setting"}, {"Event/Movie/", "movie"}};
    for (size_t i = 0; i < sizeof kDirs / sizeof *kDirs; i++) {
        const char *at = strstr(path, kDirs[i].dir);
        if (!at) continue;
        char name[96];
        snprintf(name, sizeof name, "%s", at + strlen(kDirs[i].dir));
        char *dot = strchr(name, '.');
        if (dot) *dot = 0;
        dbg_watch_event("%-7s %s", kDirs[i].what, name);
        return;
    }
}

// The area and the interface's screen, looked at by the flags thread every EVENT_EVERY looks.
#define EVENT_EVERY 25   // 100 ms
#define UI_SCREEN_KEY 0x578d41e1u   // the enum flag debug_move.c reads: the screen the interface is on
static void events_poll(int reset) {
    static char area_was[64];
    static uint32_t screen_was;
    static uint32_t keys[12];
    static const char *const kScreens[12] = {"None", "ItemMenu", "EquipmentMenu", "CollectMenu", "SystemMenu", "Save",
                                            "Load", "Option", "Ocarina", "Title", "Loading", "GameOver"};
    if (!keys[0])
        for (int i = 0; i < 12; i++) keys[i] = dbg_flag_key(kScreens[i]);
    if (reset) area_was[0] = 0, screen_was = 0;
    char area[64] = "";
    if (dbg_current_area(area) && area[0] && strcmp(area, area_was)) {
        if (area_was[0]) dbg_watch_event("area    %s (from %s)", area, area_was);
        else dbg_watch_event("area    %s", area);
        snprintf(area_was, sizeof area_was, "%s", area);
    }
    uint32_t screen = 0;
    if (dbg_flag_get_u32(DBG_FLAG_ENUM, UI_SCREEN_KEY, &screen) && screen != screen_was) {
        const char *name = NULL;
        for (int i = 0; i < 12; i++)
            if (keys[i] == screen) name = kScreens[i];
        if (!reset || name) {
            if (name) dbg_watch_event("screen  %s", name);
            else dbg_watch_event("screen  %08x", screen);
        }
        screen_was = screen;
    }
}

int dbg_watch_count(void) {
    plat_mutex_lock_shared(&log_lock);
    int n = log_n;
    plat_mutex_unlock_shared(&log_lock);
    return n;
}

int dbg_watch_line(int i, char *out, size_t n) {
    int ok = 0;
    plat_mutex_lock_shared(&log_lock);
    if (i >= 0 && i < log_n) {
        snprintf(out, n, "%s", log_ring[(log_head - 1 - i + NLOG) % NLOG]);
        ok = 1;
    }
    plat_mutex_unlock_shared(&log_lock);
    return ok;
}

// ---- flag names: debug_flag_names.json (tools/debug_flag_names.py) maps "KEY": [kind, name, where]
// Built whole by the first caller, then published: names before nnames, so a reader that sees a count sees the array.
static struct Name { uint32_t key; char *name, *where; } *names;   // where: the game file that sets it, or "built"
static _Atomic int nnames;
static _Atomic int names_loaded;

static int name_cmp(const void *a, const void *b) {
    uint32_t x = ((const struct Name *)a)->key, y = ((const struct Name *)b)->key;
    return x < y ? -1 : x > y;
}

// Reads a JSON string starting at the quote *p; leaves *p past it. Escapes are kept as the character after the backslash.
static int json_str(const char **p, char *out, size_t n) {
    const char *s = *p;
    if (*s != '"') return 0;
    size_t k = 0;
    for (s++; *s && *s != '"'; s++) {
        if (*s == '\\' && s[1]) s++;
        if (k + 1 < n) out[k++] = *s;
    }
    if (*s != '"') return 0;
    out[k] = 0;
    *p = s + 1;
    return 1;
}

static const char *skip_ws(const char *s) {
    while (*s == ' ' || *s == '\n' || *s == '\r' || *s == '\t') s++;
    return s;
}

static void names_load(void) {
    if (atomic_exchange(&names_loaded, 1)) return;
    FILE *f = fopen(NAMES_FILE, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *text = len > 0 ? malloc((size_t)len + 1) : NULL;
    if (!text || fread(text, 1, (size_t)len, f) != (size_t)len) {
        free(text);
        fclose(f);
        return;
    }
    fclose(f);
    text[len] = 0;
    int cap = 0, n = 0;
    struct Name *nm = NULL;
    for (const char *s = text; (s = strchr(s, '"'));) {
        char key[16], kind[32], name[160], where[200];
        if (!json_str(&s, key, sizeof key)) break;
        const char *q = skip_ws(s);
        if (strlen(key) != 8 || strspn(key, "0123456789abcdefABCDEF") != 8 || *q != ':') continue;
        q = skip_ws(q + 1);
        if (*q != '[') continue;
        q = skip_ws(q + 1);
        if (!json_str(&q, kind, sizeof kind)) continue;
        q = skip_ws(q);
        if (*q != ',') continue;
        q = skip_ws(q + 1);
        if (!json_str(&q, name, sizeof name)) continue;
        q = skip_ws(q);
        where[0] = 0;
        if (*q == ',') {
            q = skip_ws(q + 1);
            if (!json_str(&q, where, sizeof where)) where[0] = 0;
        }
        s = q;
        if (n == cap) {
            cap = cap ? cap * 2 : 4096;
            struct Name *g = realloc(nm, (size_t)cap * sizeof *nm);
            if (!g) break;
            nm = g;
        }
        nm[n].key = (uint32_t)strtoul(key, NULL, 16);
        nm[n].name = strdup(name);
        nm[n].where = strdup(where);
        if (nm[n].name && nm[n].where) n++;
        else free(nm[n].name), free(nm[n].where);
    }
    free(text);
    if (nm) qsort(nm, (size_t)n, sizeof *nm, name_cmp);
    names = nm;
    atomic_store(&nnames, n);
}

const char *dbg_flag_name(uint32_t key) {
    names_load();
    int n = atomic_load(&nnames);
    struct Name want = {key, NULL, NULL}, *f = n ? bsearch(&want, names, (size_t)n, sizeof *names, name_cmp) : NULL;
    return f ? f->name : NULL;
}

const char *dbg_flag_where(uint32_t key) {
    names_load();
    int n = atomic_load(&nnames);
    struct Name want = {key, NULL, NULL}, *f = n ? bsearch(&want, names, (size_t)n, sizeof *names, name_cmp) : NULL;
    return f ? f->where : NULL;
}

int dbg_watch_names(void) { return nnames; }

static const char *name_of(uint32_t key, char buf[16]) {
    const char *n = dbg_flag_name(key);
    if (n) return n;
    snprintf(buf, 16, "key %08x", key);
    return buf;
}

// ---- flags
// Every store of every kind (debug_tools.h, up to DBG_FLAG_ALL). For each entry both the value and the copy the save
// files get are kept: a copy that changes on its own (not along with its value) is logged as "(saved copy)", which is
// how a save that takes the values at one moment shows. A BoolArray's or Binary's value lies behind a pointer, so its
// bytes are read and kept too (a Binary, up to a few hundred KiB, every BINARY_EVERY looks only).
static struct Snap {
    uint64_t base;
    int count;
    uint32_t size, off;
    DbgFlagLayout lay;
    uint8_t *raw;      // the store as last read
    uint8_t *val;      // inline: each entry's value and its save copy, 2 * width bytes per entry
    uint8_t **data;    // indirect: each entry's value bytes and save copy bytes (2 per entry), NULL when none
    uint32_t *len;     // indirect: their lengths in bytes
    uint32_t *key;
    uint8_t *muted;    // left out by "Ignore what has changed so far"
    uint32_t *changes; // since the baseline or the last Clear
} snaps[DBG_FLAG_ALL];

static void snap_free(struct Snap *s) {
    if (s->data)
        for (int i = 0; i < s->count * 2; i++) free(s->data[i]);
    free(s->raw), free(s->val), free(s->data), free(s->len), free(s->key), free(s->muted), free(s->changes);
    memset(s, 0, sizeof *s);
}

static void value_text(int t, const uint8_t *v, char *out, size_t n) {
    uint32_t u;
    uint64_t q;
    float f[3];
    char s[65];
    switch (t) {
    case DBG_FLAG_BOOL: snprintf(out, n, "%u", v[0]); break;
    case DBG_FLAG_INT: memcpy(&u, v, 4), snprintf(out, n, "%d", (int32_t)u); break;
    case DBG_FLAG_UINT: memcpy(&u, v, 4), snprintf(out, n, "%u", u); break;
    case DBG_FLAG_UINT64: memcpy(&q, v, 8), snprintf(out, n, "%llu (0x%016llx)", (unsigned long long)q, (unsigned long long)q); break;
    case DBG_FLAG_FLOAT: memcpy(f, v, 4), snprintf(out, n, "%.9g", f[0]); break;
    case DBG_FLAG_ENUM: memcpy(&u, v, 4), snprintf(out, n, "%08x", u); break;
    case DBG_FLAG_VEC3: memcpy(f, v, 12), snprintf(out, n, "(%.2f, %.2f, %.2f)", f[0], f[1], f[2]); break;
    default: {   // the strings: 64, 32 or 16 characters
        uint32_t w = t == DBG_FLAG_STRING16 ? 16 : t == DBG_FLAG_STRING32 ? 32 : 64;
        memcpy(s, v, w);
        s[w] = 0;
        snprintf(out, n, "\"%s\"", s);
    }
    }
}

// The bytes an indirect entry's value (copy 0) or save copy (copy 1) points at: its address and length, 0 when none.
static uint32_t indirect_at(int t, const struct Snap *s, const uint8_t *entry, int copy, uint64_t *ptr) {
    uint32_t at = s->off + (copy ? s->lay.copy : 0), n;
    memcpy(&n, entry + at, 4);
    memcpy(ptr, entry + at + s->lay.ptr, 8);
    if (t == DBG_FLAG_BOOLARRAY) n = (n + 7) / 8;   // bits, packed
    if (!*ptr || n > (4u << 20)) return 0;
    return n;
}

// Reads indirect entry i's two blocks into the snapshot; 1 when either differs from what was kept.
static int indirect_take(int t, struct Snap *s, int i, const uint8_t *entry, uint8_t *old[2], uint32_t oldn[2]) {
    int changed = 0;
    for (int c = 0; c < 2; c++) {
        uint64_t ptr;
        uint32_t n = indirect_at(t, s, entry, c, &ptr);
        uint8_t *buf = n ? malloc(n) : NULL;
        if (buf && !plat_mem_read(ptr, buf, n)) free(buf), buf = NULL, n = 0;
        if (old) {
            old[c] = s->data[i * 2 + c], oldn[c] = s->len[i * 2 + c];
            changed |= oldn[c] != n || (n && (!old[c] || !buf || memcmp(old[c], buf, n)));
        } else {
            free(s->data[i * 2 + c]);
        }
        s->data[i * 2 + c] = buf, s->len[i * 2 + c] = n;
    }
    return changed;
}

// Reads store t whole and takes the values out of it. A store that moved or was not there is taken as the new
// baseline: returns 2 then, 1 when read as before, 0 when unreadable.
static int snap_read(int t) {
    struct Snap *s = &snaps[t];
    uint64_t base;
    int count;
    uint32_t size, off;
    if (!dbg_flag_store(t, &base, &count, &size, &off) || count <= 0) return 0;
    int fresh = base != s->base || count != s->count;
    if (fresh) {
        snap_free(s);
        if (!dbg_flag_layout(t, &s->lay)) return 0;
        s->raw = malloc((size_t)count * size);
        s->val = malloc((size_t)count * 2 * (s->lay.width ? s->lay.width : 1));
        s->data = s->lay.indirect ? calloc((size_t)count * 2, sizeof *s->data) : NULL;
        s->len = s->lay.indirect ? calloc((size_t)count * 2, sizeof *s->len) : NULL;
        s->key = malloc((size_t)count * sizeof *s->key);
        s->muted = calloc((size_t)count, 1);
        s->changes = calloc((size_t)count, sizeof *s->changes);
        if (!s->raw || !s->val || !s->key || !s->muted || !s->changes || (s->lay.indirect && (!s->data || !s->len))) {
            snap_free(s);
            return 0;
        }
        s->base = base, s->count = count, s->size = size, s->off = off;
        dbg_flag_store_keys(t, s->key, count);
    }
    if (!plat_mem_read(base, s->raw, (size_t)count * size)) return 0;
    return fresh ? 2 : 1;
}

static void snap_take(int t) {
    struct Snap *s = &snaps[t];
    uint32_t w = s->lay.width;
    for (int i = 0; i < s->count; i++) {
        const uint8_t *e = s->raw + (size_t)i * s->size;
        if (s->lay.indirect) {
            indirect_take(t, s, i, e, NULL, NULL);
        } else {
            memcpy(s->val + (size_t)i * 2 * w, e + s->off, w);
            memcpy(s->val + (size_t)i * 2 * w + w, e + s->off + s->lay.copy, w);
        }
    }
}

// The report (below) of the capture running: kept by the flags thread.
static int seen_dirty;      // something new since the report was last written
static double rep_len_s;    // the capture's length so far
static void seen_change(int t, uint32_t key, int saved, const char *was, const char *now);
static void report_start(void);
static void report_write(void);

// One change of entry i: to the list and the file, or after NOISY changes to the file only (the list stays readable;
// the capture keeps everything). saved: the change is of the save copy.
static void flag_logged(int t, struct Snap *s, int i, int saved, const char *a, const char *b) {
    char nb[16];
    const char *nm = name_of(s->key[i], nb);
    if (s->changes[i] < 0xffffffffu) s->changes[i]++;
    if (s->changes[i] == NOISY + 1)
        log_line("%-8s %s changes all the time: logged in the capture file only from now on", kType[t], nm);
    if (s->changes[i] > NOISY) log_file_only("%-8s %s%s  %s -> %s", kType[t], nm, saved ? " (saved copy)" : "", a, b);
    else log_line("%-8s %s%s  %s -> %s", kType[t], nm, saved ? " (saved copy)" : "", a, b);
    atomic_fetch_add(&flag_changes, 1);
    seen_change(t, s->key[i], saved, a, b);
}

// What changed between two blocks of an indirect entry, as text: the bits of a BoolArray, the bytes of a Binary.
static void indirect_change(int t, const uint8_t *was, uint32_t wn, const uint8_t *now, uint32_t nn, char *a, size_t an,
                            char *b, size_t bn) {
    if (t == DBG_FLAG_BOOLARRAY) {
        // every bit that differs, as [index]=value, up to what fits
        size_t ka = 0, kb = 0;
        uint32_t n = wn > nn ? wn : nn;
        a[0] = b[0] = 0;
        for (uint32_t bit = 0; bit < n * 8 && ka + 16 < an && kb + 16 < bn; bit++) {
            int x = bit / 8 < wn && was && (was[bit / 8] >> (bit % 8) & 1);
            int y = bit / 8 < nn && now && (now[bit / 8] >> (bit % 8) & 1);
            if (x == y) continue;
            ka += (size_t)snprintf(a + ka, an - ka, "%s[%u]=%d", ka ? " " : "", bit, x);
            kb += (size_t)snprintf(b + kb, bn - kb, "%s[%u]=%d", kb ? " " : "", bit, y);
        }
        return;
    }
    uint32_t n = wn < nn ? wn : nn, first = 0, last = 0, diff = 0;
    for (uint32_t k = 0; was && now && k < n; k++)
        if (was[k] != now[k]) {
            if (!diff++) first = k;
            last = k;
        }
    snprintf(a, an, "%u bytes", wn);
    if (wn != nn) snprintf(b, bn, "%u bytes (size changed)", nn);
    else snprintf(b, bn, "%u bytes differ in 0x%x..0x%x", diff, first, last);
}

static void snap_compare(int t, int binary_turn) {
    struct Snap *s = &snaps[t];
    uint32_t w = s->lay.width;
    char a[96], b[96];
    if (t == DBG_FLAG_BINARY && !binary_turn) return;
    for (int i = 0; i < s->count; i++) {
        const uint8_t *e = s->raw + (size_t)i * s->size;
        if (s->lay.indirect) {
            uint8_t *old[2];
            uint32_t oldn[2];
            if (!indirect_take(t, s, i, e, old, oldn)) {
                free(old[0]), free(old[1]);
                continue;
            }
            for (int c = 0; c < 2 && !s->muted[i]; c++) {
                const uint8_t *now = s->data[i * 2 + c];
                uint32_t nn = s->len[i * 2 + c];
                if (oldn[c] == nn && (!nn || (old[c] && now && !memcmp(old[c], now, nn)))) continue;
                // the save copy only when it does not just follow its value
                if (c == 1 && s->len[i * 2] == nn && nn && s->data[i * 2] && now && !memcmp(s->data[i * 2], now, nn) &&
                    !(oldn[0] == s->len[i * 2] && old[0] && !memcmp(old[0], s->data[i * 2], oldn[0])))
                    continue;
                indirect_change(t, old[c], oldn[c], now, nn, a, sizeof a, b, sizeof b);
                flag_logged(t, s, i, c, a, b);
            }
            free(old[0]), free(old[1]);
            continue;
        }
        const uint8_t *now = e + s->off, *now_copy = e + s->off + s->lay.copy;
        uint8_t *was = s->val + (size_t)i * 2 * w, *was_copy = was + w;
        int vchanged = memcmp(now, was, w) != 0, cchanged = memcmp(now_copy, was_copy, w) != 0;
        if (!vchanged && !cchanged) continue;
        if (!s->muted[i]) {
            if (vchanged) {
                value_text(t, was, a, sizeof a);
                value_text(t, now, b, sizeof b);
                flag_logged(t, s, i, 0, a, b);
            }
            // the save copy only when it does not just follow its value
            if (cchanged && (!vchanged || memcmp(now_copy, now, w))) {
                value_text(t, was_copy, a, sizeof a);
                value_text(t, now_copy, b, sizeof b);
                flag_logged(t, s, i, 1, a, b);
            }
        }
        memcpy(was, now, w);
        memcpy(was_copy, now_copy, w);
    }
}

static void flags_thread(void *arg) {
    (void)arg;
    int found[DBG_FLAG_ALL] = {0};
    uint64_t written = 0, looks = 0;
    int events_fresh = 1;
again:
    while (atomic_load(&watch_on)) {
        if (atomic_exchange(&want_reset, 0)) {
            for (int t = 0; t < DBG_FLAG_ALL; t++) snaps[t].base = 0, snaps[t].count = 0, found[t] = 0;
            atomic_store(&ignored_n, 0);
            report_start();
            written = 0;
            events_fresh = 1;   // the first look of a capture names the area and screen it starts in
        }
        int clear = atomic_exchange(&want_clear, 0), ignore = atomic_exchange(&want_ignore, 0), any = 0;
        int binary_turn = looks++ % BINARY_EVERY == 0;
        if (looks % EVENT_EVERY == 1 || events_fresh) events_poll(events_fresh), events_fresh = 0;
        for (int t = 0; t < DBG_FLAG_ALL; t++) {
            struct Snap *s = &snaps[t];
            if (ignore)
                for (int i = 0; i < s->count; i++)
                    if (s->changes[i] && !s->muted[i]) s->muted[i] = 1, atomic_fetch_add(&ignored_n, 1);
            if (clear || ignore)
                for (int i = 0; i < s->count; i++) s->changes[i] = 0;
            int r = snap_read(t);
            if (!r) continue;
            any = 1;
            if (r == 2 || clear) {
                if (r == 2 && !found[t]) log_line("store    %s: %d flags watched", kType[t], s->count);
                if (r == 2 && found[t]++) log_line("%-8s the store moved (the game loaded again): new baseline", kType[t]);
                snap_take(t);
            } else {
                snap_compare(t, binary_turn);
            }
        }
        snprintf(state, sizeof state, any ? "Watching" : dbg_flags_locating() ? "Looking for the game's flags" : "Flags not found yet (load a save)");
        log_flush();
        uint64_t now = plat_now_ns();
        rep_len_s = (double)(now - atomic_load(&t0)) / 1e9;
        if (seen_dirty && now - written >= REPORT_PAUSE_NS) report_write(), written = now;
        plat_sleep_ns(POLL_NS);
    }
    report_write();
    atomic_store(&flags_running, 0);
    // Started again while this thread was finishing: dbg_watch_set found it still running and started none, so it goes on.
    int zero = 0;
    if (atomic_load(&watch_on) && atomic_compare_exchange_strong(&flags_running, &zero, 1)) goto again;
}

// ---- the report: a Markdown summary of a capture, one table row per flag that changed
// Kept by the flags thread alone: what each flag that changed did since the start, written to the capture's .md file
// every few seconds while something is new, and once more when the capture ends.
static struct Seen {
    uint32_t key;
    uint8_t type, saved, more, nvals;   // saved: the row is the flag's save copy
    int changes;
    double first_s;
    char vals[REPORT_VALUES][72];
} *seen;
static int nseen;
static char rep_md[96], rep_txt[96], rep_title[32];

// Descriptions: flag_descriptions.json, read again at each start so an edited file counts from the next capture.
static struct Desc { char *name, *text; } *descs;
static int ndescs;

static void descs_load(void) {
    for (int i = 0; i < ndescs; i++) free(descs[i].name), free(descs[i].text);
    free(descs), descs = NULL, ndescs = 0;
    FILE *f = fopen(DESC_FILE, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *text = len > 0 ? malloc((size_t)len + 1) : NULL;
    if (!text || fread(text, 1, (size_t)len, f) != (size_t)len) {
        free(text);
        fclose(f);
        return;
    }
    fclose(f);
    text[len] = 0;
    int cap = 0;
    static char name[200], desc[2048];
    for (const char *p = text; (p = strchr(p, '"'));) {
        if (!json_str(&p, name, sizeof name)) break;
        const char *q = skip_ws(p);
        if (*q != ':') continue;
        q = skip_ws(q + 1);
        if (!json_str(&q, desc, sizeof desc)) continue;
        p = q;
        if (ndescs == cap) {
            cap = cap ? cap * 2 : 256;
            struct Desc *g = realloc(descs, (size_t)cap * sizeof *descs);
            if (!g) break;
            descs = g;
        }
        descs[ndescs].name = strdup(name);
        descs[ndescs].text = strdup(desc);
        if (descs[ndescs].name && descs[ndescs].text) ndescs++;
        else free(descs[ndescs].name), free(descs[ndescs].text);
    }
    free(text);
}

static const char *desc_of(const char *name) {
    for (int i = 0; i < ndescs; i++)
        if (!strcmp(descs[i].name, name)) return descs[i].text;
    return NULL;
}

static struct Seen *seen_get(int t, uint32_t key, int saved) {
    for (int i = 0; i < nseen; i++)
        if (seen[i].key == key && seen[i].type == t && seen[i].saved == saved) return &seen[i];
    if (!seen) seen = malloc(REPORT_MAX * sizeof *seen);
    if (!seen || nseen >= REPORT_MAX) return NULL;
    struct Seen *e = &seen[nseen++];
    memset(e, 0, sizeof *e);
    e->key = key, e->type = (uint8_t)t, e->saved = (uint8_t)saved;
    e->first_s = (double)(plat_now_ns() - atomic_load(&t0)) / 1e9;
    return e;
}

static void seen_value(struct Seen *e, const char *v) {
    for (int i = 0; i < e->nvals; i++)
        if (!strcmp(e->vals[i], v)) return;
    if (e->nvals < REPORT_VALUES) snprintf(e->vals[e->nvals++], sizeof e->vals[0], "%s", v);
    else e->more = 1;
}

static void seen_change(int t, uint32_t key, int saved, const char *was, const char *now) {
    struct Seen *e = seen_get(t, key, saved);
    if (!e) return;
    e->changes++;
    seen_value(e, was);
    seen_value(e, now);
    seen_dirty = 1;
}

// The report's group of a flag: the start of its name up to the first '.' or '_', without trailing digits, capitalised;
// the flags without a name go last, together.
static void group_of(const char *name, char *out, size_t n) {
    if (!strncmp(name, "key ", 4)) {
        snprintf(out, n, "Unnamed flags (known only by their key)");
        return;
    }
    size_t k = strcspn(name, "._");
    while (k > 0 && name[k - 1] >= '0' && name[k - 1] <= '9') k--;
    if (!k) k = strlen(name);
    snprintf(out, n, "%.*s", (int)k, name);
    if (out[0] >= 'a' && out[0] <= 'z') out[0] = (char)(out[0] - 'a' + 'A');
}

static struct Row { const char *name; char shown[176], group[64]; int i; } *rows;   // name: the flag's; shown: the row's

static int row_order(const void *a, const void *b) {
    const struct Row *x = a, *y = b;
    int ux = !strncmp(x->name, "key ", 4), uy = !strncmp(y->name, "key ", 4);
    if (ux != uy) return ux - uy;
    int c = strcasecmp(x->group, y->group);
    return c ? c : strcasecmp(x->shown, y->shown);
}

// Text for a table cell: '|' would end the cell and '`' the code span, line breaks the row.
static void cell(FILE *f, const char *s, int code) {
    for (; *s; s++) {
        char c = *s;
        if (c == '|') fputs(code ? "/" : "\\|", f);
        else if (c == '`' && code) fputc('\'', f);
        else if (c == '\n' || c == '\r' || c == '\t') fputc(' ', f);
        else fputc(c, f);
    }
}

// The link target of a heading, as Markdown viewers make it: lower case, spaces as '-', other punctuation dropped.
static void anchor(FILE *f, const char *g) {
    for (; *g; g++) {
        char c = *g;
        if (c >= 'A' && c <= 'Z') fputc(c - 'A' + 'a', f);
        else if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_') fputc(c, f);
        else if (c == ' ') fputc('-', f);
    }
}

static void report_write(void) {
    if (!rep_md[0]) return;
    FILE *f = fopen(rep_md, "w");
    if (!f) return;
    static char nb[REPORT_MAX][16];
    struct Row *r = realloc(rows, (size_t)(nseen ? nseen : 1) * sizeof *rows);
    if (!r) {
        fclose(f);
        return;
    }
    rows = r;
    int described = 0, total = 0;
    for (int i = 0; i < nseen; i++) {
        rows[i].name = name_of(seen[i].key, nb[i]);
        snprintf(rows[i].shown, sizeof rows[i].shown, "%s%s", rows[i].name, seen[i].saved ? " (saved copy)" : "");
        rows[i].i = i;
        group_of(rows[i].name, rows[i].group, sizeof rows[i].group);
        described += desc_of(rows[i].name) != NULL;
        total += seen[i].changes;
    }
    qsort(rows, (size_t)nseen, sizeof *rows, row_order);
    const char *txt = strrchr(rep_txt, '/');
    txt = txt ? txt + 1 : rep_txt;
    fprintf(f, "# Flag capture %s\n\n", rep_title);
    fprintf(f, "Every flag of the game that changed while this capture ran. The raw log, with the time of each change and "
               "the actors that came and went, is `%s` beside this file.\n\n", txt);
    fprintf(f, "- Length: %.1f s\n- Flags that changed: %d (%d changes)\n- Described: %d of %d (from `" DESC_FILE "` "
               "next to the game)\n\n", rep_len_s, nseen, total, described, nseen);
    fputs("Type: the store the flag lives in. Changes: how many times it changed. At: the seconds into the capture of its "
          "first change. A row marked (saved copy) is the copy of the flag the save files get, where it changed on its "
          "own, not along with the flag: a save taking the values at that moment. A boolarr row lists the elements "
          "that changed; a binary row how many bytes changed and where.\n\n", f);
    if (!nseen) fputs("No flag changed.\n", f);
    else {
        fputs("## Groups\n\n", f);
        for (int i = 0; i < nseen;) {
            int j = i;
            while (j < nseen && !strcmp(rows[j].group, rows[i].group)) j++;
            fprintf(f, "- [%s](#", rows[i].group);
            anchor(f, rows[i].group);
            fprintf(f, ") (%d)\n", j - i);
            i = j;
        }
    }
    for (int i = 0; i < nseen; i++) {
        if (!i || strcmp(rows[i].group, rows[i - 1].group))
            fprintf(f, "\n## %s\n\n| Flag | Type | Changes | At (s) | Values seen | What it is |\n|---|---|---|---|---|---|\n",
                    rows[i].group);
        const struct Seen *e = &seen[rows[i].i];
        fputs("| `", f);
        cell(f, rows[i].shown, 1);
        fprintf(f, "` | %s | %d | %.3f | ", kType[e->type], e->changes, e->first_s);
        for (int v = 0; v < e->nvals; v++) {
            fputs(v ? ", `" : "`", f);
            cell(f, e->vals[v], 1);
            fputc('`', f);
        }
        if (e->more) fputs(" ...", f);
        fputs(" | ", f);
        const char *d = desc_of(rows[i].name), *w = dbg_flag_where(e->key);
        if (d) cell(f, d, 0);
        else fputs("*(not described yet)*", f);
        if (w && w[0] && strcmp(w, "built")) {
            fputs("<br>Used by: `", f);
            cell(f, w, 1);
            fputc('`', f);
        }
        fputs(" |\n", f);
    }
    fclose(f);
    seen_dirty = 0;
}

// A new capture: the last one's report is finished first, then the new one starts empty.
static void report_start(void) {
    if (rep_md[0] && seen_dirty) report_write();
    nseen = 0;
    seen_dirty = 1;
    plat_mutex_lock(&log_lock);
    snprintf(rep_md, sizeof rep_md, "%s", report_path);
    snprintf(rep_txt, sizeof rep_txt, "%s", log_path);
    snprintf(rep_title, sizeof rep_title, "%s", capture_title);
    plat_mutex_unlock(&log_lock);
    descs_load();
}

// ---- actors, counted by name
struct ActorCount {
    char name[80];
    int n;
};
#define NACTOR_NAMES 2048
static struct ActorCount *act_now, *act_was;
static int act_now_n, act_was_n;

static int actor_count(uint64_t actor, const char *name, void *ctx) {
    (void)actor, (void)ctx;
    for (int i = 0; i < act_now_n; i++)
        if (!strcmp(act_now[i].name, name)) return act_now[i].n++, 1;
    if (act_now_n < NACTOR_NAMES) {
        snprintf(act_now[act_now_n].name, sizeof act_now[0].name, "%s", name);
        act_now[act_now_n++].n = 1;
    }
    return atomic_load(&watch_on);
}

static int count_in(const struct ActorCount *a, int n, const char *name) {
    for (int i = 0; i < n; i++)
        if (!strcmp(a[i].name, name)) return a[i].n;
    return 0;
}

static void actors_thread(void *arg) {
    (void)arg;
    if (!act_now) act_now = malloc(NACTOR_NAMES * sizeof *act_now);
    if (!act_was) act_was = malloc(NACTOR_NAMES * sizeof *act_was);
    int have = 0;
    while (act_now && act_was && atomic_load(&watch_on)) {
        if (atomic_exchange(&actors_clear, 0)) have = 0;
        act_now_n = 0;
        dbg_each_actor(actor_count, NULL);
        if (!atomic_load(&watch_on)) break;
        if (have && act_now_n) {
            // A pass with many names changed (an area loading) gets one line in the menu, the names in the file.
            int changed = 0;
            for (int i = 0; i < act_now_n; i++) changed += count_in(act_was, act_was_n, act_now[i].name) != act_now[i].n;
            for (int i = 0; i < act_was_n; i++) changed += !count_in(act_now, act_now_n, act_was[i].name);
            void (*put)(const char *, ...) = changed > ACTOR_LINES ? log_file_only : log_line;
            if (changed > ACTOR_LINES) log_line("actor  %d kinds of actor changed (an area loading?): the list is in the capture file", changed);
            for (int i = 0; i < act_now_n; i++) {
                int was = count_in(act_was, act_was_n, act_now[i].name);
                if (was != act_now[i].n) put("actor  %+d %s (now %d)", act_now[i].n - was, act_now[i].name, act_now[i].n);
            }
            for (int i = 0; i < act_was_n; i++)
                if (!count_in(act_now, act_now_n, act_was[i].name)) put("actor  -%d %s (now 0)", act_was[i].n, act_was[i].name);
        }
        if (act_now_n) {
            memcpy(act_was, act_now, (size_t)act_now_n * sizeof *act_now);
            act_was_n = act_now_n;
            have = 1;
        }
        plat_sleep_ns(ACTOR_PAUSE_NS);
    }
    atomic_store(&actors_running, 0);
}

// ---- control
void dbg_watch_set(int on) {
    if (!on) {
        atomic_store(&watch_on, 0);
        snprintf(state, sizeof state, "off");
        plat_mutex_lock(&log_lock);
        if (log_file) fclose(log_file), log_file = NULL;
        plat_mutex_unlock(&log_lock);
        return;
    }
    if (!dbg_available() || atomic_exchange(&watch_on, 1)) return;
    names_load();
    atomic_store(&t0, plat_now_ns());
    // A new file in the captures folder for each start, so a start never overwrites an earlier capture.
    struct tm tm;
    plat_local_time(&tm);
    char name[48];
    strftime(name, sizeof name, "changes-%Y%m%d-%H%M%S", &tm);
    plat_file_mkdir_p(CAPTURE_DIR);
    plat_mutex_lock(&log_lock);
    log_n = log_head = 0;
    if (log_file) fclose(log_file);
    snprintf(log_path, sizeof log_path, CAPTURE_DIR "/%s.txt", name);
    snprintf(report_path, sizeof report_path, CAPTURE_DIR "/%s.md", name);
    strftime(capture_title, sizeof capture_title, "%Y-%m-%d %H:%M:%S", &tm);
    log_file = fopen(log_path, "w");
    plat_mutex_unlock(&log_lock);
    atomic_store(&flag_changes, 0);
    atomic_store(&want_reset, 1);
    atomic_store(&actors_clear, 1);
    snprintf(state, sizeof state, "Starting");
    log_line("watching: %d flag names known%s", nnames, nnames ? "" : " (no " NAMES_FILE " next to the game: keys only)");
    int zero = 0;
    if (atomic_compare_exchange_strong(&flags_running, &zero, 1) && !plat_thread_spawn(flags_thread, NULL, "debug-watch", 0))
        atomic_store(&flags_running, 0);
    zero = 0;
    if (atomic_compare_exchange_strong(&actors_running, &zero, 1) && !plat_thread_spawn(actors_thread, NULL, "debug-watch-actors", 0))
        atomic_store(&actors_running, 0);
}

int dbg_watch_on(void) { return atomic_load(&watch_on); }

void dbg_watch_ignore_changed(void) { atomic_store(&want_ignore, 1); }

void dbg_watch_clear(void) {
    plat_mutex_lock(&log_lock);
    log_n = log_head = 0;
    plat_mutex_unlock(&log_lock);
    atomic_store(&want_clear, 1);
    atomic_store(&actors_clear, 1);
}

int dbg_watch_ignored(void) { return atomic_load(&ignored_n); }

const char *dbg_watch_file(void) { return log_path; }

const char *dbg_watch_report(void) { return report_path; }

int dbg_watch_changes(void) { return atomic_load(&flag_changes); }

const char *dbg_watch_state(void) { return state; }
