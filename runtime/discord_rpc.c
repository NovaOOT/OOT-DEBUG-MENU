// Discord Rich Presence: "Playing The Legend of Zelda: Ocarina of Time PC" with the elapsed play time and, with
// OOTPC_DISCORD_LOCATION on (the default), where the player is ("Kokiri Forest · Child") in the player's Discord status, through
// the local IPC of a running Discord client (platform.h's plat_ipc_*). Nothing leaves the machine but the activity
// itself, and nothing is sent while OOTPC_DISCORD_RPC is off (it is on by default, and does nothing without a
// Discord client running on the same PC). OOTPC_DISCORD_APP_ID names the application whose name ("Playing <name>")
// and artwork Discord shows, OOTPC_DISCORD_IMAGE the key of its uploaded art asset; both default to discord_app.h,
// the one place the OOT-PC application's id is written, and OOT-PC.ini's discord_app_id and discord_image override
// them. With no application id at all the thread stays idle and logs that once: there is nothing to show the status
// under.
//
// One host thread owns the connection and never holds a virtual core (kernel.c: host work runs without one). It
// re-reads the settings when cfg_version changes (a menu change reaches it within half a second), connects to the
// first of Discord's discord-ipc-0..9 pipes that answers, and writes the activity again every 60 s: that write is
// the keep-alive, and one that fails closes the connection and tries again 15 s later. Discord clears the status by
// itself when the process ends and the pipe closes, so there is no shutdown path. Every answer frame is read and
// discarded: unread, they would fill the pipe's buffer and block the client's writes.
//
// The protocol is the documented IPC one (discord-rpc's src/rpc_connection.h): a frame is <opcode u32 little
// endian><length u32><length bytes of JSON>, with Handshake 0, Frame 1, Close 2, Ping 3 and Pong 4. The handshake
// (opcode 0) carries {"v":1,"client_id":...} and is answered by a Ready frame (opcode 1); every command,
// SET_ACTIVITY here, is a frame (opcode 1) answered by one, and a Ping the client sends is echoed as a Pong. A
// client that speaks another numbering (an early arRPC did) answers the handshake with a Close. --probe-discord
// runs the same exchange once, without the game and without the setting being on.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cfg.h"
#include "debug_tools.h"
#include "host.h"
#include "platform/platform.h"

enum { OP_HANDSHAKE = 0, OP_FRAME = 1, OP_CLOSE = 2, OP_PING = 3, OP_PONG = 4 };
enum { LOCATION_GAP_MS = 5000 };   // the least time between two activity updates for a change of area

#define APP_ID_CAP 24    // a Discord snowflake: at most 20 digits
#define IMAGE_KEY_CAP 64
#define ANSWER_CAP 8192  // the Ready frame carries the client's config; a few KiB

// A settings value that fits the JSON below without escaping: the app id is digits, an asset key letters, digits,
// '_' and '-' (what Discord's developer portal accepts for an asset key).
static int valid_app_id(const char *s) {
    size_t n = strlen(s);
    if (!n || n > 20) return 0;
    for (const char *p = s; *p; p++)
        if (*p < '0' || *p > '9') return 0;
    return 1;
}

static int valid_image_key(const char *s) {
    size_t n = strlen(s);
    if (!n || n >= IMAGE_KEY_CAP) return 0;
    for (const char *p = s; *p; p++)
        if ((*p < 'a' || *p > 'z') && (*p < 'A' || *p > 'Z') && (*p < '0' || *p > '9') && *p != '_' && *p != '-')
            return 0;
    return 1;
}

// The setting's value, or its default when the value does not fit the protocol (logged: the player sees why their
// own app does not show), or "" when the default does not fit either (an application id discord_app.h leaves
// empty). Called on the presence thread and by the probe, after cfg_version changed or once.
static void read_setting(const char *name, char *out, size_t cap, int (*valid)(const char *)) {
    const char *v = cfg_value(name), *def = cfg_meta(name)->def;
    if (valid(v)) {
        snprintf(out, cap, "%s", v);
        return;
    }
    snprintf(out, cap, "%s", valid(def) ? def : "");
    if (*v && strcmp(v, def))
        rt_log("discord: %s does not fit what the Rich Presence protocol takes; using %s", name + strlen("OOTPC_"),
               *out ? out : "none");
}

