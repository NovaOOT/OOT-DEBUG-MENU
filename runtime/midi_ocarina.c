// A MIDI file played on the ocarina (midi_ocarina.h). The file is read into notes (formats 0 and 1, every tempo change,
// the percussion channel left out), one part of it becomes a melody of single notes, and each note of the melody is
// moved onto a note the ocarina can play. While the ocarina is out (the UI screen flag reads Ocarina, as in
// debug_move.c) and the game takes input, midi_oc_input ORs each note's buttons into the game's input from the note's
// start to its end, with a short release before the next note so that the game sees every press.
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "debug_tools.h"
#include "host.h"
#include "input_map.h"
#include "midi_ocarina.h"
#include "platform/platform.h"

// The UI screen the game is on (debug_move.c dbg_fly_game_mask): Ocarina while the ocarina is out.
#define UI_SCREEN_KEY 0x578d41e1u
#define UI_SCREEN_OCARINA 0x6a586f83u   // dbg_flag_key("Ocarina")

// ---- the ocarina's notes
// The game's song book (GameParameter/MelodyScoreBook) names its five notes D, F, A, B and the D an octave above:
// D4 F4 A4 B4 D5 as MIDI note numbers (the ocarina sounds them an octave higher). Each has a button; the left stick
// bends it (below). The buttons are the ones the game's own note prompts show (OcarinaButtonA/X/Y/L/R_00, tools/pad_prompts.py;
// B puts the ocarina away). Which button plays which note is read from the songs' buttons in the game's pause menu
// (1.1.1): Zelda's Lullaby (B D A) is Y X A, the Song of Time (A D F) is A L R.
static const struct { uint8_t pitch; int bit; } BASE[5] = {
    {62, B_L}, {65, B_R}, {69, B_A}, {71, B_Y}, {74, B_X},
};
// With the ocarina out the left stick bends the note: up raises it, down lowers it, in proportion to the tilt, a full
// tilt by two semitones (measured from the game's sound, 1.1.1: a quarter tilt +0.5, half +1, full +2, the same down).
// So a semitone is half a tilt, and two notes a whole tone apart reach every pitch between them: C4 to E5 all play.
#define BEND_PER_SEMITONE 16384

typedef struct { uint8_t pitch; int8_t base, mod; } Playable;   // mod: semitones of bend, -2 to +2
static Playable playable[32];
static int playable_n, play_lo, play_hi;

static void playable_build(void) {
    if (playable_n) return;
    static const int8_t order[5] = {0, 1, -1, 2, -2};   // the least bend first: a pitch two notes reach bends less
    for (int m = 0; m < 5; m++) {
        int mod = order[m];
        for (int b = 0; b < 5; b++) {
            int p = BASE[b].pitch + mod, seen = 0;
            for (int i = 0; i < playable_n; i++) seen |= playable[i].pitch == p;
            if (!seen) playable[playable_n++] = (Playable){(uint8_t)p, (int8_t)b, (int8_t)mod};
        }
    }
    play_lo = 127, play_hi = 0;
    for (int i = 0; i < playable_n; i++) {
        if (playable[i].pitch < play_lo) play_lo = playable[i].pitch;
        if (playable[i].pitch > play_hi) play_hi = playable[i].pitch;
    }
}

static int playable_of(int p) {
    for (int i = 0; i < playable_n; i++)
        if (playable[i].pitch == p) return i;
    return -1;
}

// ---- the file
typedef struct { int64_t on, off; uint8_t pitch; uint16_t part; } Note;   // microseconds of the file
typedef struct { uint16_t track; uint8_t chan; int count; double mean; char name[48]; char label[96]; } Part;

typedef struct {
    Note *notes; int n;          // every note, by start
    Part *parts; int nparts;
    int auto_part;
    char file[256];
} Song;

typedef struct { int64_t on, off; uint8_t pitch; int idx; } Played;   // idx: playable[] entry

static PlatMutex lock = PLAT_MUTEX_INIT;
static Song song;
static Played *mel;
static int mel_n, mel_bent, part_sel = -2, transpose_auto, transpose_user, speed = 100;
static int state = MIDI_OC_IDLE;
static int64_t clock_us;          // file time played so far
static uint64_t last_poll_ns, ocarina_since_ns, play_since_ns, away_since_ns;
static int cur_note = -1, logged_note = -1;

