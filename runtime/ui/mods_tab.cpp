// The Mods tab of the in-game menu: Play as Lank, Play as Dark Link (the Dark Link mod's switch) and the other built-in switches, then a switch for each mod folder (mods.c) in the Mods order, with the
// mod's setting (mods.h) on a row under it; a setting several mods share is one row, under the first of them. A mod's
// switch and setting apply at the next start; the row says so while they differ from what this run started with.
#include <stdio.h>
#include <string.h>

#include "imgui.h"
#include "ui/option_rows.h"

extern "C" {
#include "cfg.h"
#include "debug_tools.h"
#include "mods.h"
#include "n64gfx.h"
}

// A live 0|1 setting (cfg.c) as a switch: unset counts as def.
static bool cfg_on(const char *key, bool def) {
    char name[64];
    snprintf(name, sizeof name, "OOTPC_%s", key);
    const char *v = cfg_get(name);
    return v && *v ? strcmp(v, "0") != 0 : def;
}
static void cfg_toggle_row(const char *id, const char *key, const char *label, const char *desc, bool def,
                           bool needs = true, const char *why = nullptr) {
    char name[64];
    snprintf(name, sizeof name, "OOTPC_%s", key);
    if (!needs) opt_disable_next_text(why);
    else if (cfg_from_env(name)) opt_disable_next_text("Set by the environment.");
    bool on = cfg_on(key, def);
    if (opt_toggle_text(id, label, desc, on)) {
        cfg_set(name, on ? "0" : "1");
        opt_save_soon();
    }
}

// The N64 Graphics mod's parts (n64gfx.h N64_PART_*), each on its own switch under the mod's.
static void n64_parts(bool mod_on) {
    static const char *const labels[N64_PARTS] = {"N64 Link", "N64 items", "N64 enemies and characters", "N64 world",
                                                  "Swords strike walls"};
    static const char *const descs[N64_PARTS] = {
        "Link is the N64 model, with his tunics, boots, gauntlets, swords and shields. Off: the remake's Link.",
        "The items Link holds (bow, slingshot, hookshot, ocarinas, boomerang, hammer, bottles, Deku stick) and the "
        "masks are the N64 ones. Needs N64 Link.",
        "Enemies and other characters are drawn as the N64 version's where the mod has them.",
        "The levels are drawn as the N64 version's where the mod has them; the remake's collision stays.",
        "A sword, Deku stick or hammer swung into a wall bounces off it with a spark, as in the N64 game, instead of "
        "passing through."};
    for (int k = 0; k < N64_PARTS; k++) {
        char id[48];
        snprintf(id, sizeof id, "##mod_n64_part_%d", k);
        bool needs = mod_on && (k != N64_PART_ITEMS || cfg_on("N64_LINK", true));
        cfg_toggle_row(id, n64gfx_part_key(k), labels[k], descs[k], true, needs,
                       !mod_on ? "Turn N64 Graphics on to use it." : "Turn N64 Link on to use it.");
    }
}

// The Dark Link mod's style while N64 Graphics draws N64 Link (n64gfx.h n64gfx_dark), on a row under the mod's switch:
// N64 Link drawn as the N64 game's Dark Link, or N64 Link off so the remake's dark models play.
static void dark_link_style(bool mod_on) {
    static const char *const names[] = {"N64 style", "Remake style"};
    const char *v = cfg_get("OOTPC_DARK_LINK_STYLE");
    int cur = v && !strcmp(v, "remake") ? 1 : 0;
    if (!mod_on) opt_disable_next_text("Turn Dark Link on to use it.");
    else if (!mods_feature(MOD_FEATURE_N64, nullptr, 0)) opt_disable_next_text("Turn N64 Graphics on to use it.");
    else if (cfg_from_env("OOTPC_DARK_LINK_STYLE")) opt_disable_next_text("Set by the environment.");
    int r = opt_text_choice("##mod_dark_link_style", "Dark Link style", names, 2, cur,
                            "While N64 Graphics draws N64 Link. N64 style: N64 Link is the N64 game's Dark Link, near "
                            "black with red eyes, as child and adult. Remake style: N64 Link is off and the remake's "
                            "dark models play.");
    if (r >= 0 && r != cur) {
        cfg_set("OOTPC_DARK_LINK_STYLE", r ? "remake" : "n64");
        opt_save_soon();
    }
    bool fx = cfg_bool("OOTPC_DARK_LINK_EFFECTS") != 0;
    const char *vs = cfg_get("OOTPC_DARK_LINK_STYLE");
    if (!mod_on) opt_disable_next_text("Turn Dark Link on to use it.");
    else if (mods_feature(MOD_FEATURE_N64, nullptr, 0) && !(vs && !strcmp(vs, "remake")))
        opt_disable_next_text("For the remake's Dark Link: Remake style above, or N64 Graphics off.");
    else if (cfg_from_env("OOTPC_DARK_LINK_EFFECTS")) opt_disable_next_text("Set by the environment.");
    if (opt_toggle_text("##mod_dark_link_fx", "Dark Link effects",
                        "The remake's Dark Link wears the enemy's own effects: the dark smoke round him and his eyes' "
                        "red glow and trail. Off saves a little GPU time.",
                        fx)) {
        cfg_set("OOTPC_DARK_LINK_EFFECTS", fx ? "0" : "1");
        opt_save_soon();
    }
}