static void log_no_app_id(void) {
    rt_log("discord: no application id: set discord_app_id in OOT-PC.ini, or OOTPC_DISCORD_APP_ID_DEFAULT in "
           "runtime/discord_app.h, to the id of the Discord application named The Legend of Zelda: Ocarina of Time PC");
}

static void le32(uint8_t *at, uint32_t v) {
    at[0] = (uint8_t)v, at[1] = (uint8_t)(v >> 8), at[2] = (uint8_t)(v >> 16), at[3] = (uint8_t)(v >> 24);
}

static int write_frame(PlatIpc *p, uint32_t op, const char *json) {
    uint8_t head[8];
    le32(head, op);
    le32(head + 4, (uint32_t)strlen(json));
    return plat_ipc_write(p, head, sizeof head) && plat_ipc_write(p, json, strlen(json));
}

// One frame off the wire, its 8-byte header in as many reads as the peer made, then its payload; a payload longer
// than cap-1 fills buf with its first cap-1 bytes and the rest is still consumed. *op gets the opcode; buf is
// NUL-terminated. 1 on a whole frame, 0 when the deadline passed, -1 when the connection broke.
static int read_one_frame(PlatIpc *p, uint32_t *op, char *buf, size_t cap, uint64_t deadline) {
    uint8_t head[8];
    size_t got = 0;
    while (got < sizeof head) {
        uint32_t left = deadline > plat_now_ms() ? (uint32_t)(deadline - plat_now_ms()) : 0;
        int n = plat_ipc_read(p, head + got, sizeof head - got, left);
        if (n < 0) return -1;
        if (!n) {
            if (plat_now_ms() >= deadline) return 0;
            continue;
        }
        got += (size_t)n;
    }
    *op = head[0] | head[1] << 8 | head[2] << 16 | (uint32_t)head[3] << 24;
    uint32_t len = head[4] | head[5] << 8 | head[6] << 16 | (uint32_t)head[7] << 24;
    size_t taken = 0;
    while (taken < len) {
        size_t want = len - taken > cap - 1 ? cap - 1 : len - taken;
        uint32_t left = deadline > plat_now_ms() ? (uint32_t)(deadline - plat_now_ms()) : 0;
        int n = plat_ipc_read(p, buf, want, left);
        if (n < 0) return -1;
        if (!n) {
            if (plat_now_ms() >= deadline) return 0;
            continue;
        }
        taken += (size_t)n;
    }
    buf[taken < cap - 1 ? taken : cap - 1] = 0;
    return 1;
}

// The next frame that is not a Ping: a Ping the client sent to see we are alive is echoed as a Pong with its own
// payload, a Close (the client told us it is done) counts as a broken connection so the caller connects again
// later. Same return values as read_one_frame; the deadline covers the whole exchange.
static int read_frame(PlatIpc *p, uint32_t *op, char *buf, size_t cap, uint32_t ms) {
    uint64_t deadline = plat_now_ms() + ms;
    for (;;) {
        int r = read_one_frame(p, op, buf, cap, deadline);
        if (r != 1) return r;
        if (*op == OP_PING) {
            uint8_t head[8];
            size_t n = strlen(buf);
            le32(head, OP_PONG);
            le32(head + 4, (uint32_t)n);
            if (!plat_ipc_write(p, head, sizeof head) || !plat_ipc_write(p, buf, n)) return -1;
            continue;
        }
        if (*op == OP_CLOSE) return -1;
        return 1;
    }
}