static uint32_t be32(const uint8_t *p) { return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }

static int varlen(const uint8_t *p, const uint8_t *end, uint32_t *v, const uint8_t **next) {
    uint32_t x = 0;
    for (int i = 0; i < 4 && p < end; i++) {
        x = x << 7 | (*p & 0x7f);
        if (!(*p++ & 0x80)) { *v = x, *next = p; return 1; }
    }
    return 0;
}

typedef struct { uint64_t tick; uint32_t uspq; } Tempo;
typedef struct { uint64_t on, off; uint8_t pitch; uint16_t part; } TickNote;

typedef struct {
    TickNote *v; int n, cap;
    Tempo *t; int nt, capt;
    Part *parts; int nparts, capp;
} Build;

static int grow(void **p, int *cap, int need, size_t size) {
    if (need <= *cap) return 1;
    int ncap = *cap ? *cap * 2 : 256;
    while (ncap < need) ncap *= 2;
    void *q = realloc(*p, (size_t)ncap * size);
    if (!q) return 0;
    *p = q, *cap = ncap;
    return 1;
}

static int part_of(Build *b, int track, int chan, const char *name) {
    for (int i = 0; i < b->nparts; i++)
        if (b->parts[i].track == track && b->parts[i].chan == chan) return i;
    if (!grow((void **)&b->parts, &b->capp, b->nparts + 1, sizeof *b->parts)) return -1;
    Part *p = &b->parts[b->nparts];
    memset(p, 0, sizeof *p);
    p->track = (uint16_t)track, p->chan = (uint8_t)chan;
    snprintf(p->name, sizeof p->name, "%s", name);
    return b->nparts++;
}

static int add_note(Build *b, uint64_t on, uint64_t off, int pitch, int part) {
    if (part < 0 || off <= on) return 1;
    if (!grow((void **)&b->v, &b->cap, b->n + 1, sizeof *b->v)) return 0;
    b->v[b->n++] = (TickNote){on, off, (uint8_t)pitch, (uint16_t)part};
    return 1;
}

// One MTrk chunk: its notes (paired on to off per channel and key) and its tempo changes.
static int read_track(Build *b, int track, const uint8_t *p, const uint8_t *end, uint64_t *last_tick) {
    int64_t open[16][128];   // the tick a key went down, -1 while up
    int part_ix[16];
    for (int c = 0; c < 16; c++) {
        part_ix[c] = -1;
        for (int k = 0; k < 128; k++) open[c][k] = -1;
    }
    char name[48] = "";
    uint64_t tick = 0;
    int status = 0;
    while (p < end) {
        uint32_t delta;
        if (!varlen(p, end, &delta, &p)) return 0;
        tick += delta;
        if (p >= end) return 0;
        int s = *p;
        if (s & 0x80) p++, status = s < 0xf0 ? s : status;
        else if (!status) return 0;
        else s = status;   // running status: this byte is the first data byte
        if (s == 0xff) {
            if (p >= end) return 0;
            int type = *p++;
            uint32_t len;
            if (!varlen(p, end, &len, &p) || (size_t)(end - p) < len) return 0;
            if (type == 0x51 && len == 3) {
                if (!grow((void **)&b->t, &b->capt, b->nt + 1, sizeof *b->t)) return 0;
                b->t[b->nt++] = (Tempo){tick, (uint32_t)p[0] << 16 | p[1] << 8 | p[2]};
            } else if ((type == 0x03 || type == 0x04) && !name[0]) {
                size_t k = len < sizeof name - 1 ? len : sizeof name - 1;
                memcpy(name, p, k);
                name[k] = 0;
                for (char *c = name; *c; c++)
                    if ((unsigned char)*c < 0x20) *c = ' ';
            }
            p += len;
            if (type == 0x2f) break;
            continue;
        }
        if (s == 0xf0 || s == 0xf7) {
            uint32_t len;
            if (!varlen(p, end, &len, &p) || (size_t)(end - p) < len) return 0;
            p += len;
            continue;
        }
        if (s >= 0xf0) continue;   // system common or real time in a file: no data bytes to skip that we know of
        int kind = s & 0xf0, chan = s & 0x0f, n = kind == 0xc0 || kind == 0xd0 ? 1 : 2;
        if (end - p < n) return 0;
        int d0 = p[0] & 0x7f, d1 = n > 1 ? p[1] & 0x7f : 0;
        p += n;
        if ((kind != 0x90 && kind != 0x80) || chan == 9) continue;   // channel 10 is percussion
        int on = kind == 0x90 && d1 > 0;
        if (open[chan][d0] >= 0) {   // a key ends, or goes down again while down
            if (part_ix[chan] < 0) part_ix[chan] = part_of(b, track, chan, name);
            if (!add_note(b, (uint64_t)open[chan][d0], tick, d0, part_ix[chan])) return 0;
            open[chan][d0] = -1;
        }
        if (on) open[chan][d0] = (int64_t)tick;
    }
    for (int c = 0; c < 16; c++)
        for (int k = 0; k < 128; k++)
            if (open[c][k] >= 0) {
                if (part_ix[c] < 0) part_ix[c] = part_of(b, track, c, name);
                if (!add_note(b, (uint64_t)open[c][k], tick, k, part_ix[c])) return 0;
            }
    for (int i = 0; i < b->nparts; i++)   // a name met after the first note
        if (b->parts[i].track == track && !b->parts[i].name[0]) snprintf(b->parts[i].name, sizeof b->parts[i].name, "%s", name);
    if (tick > *last_tick) *last_tick = tick;
    return 1;
}