// Play as Dark Link, right under Play as Lank: the Dark Link mod's switch (its folder made by the launcher's Make Dark
// Link, tools/make_dark_link_mod.py) with its style row; the mod's folder is then left out of the list below.
static void play_as_dark_link(void) {
    int at = -1;
    for (int i = 0; i < mods_count() && at < 0; i++) {
        const ModInfo *m = mods_at(i);
        if (m && !strcmp(m->feature, MOD_FEATURE_DARK_LINK)) at = i;
    }
    ModInfo *m = at >= 0 ? mods_at(at) : nullptr;
    if (!m) {
        opt_disable_next_text("Make the Dark Link mod first: the launcher's Mods page, Make Dark Link.");
        opt_toggle_text("##mod_play_dark", "Play as Dark Link", "Link plays as Dark Link. Applies at the next start.",
                        false);
        return;
    }
    bool on = m->enabled != 0;
    const char *value = on != (m->start_enabled != 0) ? (on ? "On after restart" : "Off after restart") : nullptr;
    if (m->problem != MOD_OK) opt_disable_next_text("This mod cannot be used: see the log.");
    if (opt_toggle_text("##mod_play_dark", "Play as Dark Link",
                        "Link plays as Dark Link: the adult with the game's own Dark Link models, sword, shield and "
                        "sheath, the child with his own models made near black with red eyes. With N64 Graphics, Dark "
                        "Link style picks how N64 Link looks. Applies at the next start.", on, value)) {
        mods_set_enabled(at, !on);
        opt_save_soon();
    }
    dark_link_style(on && m->problem == MOD_OK);
}

// What the mods made for this build do, by folder name; another mod shows its folder's state only.
static const char *mod_desc(const ModInfo *m) {
    const char *name = m->name;
    if (!strcmp(m->feature, MOD_FEATURE_STEAM_COOP))
        return "Co-op through Steam: the Co-op tab makes and joins rooms, and progress is shared with friends, each in "
               "their own game and save. Needs Steam running. Changes apply at the next start.";
    if (!strcmp(m->feature, MOD_FEATURE_DARK_LINK))
        return "Link plays as Dark Link: the adult with the game's own Dark Link models, sword, shield and sheath, the "
               "child with his own models made near black with red eyes. With N64 Graphics, Dark Link style picks "
               "how N64 Link looks. Changes apply at the next start.";
    static const struct { const char *name, *desc; } kKnown[] = {
        {"Fishing on horse", "The fishing rod can be used while riding, as the bow can."},
        {"Lake Hylia fishing", "The fishing pond owner also stands on the lake island, so the rod can be had "
                               "outside the pond. Adds bass and loaches that bite the lure and fish to catch in a "
                               "bottle to the lake, and takes the enemies off the lake and its shores, as child and "
                               "adult. Fish count sets how many fish."},
    };
    for (const auto &k : kKnown)
        if (!strcmp(k.name, name)) return k.desc;
    return "A mod from the mods folder. Changes apply at the next start.";
}