// The first of Discord's pipes that completes a handshake. NULL when no client is running or none answered in time;
// the caller tries again later.
static PlatIpc *connect_client(const char *app_id) {
    for (int i = 0; i < 10; i++) {
        char name[32], json[160];
        snprintf(name, sizeof name, "discord-ipc-%d", i);
        PlatIpc *p = plat_ipc_connect(name);
        if (!p) continue;
        snprintf(json, sizeof json, "{\"v\":1,\"client_id\":\"%s\"}", app_id);
        if (!write_frame(p, OP_HANDSHAKE, json)) {
            plat_ipc_close(p);
            continue;
        }
        uint32_t op = 0;
        char answer[ANSWER_CAP];
        if (read_frame(p, &op, answer, sizeof answer, 2000) == 1 && op == OP_FRAME) return p;   // the Ready event
        plat_ipc_close(p);   // this pipe answered but not with a Ready frame in 2 s: the next may belong to a client
    }
    return NULL;
}

// ---- where the player is (OOTPC_DISCORD_LOCATION): the status's second line
// The area file the game has loaded (debug_tools.h dbg_current_area: "KokiriForest_Child", "HyruleField_Adult") as
// words with Link's age: "Kokiri Forest · Child". The name loses its age suffix and gets a space before each capital
// that follows a small letter and for each '_'; the few file names that are not the game's own spelling get it from
// the table. Only letters, digits, spaces and the apostrophe come out, so the line goes into the JSON unescaped.
static const struct { const char *file, *shown; } AREA_NAMES[] = {
    {"HountedWasteland", "Haunted Wasteland"}, {"LostWood", "Lost Woods"}, {"ZoraDomain", "Zora's Domain"},
    {"ZoraFountain", "Zora's Fountain"}, {"ZoraRiver", "Zora's River"}, {"HrlCastle", "Hyrule Castle"},
    {"DeathMtTrail", "Death Mountain Trail"}, {"DeathMtCrater", "Death Mountain Crater"},
};

// The screen the game's UI is on (the enum flag debug_move.c reads, key 0x578d41e1), by the hash of its name.
#define UI_SCREEN_KEY 0x578d41e1u
enum { SCREEN_PLAY, SCREEN_TITLE, SCREEN_MENU, SCREEN_LOADING, SCREEN_GAME_OVER };
static int ui_screen(void) {
    static uint32_t title, load, loading, game_over, menus[6];
    static const char *const kMenus[6] = {"ItemMenu", "EquipmentMenu", "CollectMenu", "SystemMenu", "Save", "Option"};
    if (!title) {
        for (int i = 0; i < 6; i++) menus[i] = dbg_flag_key(kMenus[i]);
        load = dbg_flag_key("Load"), loading = dbg_flag_key("Loading"), game_over = dbg_flag_key("GameOver");
        title = dbg_flag_key("Title");
    }
    uint32_t v = 0;
    if (!dbg_flag_get_u32(DBG_FLAG_ENUM, UI_SCREEN_KEY, &v)) return SCREEN_PLAY;
    if (v == title || (v == load && !dbg_player_actor())) return SCREEN_TITLE;   // Load with no player: the title's list
    if (v == loading) return SCREEN_LOADING;
    if (v == game_over) return SCREEN_GAME_OVER;
    if (v == load) return SCREEN_MENU;
    for (int i = 0; i < 6; i++)
        if (v == menus[i]) return SCREEN_MENU;
    return SCREEN_PLAY;
}