static int tempo_cmp(const void *a, const void *b) {
    const Tempo *x = a, *y = b;
    return x->tick < y->tick ? -1 : x->tick > y->tick;
}

static int note_cmp(const void *a, const void *b) {
    const Note *x = a, *y = b;
    if (x->on != y->on) return x->on < y->on ? -1 : 1;
    return (int)y->pitch - (int)x->pitch;   // the highest first at one start
}

// Ticks to microseconds through the tempo map (or the SMPTE rate of the division).
static int64_t tick_us(uint64_t tick, const Tempo *t, int nt, int division) {
    if (division & 0x8000) {
        int fps = -(int8_t)(division >> 8), tpf = division & 0xff;
        return fps > 0 && tpf > 0 ? (int64_t)((double)tick * 1e6 / (fps * tpf)) : 0;
    }
    double us = 0, tpq = division ? division : 480;
    uint64_t at = 0;
    uint32_t uspq = 500000;
    for (int i = 0; i < nt && t[i].tick < tick; i++) {
        us += (double)(t[i].tick - at) * uspq / tpq;
        at = t[i].tick, uspq = t[i].uspq ? t[i].uspq : uspq;
    }
    us += (double)(tick - at) * uspq / tpq;
    return (int64_t)us;
}

// The part most likely to be the tune: sounding through much of the song, one note at a time, high, and a name that
// says so. A part of a few long or scattered notes (a pad, a counter line) covers little of the song and loses.
static int pick_part(const Song *s) {
    int best = -1;
    double best_score = -1;
    int64_t song_len = 1;
    for (int k = 0; k < s->n; k++)
        if (s->notes[k].off > song_len) song_len = s->notes[k].off;
    for (int i = 0; i < s->nparts; i++) {
        const Part *p = &s->parts[i];
        if (!p->count) continue;
        int64_t last_off = -1, covered = 0;
        int overlapped = 0;
        for (int k = 0; k < s->n; k++) {
            const Note *x = &s->notes[k];
            if (x->part != i) continue;
            if (x->on < last_off - 30000) overlapped++;
            int64_t from = x->on > last_off ? x->on : last_off;
            if (x->off > from) covered += x->off - from;
            if (x->off > last_off) last_off = x->off;
        }
        double mono = 1.0 - (double)overlapped / p->count;
        double cover = (double)covered / song_len;   // the share of the song this part sounds in
        double rate = p->count / (song_len / 1e6);    // notes a second: a tune moves
        double score = mono * cover * (rate < 2.0 ? rate / 2.0 : 1.0) * log2(1.0 + p->count) * (p->mean / 60.0);
        char low[48];
        snprintf(low, sizeof low, "%s", p->name);
        for (char *c = low; *c; c++)
            if (*c >= 'A' && *c <= 'Z') *c = (char)(*c - 'A' + 'a');
        static const char *const tune[] = {"melody", "lead", "vocal", "voice", "flute", "ocarina", "solo", "main", "theme"};
        for (size_t k = 0; k < sizeof tune / sizeof *tune; k++)
            if (strstr(low, tune[k])) score *= 4.0;   // the file says which part is the tune: believe it
        if (p->count < 8) score *= 0.3;
        if (score > best_score) best_score = score, best = i;
    }
    return best;
}