extern "C" void tab_mods(float scale) {
    opt_begin("##mods", scale);
    opt_group_text("Misc");
    if (!dbg_available()) opt_disable_next_text("Needs game version 1.1.1 or 1.0.0.");
    bool lank = dbg_lank_on() != 0;
    if (opt_toggle_text("##mod_lank", "Play as Lank", "Gives child Link the adult skeleton's proportions and adult Link "
                        "the child's. Keeps the current model, animations and gameplay age.", lank))
        dbg_lank(!lank);
    play_as_dark_link();
    if (!dbg_available()) opt_disable_next_text("Needs game version 1.1.1 or 1.0.0.");
    bool bunny = dbg_bunny_on() != 0;
    if (opt_toggle_text("##mod_bunny", "Bunny Hood", "While the Bunny Hood is worn, Link runs faster and jumps further. "
                        "Bunny speed sets how much.", bunny))
        dbg_bunny(!bunny);
    {
        static const char *const names[] = {"1.25x", "1.5x", "1.75x", "2x", "2.5x", "3x"};
        static const float vals[] = {1.25f, 1.5f, 1.75f, 2.0f, 2.5f, 3.0f};
        int cur = 1;
        float f = dbg_bunny_speed();
        for (int k = 0; k < 6; k++)
            if (f > vals[k] - 0.01f && f < vals[k] + 0.01f) cur = k;
        if (!bunny) opt_disable_next_text("Turn Bunny Hood on to use it.");
        int r = opt_text_choice("##mod_bunny_speed", "Bunny speed", names, 6, cur,
                                "How much further Link gets with the Bunny Hood on: running speed and jump distance.");
        if (r >= 0) dbg_set_bunny_speed(vals[r]);
    }
    if (!dbg_available()) opt_disable_next_text("Needs game version 1.1.1 or 1.0.0.");
    bool powers = dbg_mask_powers_on() != 0;
    if (opt_toggle_text("##mod_mask_powers", "Mask abilities", "While the Zora Mask is worn, Link swims about three times "
                        "faster and dives faster; while the Goron Mask is worn, he rolls twice as fast.", powers))
        dbg_mask_powers(!powers);
    cfg_toggle_row("##mod_no_invisible_walls", "NO_INVISIBLE_WALLS", "Remove invisible walls",
                   "Takes out the walls that only block Link (around ledges, cliffs, the edges of areas and roofs) "
                   "and leaves the solid scenery. Areas loaded after the switch have them gone; the Randomizer's own "
                   "setting does the same for a randomizer save.", false);
    for (int i = 0; i < mods_count(); i++) {
        ModInfo *m = mods_at(i);
        if (!m || !strcmp(m->feature, MOD_FEATURE_DARK_LINK)) continue;   // its row is Play as Dark Link above
        char id[300], sid[300];
        snprintf(id, sizeof id, "##mod_%d", i);
        snprintf(sid, sizeof sid, "##mod_set_%d", i);
        bool on = m->enabled != 0;
        const char *value = on != (m->start_enabled != 0) ? (on ? "On after restart" : "Off after restart") : nullptr;
        if (m->problem != MOD_OK) opt_disable_next_text("This mod cannot be used: see the log.");
        if (opt_toggle_text(id, m->name, mod_desc(m), on, value)) {
            mods_set_enabled(i, !on);
            opt_save_soon();
        }
        if (!strcmp(m->feature, MOD_FEATURE_N64)) n64_parts(on && m->problem == MOD_OK);
        // a setting several mods share is one row, under the first of them; it applies while any of them is on
        bool first = true, any_on = false;
        for (int j = 0; j < mods_count() && m->nvalues; j++) {
            const ModInfo *o = mods_at(j);
            if (!o || !o->nvalues || strcmp(o->setting, m->setting)) continue;
            if (j < i) first = false;
            any_on |= o->enabled != 0;
        }
        if (m->nvalues && first) {
            const char *names[MOD_VALUES];
            for (int k = 0; k < m->nvalues; k++) names[k] = m->values[k];
            char shown[96];
            const char *sv = nullptr;
            if (m->value != m->start_value) {
                snprintf(shown, sizeof shown, "%s after restart", m->values[m->value]);
                sv = shown;
            }
            if (!any_on) opt_disable_next_text("Turn a mod that uses this setting on to use it.");
            int r = opt_text_choice(sid, m->setting, names, m->nvalues, m->value,
                                    "Used by every mod with a setting of this name. Applies at the next start.", false, sv);
            if (r >= 0 && r != m->value) {
                mods_set_value(i, r);
                opt_save_soon();
            }
        }
    }
    opt_end();
}