// The state line: the area the player is in ("Kokiri Forest · Child"), "In the menu · " before it while the game's
// pause menu is open; "In the main menu" on the title screen, "Game over" on that screen; a loading screen keeps the
// line before it. 0 when there is nothing to show.
static int location_line(char *out, size_t cap) {
    static char last[160];
    char area[64] = "", name[64];
    if (!dbg_available()) return 0;
    const int screen = ui_screen();
    const char *fixed = screen == SCREEN_TITLE ? "In the main menu" : screen == SCREEN_GAME_OVER ? "Game over" : NULL;
    if (screen == SCREEN_LOADING && last[0]) return snprintf(out, cap, "%s", last), 1;
    if (!fixed && (!dbg_current_area(area) || !area[0]))
        fixed = dbg_player_actor() ? NULL : "In the main menu";   // no area and no player: no game started yet
    if (fixed) {
        snprintf(out, cap, "%s", fixed);
        snprintf(last, sizeof last, "%s", fixed);
        return 1;
    }
    if (!area[0]) return 0;
    size_t n = strlen(area);
    if (n > 6 && (!strcmp(area + n - 6, "_Child") || !strcmp(area + n - 6, "_Adult"))) n -= 6;
    snprintf(name, sizeof name, "%.*s", (int)n, area);
    const char *shown = NULL;
    for (size_t i = 0; i < sizeof AREA_NAMES / sizeof *AREA_NAMES; i++)
        if (!strcmp(name, AREA_NAMES[i].file)) shown = AREA_NAMES[i].shown;
    char words[96];
    size_t w = 0;
    if (shown) {
        snprintf(words, sizeof words, "%s", shown);
        w = strlen(words);
    } else {
        for (size_t i = 0; name[i] && w + 2 < sizeof words; i++) {
            char c = name[i];
            int lower_before = i && name[i - 1] >= 'a' && name[i - 1] <= 'z';
            if (c == '_') c = ' ';
            else if (((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) && lower_before) words[w++] = ' ';
            if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || (c == ' ' && w && words[w - 1] != ' '))
                words[w++] = c;
        }
        while (w && words[w - 1] == ' ') w--;
        words[w] = 0;
    }
    if (!w) return 0;
    int child = dbg_is_child();
    snprintf(out, cap, "%s%s%s", screen == SCREEN_MENU ? "In the menu \\u00b7 " : "", words,
             child == 1 ? " \\u00b7 Child" : child == 0 ? " \\u00b7 Adult" : "");
    snprintf(last, sizeof last, "%s", out);
    return 1;
}

// ---- what the player is fighting (OOTPC_DISCORD_FIGHT, on by default): "Attacking: Deku Baba" after the place
//
// The enemies near the player come from the debug overlay's enemy list (debug_view.c dbg_view_health), which scans the
// heap on its own helper thread every 1.5 s while someone wants it: here, while the presence is connected, in the game
// and with the setting on (dbg_view_foes). Each poll of the presence thread (every half second) compares every enemy's
// life with the poll before: the nearest one whose life fell is the one the player is hitting. When the player's own
// health falls, the nearest living enemy within FOE_NEAR_M is the one hitting them. Either names the enemy for
// FOE_HOLD_MS after the last such exchange, so a fight shows while it lasts and is gone a little after it ends.
enum { FOE_MAX = 64, FOE_HOLD_MS = 12000 };
#define FOE_NEAR_M 12.0f
#define FOE_SAME_M 4.0f   // one enemy between two polls: the same name and no further than this