// The melody (mel) from the selected part or parts: one note at a time. Notes that start together (a chord) give
// their highest; with every part at once a lower note that starts under a sounding higher one is left out (the top
// line), while within one part a new note ends the one before. Then the shift that puts the most of the melody's time
// on the ocarina's own notes, and each note onto the nearest one it can play.
static void melody_build(void) {
    free(mel);
    mel = NULL, mel_n = mel_bent = 0, transpose_auto = 0;
    if (!song.n) return;
    playable_build();
    int all = part_sel == -1, want = part_sel >= 0 ? part_sel : song.auto_part;
    Played *m = malloc((size_t)song.n * sizeof *m);
    if (!m) return;
    int n = 0;
    const int64_t CHORD_US = 40000;
    for (int i = 0; i < song.n; i++) {
        const Note *x = &song.notes[i];
        if (!all && x->part != want) continue;
        if (n && x->on < m[n - 1].off) {
            Played *c = &m[n - 1];
            if (x->on - c->on <= CHORD_US) {   // the same chord
                if (x->pitch > c->pitch) c->pitch = x->pitch, c->off = x->off;
                continue;
            }
            if (all && x->pitch < c->pitch) continue;
            c->off = x->on;
        }
        m[n++] = (Played){x->on, x->off, x->pitch, -1};
    }
    int k = 0;
    for (int i = 0; i < n; i++)
        if (m[i].off - m[i].on >= 15000) m[k++] = m[i];
    n = k;
    // the shift: time on an exact note in range counts fully, an octave away half
    double best = -1;
    int best_s = 0;
    for (int s = -36; s <= 36; s++) {
        double score = 0;
        for (int i = 0; i < n; i++) {
            int p = m[i].pitch + s;
            double d = (double)(m[i].off - m[i].on);
            if (p >= play_lo && p <= play_hi && playable_of(p) >= 0) score += d;
            else {
                while (p > play_hi) p -= 12;
                while (p < play_lo) p += 12;
                if (playable_of(p) >= 0) score += d * 0.5;
            }
        }
        score *= s % 12 == 0 ? 1.05 : 1.0;   // an octave keeps the key
        if (score > best || (score == best && abs(s) < abs(best_s))) best = score, best_s = s;
    }
    transpose_auto = best_s;
    for (int i = 0; i < n; i++) {
        int p = m[i].pitch + transpose_auto + transpose_user;
        while (p > play_hi) p -= 12;
        while (p < play_lo) p += 12;
        int ix = playable_of(p);
        for (int d = 1; ix < 0 && d < 12; d++) {
            ix = playable_of(p + d);
            if (ix < 0) ix = playable_of(p - d);
        }
        if (ix < 0) ix = 0;
        if (playable[ix].pitch != p) mel_bent++;
        m[i].idx = ix, m[i].pitch = playable[ix].pitch;
    }
    // every press seen: each note held MIN_HOLD at least, and let go GAP before the next starts; a run too fast for
    // that is played a little late, and catches up where the tune leaves room
    const int64_t GAP = 40000, MIN_HOLD = 55000;
    for (int i = 0; i < n; i++) {
        if (i && m[i].on < m[i - 1].off + GAP) m[i].on = m[i - 1].off + GAP;
        int64_t next = i + 1 < n ? m[i + 1].on : INT64_MAX;
        int64_t off = m[i].off < next - GAP ? m[i].off : next - GAP;
        if (off < m[i].on + MIN_HOLD) off = m[i].on + MIN_HOLD;
        m[i].off = off;
    }
    mel = m, mel_n = n;
}