// The short names players know the enemies by: a variant goes by its kind (a Fire Keese is a Keese, a Gold Skulltula a
// Skulltula); bosses and named characters keep their names. Others are made from the actor's name (foe_name). Where
// two entries share a start, the longer one comes first.
static const struct { const char *actor, *shown; } FOE_NAMES[] = {
    {"Enemy_Amos", "Armos"}, {"Enemy_Anubis", "Anubis"}, {"Enemy_BabyDodongo", "Baby Dodongo"}, {"Enemy_Bari", "Bari"},
    {"Enemy_Beamos", "Beamos"}, {"Enemy_Biri", "Biri"}, {"Enemy_Bubble_Blue", "Bubble"},
    {"Enemy_Bubble_Green", "Bubble"}, {"Enemy_Bubble_Red", "Bubble"}, {"Enemy_Bubble", "Bubble"},
    {"Enemy_DeadHand_Hand", "Dead Hand"}, {"Enemy_DeadHand", "Dead Hand"}, {"Enemy_Dekubaba", "Deku Baba"},
    {"Enemy_Dinolfos", "Dinolfos"}, {"Enemy_Dodongo", "Dodongo"},
    {"Enemy_DungeonBoss_Barinade_Jellyfish", "Biri"}, {"Enemy_DungeonBoss_Barinade", "Barinade"},
    {"Enemy_DungeonBoss_BongoBongo", "Bongo Bongo"}, {"Enemy_DungeonBoss_Dodongo", "King Dodongo"},
    {"Enemy_DungeonBoss_Ganondorf", "Ganondorf"}, {"Enemy_DungeonBoss_Ganon", "Ganon"},
    {"Enemy_DungeonBoss_Gohma", "Gohma"}, {"Enemy_DungeonBoss_Kotake", "Twinrova"},
    {"Enemy_DungeonBoss_Koume", "Twinrova"}, {"Enemy_DungeonBoss_Twinrova", "Twinrova"},
    {"Enemy_DungeonBoss_Morpha", "Morpha"}, {"Enemy_DungeonBoss_Volvagia", "Volvagia"},
    {"Enemy_DungeonBoss_PhantomGanon", "Phantom Ganon"}, {"Enemy_Floormaster", "Floormaster"},
    {"Enemy_Freezard", "Freezard"}, {"Enemy_Gibdo", "Gibdo"}, {"Enemy_Gohma_Larva", "Gohma Larva"},
    {"Enemy_Guay", "Guay"}, {"Enemy_IronKnuckle", "Iron Knuckle"}, {"Enemy_Keese_Fire", "Keese"},
    {"Enemy_Keese_Ice", "Keese"}, {"Enemy_Keese", "Keese"}, {"Enemy_Leever", "Leever"},
    {"Enemy_LikeLike", "Like Like"}, {"Enemy_Lizalfos", "Lizalfos"}, {"Enemy_MiddleBoss_BigOcto", "Big Octo"},
    {"Enemy_MiddleBoss_DarkLink", "Dark Link"}, {"Enemy_MiddleBoss_FlareDancer", "Flare Dancer"},
    {"Enemy_MiddleBoss_IronKnuckle", "Iron Knuckle"}, {"Enemy_MiddleBoss_Poe_Meg", "Meg"},
    {"Enemy_MiddleBoss_DeadHand", "Dead Hand"}, {"Enemy_Moblin", "Moblin"}, {"Enemy_Octorok", "Octorok"},
    {"Enemy_Peahat_Larva", "Peahat"}, {"Enemy_Peahat", "Peahat"}, {"Enemy_Poe_Amy", "Amy"},
    {"Enemy_Poe_Beth", "Beth"}, {"Enemy_Poe_Joelle", "Joelle"}, {"Enemy_Poe_Big", "Poe"},
    {"Enemy_Poe_Sharp", "Sharp"}, {"Enemy_Poe_Flat", "Flat"}, {"Enemy_Poe", "Poe"}, {"Enemy_ReDead", "ReDead"},
    {"Enemy_ScrubDeku", "Deku Scrub"}, {"Enemy_Scrub", "Deku Scrub"}, {"Enemy_Shabom", "Shabom"},
    {"Enemy_ShellBlade", "Shell Blade"}, {"Enemy_SkullKid", "Skull Kid"}, {"Enemy_Skulltula_Gold", "Skulltula"},
    {"Enemy_Skulltula", "Skulltula"}, {"Enemy_Skullwalltula", "Skullwalltula"}, {"Enemy_Spike", "Spike"},
    {"Enemy_Stalchild", "Stalchild"}, {"Enemy_Stalfos", "Stalfos"}, {"Enemy_Stinger", "Stinger"},
    {"Enemy_Tailpasaran", "Tailpasaran"}, {"Enemy_Tektite_Blue", "Tektite"}, {"Enemy_Tektite_Red", "Tektite"},
    {"Enemy_TorchSlug", "Torch Slug"}, {"Enemy_Wolfos_White", "Wolfos"}, {"Enemy_Wolfos", "Wolfos"},
};