static void song_free(Song *s) {
    free(s->notes);
    free(s->parts);
    memset(s, 0, sizeof *s);
}

int midi_oc_load(const char *path, char *err, size_t cap) {
    int64_t size = plat_file_size(path);
    if (size < 14 || size > (64 << 20)) { snprintf(err, cap, size < 0 ? "cannot read the file" : "not a MIDI file"); return 0; }
    uint8_t *data = malloc((size_t)size);
    PlatFile *f = data ? plat_fs_open(path, 0, NULL) : NULL;
    int ok = f && plat_file_pread(f, data, (uint32_t)size, 0) == size;
    if (f) plat_file_close(f);
    if (!ok) { free(data); snprintf(err, cap, "cannot read the file"); return 0; }
    const uint8_t *p = data, *end = data + size;
    if (memcmp(p, "MThd", 4) || be32(p + 4) < 6) { free(data); snprintf(err, cap, "not a MIDI file (no MThd header)"); return 0; }
    int division = p[12] << 8 | p[13], ntracks = p[10] << 8 | p[11];
    p += 8 + be32(p + 4);
    Build b = {0};
    uint64_t last_tick = 0;
    int track = 0, bad = 0;
    while (end - p >= 8 && track < ntracks + 64) {
        uint32_t len = be32(p + 4);
        const uint8_t *body = p + 8;
        if ((size_t)(end - body) < len) len = (uint32_t)(end - body);
        if (!memcmp(p, "MTrk", 4) && !read_track(&b, track++, body, body + len, &last_tick)) bad++;
        p = body + len;
    }
    free(data);
    if (!b.n) {
        free(b.v), free(b.t), free(b.parts);
        snprintf(err, cap, bad ? "the file is damaged: no notes could be read" : "the file has no notes (or only drums)");
        return 0;
    }
    qsort(b.t, (size_t)b.nt, sizeof *b.t, tempo_cmp);
    Song s = {0};
    s.notes = malloc((size_t)b.n * sizeof *s.notes);
    if (!s.notes) { free(b.v), free(b.t), free(b.parts); snprintf(err, cap, "out of memory"); return 0; }
    for (int i = 0; i < b.n; i++) {
        s.notes[i] = (Note){tick_us(b.v[i].on, b.t, b.nt, division), tick_us(b.v[i].off, b.t, b.nt, division), b.v[i].pitch,
                            b.v[i].part};
        b.parts[b.v[i].part].count++;
        b.parts[b.v[i].part].mean += b.v[i].pitch;
    }
    s.n = b.n;
    qsort(s.notes, (size_t)s.n, sizeof *s.notes, note_cmp);
    s.parts = b.parts, s.nparts = b.nparts;
    for (int i = 0; i < s.nparts; i++) {
        Part *q = &s.parts[i];
        if (q->count) q->mean /= q->count;
        snprintf(q->label, sizeof q->label, "%s%strack %d, channel %d (%d notes)", q->name, q->name[0] ? ": " : "",
                 q->track + 1, q->chan + 1, q->count);
    }
    free(b.v), free(b.t);
    s.auto_part = pick_part(&s);
    const char *base = plat_last_sep(path);
    snprintf(s.file, sizeof s.file, "%s", base ? base + 1 : path);
    plat_mutex_lock(&lock);
    song_free(&song);
    song = s;
    part_sel = -2;   // the picked part
    transpose_user = 0;
    melody_build();
    state = MIDI_OC_IDLE, cur_note = -1;
    int mn = mel_n, bent = mel_bent, tr = transpose_auto;
    double len = mel_n ? mel[mel_n - 1].off / 1e6 : 0;
    plat_mutex_unlock(&lock);
    rt_log("midi: %s: %d notes in %d parts%s, melody from part %d: %d notes, %.1f s, shifted %+d semitones, %d moved to a "
           "neighbouring note", s.file, s.n, s.nparts, bad ? " (some tracks damaged)" : "", s.auto_part + 1, mn, len, tr, bent);
    return 1;
}