// The name to show for an actor: the first table entry whose actor name starts the actor's at a word end, else the
// first part of the actor's name after its kind prefixes and game tags (Enemy_Foo_Big: "Foo"), its words split where a capital
// follows a small letter.
static void foe_name(const char *actor, char *out, size_t cap) {
    for (size_t i = 0; i < sizeof FOE_NAMES / sizeof *FOE_NAMES; i++) {
        size_t n = strlen(FOE_NAMES[i].actor);
        if (!strncmp(actor, FOE_NAMES[i].actor, n) && (actor[n] == 0 || actor[n] == '_')) {
            snprintf(out, cap, "%s", FOE_NAMES[i].shown);
            return;
        }
    }
    // the kind prefixes, and the tags of mod enemies taken from other games (Enemy_BotW_Lynel: "Lynel")
    static const char *const prefix[] = {"Enemy_", "DungeonBoss_", "MiddleBoss_", "BotW_", "TotK_", "MM_", "WW_", "TP_",
                                         "SS_", "ALBW_", "LA_"};
    for (int again = 1; again;) {
        again = 0;
        for (size_t i = 0; i < sizeof prefix / sizeof *prefix; i++)
            if (!strncmp(actor, prefix[i], strlen(prefix[i]))) actor += strlen(prefix[i]), again = 1;
    }
    size_t w = 0;
    for (size_t i = 0; actor[i] && actor[i] != '_' && w + 2 < cap; i++) {   // the kind only: no variant after a '_'
        char c = actor[i];
        int lower_before = i && actor[i - 1] >= 'a' && actor[i - 1] <= 'z';
        if (c >= 'A' && c <= 'Z' && lower_before) out[w++] = ' ';
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || (c == ' ' && w && out[w - 1] != ' '))
            out[w++] = c;
    }
    while (w && out[w - 1] == ' ') w--;
    out[w] = 0;
}

static struct {
    DbgHealth last[FOE_MAX];   // the enemies of the poll before, with their life then
    int nlast, last_hp;        // last_hp: the player's health then, -1 not known
    char shown[64];            // the enemy being fought, "" none
    uint64_t shown_ms;         // the last exchange that named it
} F = {.last_hp = -1};

static float dist2(const float a[3], const float b[3]) {
    float d[3] = {a[0] - b[0], a[1] - b[1], a[2] - b[2]};
    return d[0] * d[0] + d[1] * d[1] + d[2] * d[2];
}

// "Attacking: <enemy>" into out while a fight is on, "" otherwise; on = 0 forgets the fight and stops the enemy scan.
static void fight_line(char *out, size_t cap, int on) {
    out[0] = 0;
    dbg_view_foes(on);
    if (!on) {
        F.nlast = 0, F.last_hp = -1, F.shown[0] = 0;
        return;
    }
    static DbgHealth now[FOE_MAX];
    int n = dbg_view_health(now, FOE_MAX, 60.0f);
    float me[3];
    int have_me = dbg_player_pos(me);
    uint64_t t = plat_now_ms();
    int hit = -1;
    float hit_d = 1e30f;
    for (int i = 0; i < n; i++)
        for (int j = 0; j < F.nlast; j++) {
            if (strcmp(now[i].name, F.last[j].name) || dist2(now[i].pos, F.last[j].pos) > FOE_SAME_M * FOE_SAME_M) continue;
            float d = have_me ? dist2(now[i].pos, me) : 0;
            if (now[i].life < F.last[j].life && d < hit_d) hit = i, hit_d = d;
            break;
        }
    int cur = 0, max = 0;
    if (dbg_health_get(&cur, &max)) {
        if (hit < 0 && have_me && F.last_hp >= 0 && cur < F.last_hp)   // hurt: the nearest living enemy close by
            for (int i = 0; i < n; i++) {
                float d = dist2(now[i].pos, me);
                if (now[i].life > 0 && d < FOE_NEAR_M * FOE_NEAR_M && d < hit_d) hit = i, hit_d = d;
            }
        F.last_hp = cur;
    }
    if (hit >= 0) {
        char was[sizeof F.shown];
        memcpy(was, F.shown, sizeof was);
        foe_name(now[hit].name, F.shown, sizeof F.shown), F.shown_ms = t;
        if (strcmp(was, F.shown)) rt_log("discord: fighting %s (%s)", F.shown, now[hit].name);
    }
    memcpy(F.last, now, (size_t)n * sizeof *now);
    F.nlast = n;
    if (F.shown[0] && t - F.shown_ms > FOE_HOLD_MS) F.shown[0] = 0;
    if (F.shown[0]) snprintf(out, cap, "Attacking: %s", F.shown);
}

static uint32_t next_nonce(void) {
    static volatile int32_t n;
    return (uint32_t)plat_inc32(&n);
}

// The activity as one SET_ACTIVITY frame: the game update and the port version as the details line ("Game 1.1.1 ·
// Recomp 0.3.7": the port version without its +commit suffix, the art's hover text keeps the full one; the dot goes
// as a JSON escape), the process's start as the timestamp Discord counts the elapsed time from, and the application's
// art asset; where the player is (location_line) as the state line under it when there is one. 1 when the client
// answered (a slow answer that ran out of the 2 s read is fine: the next drain takes it), 0 when the connection broke.
static int send_activity(PlatIpc *p, const char *image, const char *location) {
    char json[768], state[160] = "";
    int v = rt_game_version;
    uint64_t start = (plat_unix_time_ns() - plat_process_uptime_ns()) / 1000000000ull;
    int port_len = (int)strcspn(rt_port_version, "+");
    if (location && *location) snprintf(state, sizeof state, ",\"state\":\"%s\"", location);
    snprintf(json, sizeof json,
             "{\"cmd\":\"SET_ACTIVITY\",\"nonce\":\"%u\",\"args\":{\"pid\":%lu,\"activity\":{\"details\":\"Game "
             "%d.%d.%d \\u00b7 Recomp %.*s\"%s,\"timestamps\":{\"start\":%llu},\"assets\":{\"large_image\":\"%s\","
             "\"large_text\":\"OOT-PC %s\"}}}}",
             next_nonce(), (unsigned long)plat_process_id(), v / 100, v / 10 % 10, v % 10, port_len, rt_port_version,
             state, (unsigned long long)start, image, rt_port_version);
    if (!write_frame(p, OP_FRAME, json)) return 0;
    uint32_t op = 0;
    char answer[ANSWER_CAP];
    return read_frame(p, &op, answer, sizeof answer, 2000) >= 0;
}

// ---- the presence thread