int midi_oc_loaded(void) { return song.n > 0; }
const char *midi_oc_file(void) { return song.file; }
int midi_oc_track_count(void) { return song.nparts; }
const char *midi_oc_track_label(int i) { return i >= 0 && i < song.nparts ? song.parts[i].label : ""; }
int midi_oc_track(void) { return part_sel == -2 ? song.auto_part : part_sel; }
int midi_oc_auto_track(void) { return song.auto_part; }
int midi_oc_transpose_auto(void) { return transpose_auto; }
int midi_oc_transpose(void) { return transpose_user; }
int midi_oc_speed(void) { return speed; }
int midi_oc_notes(void) { return mel_n; }
int midi_oc_bent(void) { return mel_bent; }
int midi_oc_state(void) { return state; }

void midi_oc_set_track(int i) {
    plat_mutex_lock(&lock);
    part_sel = i < -1 ? -1 : i >= song.nparts ? song.nparts - 1 : i;
    melody_build();
    cur_note = -1;
    if (state == MIDI_OC_PLAYING) clock_us = 0;
    plat_mutex_unlock(&lock);
}

void midi_oc_set_transpose(int semitones) {
    plat_mutex_lock(&lock);
    transpose_user = semitones < -24 ? -24 : semitones > 24 ? 24 : semitones;
    melody_build();
    cur_note = -1;
    plat_mutex_unlock(&lock);
}

void midi_oc_set_speed(int percent) { speed = percent < 25 ? 25 : percent > 400 ? 400 : percent; }

void midi_oc_play(void) {
    plat_mutex_lock(&lock);
    if (mel_n) {
        state = MIDI_OC_WAITING, clock_us = 0, cur_note = -1, logged_note = -1, away_since_ns = 0;
        play_since_ns = plat_now_ns();
    }
    plat_mutex_unlock(&lock);
}

void midi_oc_stop(void) {
    plat_mutex_lock(&lock);
    state = MIDI_OC_IDLE, cur_note = -1;
    plat_mutex_unlock(&lock);
}

void midi_oc_toggle(void) {
    if (state == MIDI_OC_WAITING || state == MIDI_OC_PLAYING) midi_oc_stop();
    else midi_oc_play();
}

void midi_oc_progress(double *at, double *len) {
    plat_mutex_lock(&lock);
    *at = state == MIDI_OC_PLAYING ? clock_us / 1e6 : 0;
    *len = mel_n ? mel[mel_n - 1].off / 1e6 : 0;
    plat_mutex_unlock(&lock);
}


static void midi_step(uint64_t *buttons, int32_t *lx, int32_t *ly, int32_t *rx, int32_t *ry) {
    int st = __atomic_load_n(&state, __ATOMIC_RELAXED);
    if (st != MIDI_OC_WAITING && st != MIDI_OC_PLAYING) return;
    uint32_t screen = 0;
    int out = dbg_flag_get_u32(DBG_FLAG_ENUM, UI_SCREEN_KEY, &screen) && screen == UI_SCREEN_OCARINA;
    uint64_t now = plat_now_ns();
    plat_mutex_lock(&lock);
    if (!out) {
        // The screen flag can read something else for a moment while the ocarina stays out; only half a second of
        // it counts as the ocarina put away. Meanwhile no note is pressed and the tune waits.
        if (!away_since_ns) {
            away_since_ns = now;
            rt_log("midi: the UI screen reads %#x, not Ocarina, at %.2f s", screen, clock_us / 1e6);
        }
        if (now - away_since_ns >= 500000000ull) {
            ocarina_since_ns = 0;
            if (state == MIDI_OC_PLAYING) {
                rt_log("midi: the ocarina was put away at %.1f s (screen %#x): stopped", clock_us / 1e6, screen);
                state = MIDI_OC_IDLE, cur_note = -1;
            }
        }
        last_poll_ns = now;
        plat_mutex_unlock(&lock);
        return;
    }
    away_since_ns = 0;
    if (!ocarina_since_ns) ocarina_since_ns = now;
    if (state == MIDI_OC_WAITING) {
        // Link raises the ocarina for about a second after it comes out, and the press that closed the menu must be
        // let go: the first note waits for both
        if (now - ocarina_since_ns < 1200000000ull || now - play_since_ns < 400000000ull) {
            plat_mutex_unlock(&lock);
            return;
        }
        state = MIDI_OC_PLAYING, clock_us = 0, cur_note = -1, last_poll_ns = now;
        rt_log("midi: playing %s on the ocarina (%d notes)", song.file, mel_n);
    }
    uint64_t dt = now - last_poll_ns;
    last_poll_ns = now;
    if (dt > 100000000ull) dt = 100000000ull;   // a hitch or a pause (menu, text entry) does not skip notes
    clock_us += (int64_t)((double)dt / 1000.0 * speed / 100.0);
    if (cur_note < 0) cur_note = 0;
    while (cur_note < mel_n && clock_us >= mel[cur_note].off) cur_note++;
    if (cur_note >= mel_n) {
        rt_log("midi: %s finished", song.file);
        state = MIDI_OC_DONE, cur_note = -1;
        plat_mutex_unlock(&lock);
        return;
    }
    const Played *n = &mel[cur_note];
    if (clock_us >= n->on) {
        const Playable *p = &playable[n->idx];
        if (cur_note != logged_note && cur_note < 40) {   // the first notes in the log: what was pressed when
            static const char *const bname[] = {"A", "B", "X", "Y", "LS", "RS", "L", "R", "ZL", "ZR"};
            static const char *const pname[] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
            int bit = BASE[p->base].bit;
            rt_log("midi: %.2f s note %d: %s%d on %s, bent %+d", clock_us / 1e6, cur_note, pname[p->pitch % 12],
                   p->pitch / 12 - 1, bit < 10 ? bname[bit] : "?", p->mod);
        }
        logged_note = cur_note;
        *buttons |= 1ull << BASE[p->base].bit;
        // the left stick straight up or down for the bend, centred for a plain note (the player's own tilt would bend it)
        int32_t y = p->mod * BEND_PER_SEMITONE;
        *lx = 0, *ly = y > 32767 ? 32767 : y < -32767 ? -32767 : y;
        (void)rx, (void)ry;
    }
    plat_mutex_unlock(&lock);
}

// What the player pressed at its last poll, for the inputs readout (midi_oc_pressed), and when (plat_now_ns). The
// sticks it held: shown_stick_set bit 0 the left, bit 1 the right, shown_stick their axes.
static volatile uint64_t shown_bits, shown_at;
static volatile int32_t shown_stick[4], shown_stick_set;

void midi_oc_input(uint64_t *buttons, int32_t *lx, int32_t *ly, int32_t *rx, int32_t *ry) {
    uint64_t b = 0;
    const int32_t NONE = INT32_MIN;   // no stick value the player ever sets
    int32_t s[4] = {NONE, NONE, NONE, NONE};
    midi_step(&b, &s[0], &s[1], &s[2], &s[3]);
    int set = (s[0] != NONE) | (s[2] != NONE) << 1;
    if (set & 1) *lx = s[0], *ly = s[1];
    if (set & 2) *rx = s[2], *ry = s[3];
    *buttons |= b;
    for (int i = 0; i < 4; i++) __atomic_store_n(&shown_stick[i], s[i], __ATOMIC_RELAXED);
    __atomic_store_n(&shown_stick_set, set, __ATOMIC_RELAXED);
    __atomic_store_n(&shown_bits, b, __ATOMIC_RELAXED);
    __atomic_store_n(&shown_at, plat_now_ns(), __ATOMIC_RELAXED);
}

uint64_t midi_oc_pressed(int32_t *lx, int32_t *ly, int32_t *rx, int32_t *ry) {
    uint64_t at = __atomic_load_n(&shown_at, __ATOMIC_RELAXED);
    if (!at || plat_now_ns() - at >= 100000000ull) return 0;
    int set = __atomic_load_n(&shown_stick_set, __ATOMIC_RELAXED);
    if (set & 1) *lx = shown_stick[0], *ly = shown_stick[1];
    if (set & 2) *rx = shown_stick[2], *ry = shown_stick[3];
    return __atomic_load_n(&shown_bits, __ATOMIC_RELAXED);
}