static void discord_main(void *arg) {
    (void)arg;
    unsigned seen = 0;
    char app_id[APP_ID_CAP], image[IMAGE_KEY_CAP];
    read_setting("OOTPC_DISCORD_APP_ID", app_id, sizeof app_id, valid_app_id);
    read_setting("OOTPC_DISCORD_IMAGE", image, sizeof image, valid_image_key);
    PlatIpc *ipc = NULL;
    uint64_t retry_at = 0, send_at = 0, sent_ms = 0;
    char sent_where[160] = "";   // the location line of the last activity sent
    int said_no_client = 0, said_no_app = 0;
    for (;;) {
        unsigned v = cfg_version();
        if (v != seen) {   // the settings changed (the menu writes them): pick the new ones up at once
            seen = v;
            // any setting bumps the version (a slider step, a cheat): only a new app id needs a new handshake, and
            // a new image only a resend; reconnecting on every change made the status blink and could hit Discord's
            // rate limit while a slider was held
            char was_id[APP_ID_CAP], was_image[IMAGE_KEY_CAP];
            memcpy(was_id, app_id, sizeof was_id);
            memcpy(was_image, image, sizeof was_image);
            read_setting("OOTPC_DISCORD_APP_ID", app_id, sizeof app_id, valid_app_id);
            read_setting("OOTPC_DISCORD_IMAGE", image, sizeof image, valid_image_key);
            if (strcmp(was_id, app_id)) {
                if (ipc) plat_ipc_close(ipc), ipc = NULL;
                retry_at = 0;
                said_no_client = said_no_app = 0;
            } else if (strcmp(was_image, image)) {
                send_at = 0;
            }
        }
        if (!cfg_bool("OOTPC_DISCORD_RPC")) {
            if (ipc) plat_ipc_close(ipc), ipc = NULL;
            plat_sleep_ms(500);
            continue;
        }
        if (!app_id[0]) {   // on, but no application to show it under: wait for a settings change
            if (!said_no_app) log_no_app_id();
            said_no_app = 1;
            plat_sleep_ms(500);
            continue;
        }
        if (!ipc && plat_now_ms() >= retry_at) {
            ipc = connect_client(app_id);
            if (!ipc) {
                retry_at = plat_now_ms() + 15000;
                if (!said_no_client)
                    rt_log("discord: no client is running; trying again every 15 s while the setting is on");
                said_no_client = 1;
            } else {
                rt_log("discord: connected (app %s)", app_id);
                send_at = 0;
                said_no_client = 0;
            }
        }
        // A new area sends at once, but no sooner than LOCATION_GAP_MS after the last send: Discord takes a handful
        // of activity updates in 20 s, and a run of loading screens (a door, then a room) would use them up.
        char where[160] = "";
        if (cfg_bool("OOTPC_DISCORD_LOCATION")) location_line(where, sizeof where);
        // the fight after the place, in the game only (not on the title, game over or pause menu lines)
        char fight[96];
        fight_line(fight, sizeof fight, ipc && where[0] && cfg_bool("OOTPC_DISCORD_FIGHT") && ui_screen() == SCREEN_PLAY);
        if (fight[0]) {
            size_t wl = strlen(where);
            snprintf(where + wl, sizeof where - wl, " \\u00b7 %s", fight);
        }
        if (ipc && strcmp(where, sent_where) && plat_now_ms() >= sent_ms + LOCATION_GAP_MS) send_at = 0;
        if (ipc && plat_now_ms() >= send_at) {
            if (send_activity(ipc, image, where)) {
                memcpy(sent_where, where, sizeof sent_where);
                sent_ms = plat_now_ms();
                send_at = plat_now_ms() + 60000;
            } else {
                rt_log("discord: the connection broke; reconnecting");
                plat_ipc_close(ipc);
                ipc = NULL;
                retry_at = plat_now_ms() + 15000;
            }
        }
        plat_sleep_ms(500);
    }
}

void discord_init(void) {
    if (!plat_thread_spawn(discord_main, NULL, "discord", 0)) rt_log("discord: cannot start the presence thread");
}

// ---- --probe-discord: the exchange above without the game and the setting; needs no game files

int probe_discord(void) {
    static int fails;
#define CHECK(c, what) do { if (!(c)) { fails++; fprintf(stderr, "FAIL discord: %s\n", what); } } while (0)
    char app_id[APP_ID_CAP], image[IMAGE_KEY_CAP];
    read_setting("OOTPC_DISCORD_APP_ID", app_id, sizeof app_id, valid_app_id);
    read_setting("OOTPC_DISCORD_IMAGE", image, sizeof image, valid_image_key);
    if (!app_id[0]) {
        log_no_app_id();
        fprintf(stderr, "discord: probe skipped: no application id (runtime/discord_app.h or discord_app_id)\n");
        return 0;
    }
    PlatIpc *p = connect_client(app_id);
    if (!p) {
        fprintf(stderr, "discord: probe skipped: no client on discord-ipc-0..9\n");
        return 0;
    }
    fprintf(stderr, "discord: connected (app %s)\n", app_id);
    CHECK(send_activity(p, image, "Hyrule Field \\u00b7 Adult"), "SET_ACTIVITY written and answered");
    fprintf(stderr, "discord: activity set; the status shows it for ten seconds, then the connection closes\n");
    plat_sleep_ms(10000);
    plat_ipc_close(p);
    fprintf(stderr, "discord: probe %s\n", fails ? "FAILED" : "passed");
    return fails != 0;
#undef CHECK
}
