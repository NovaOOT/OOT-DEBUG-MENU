// The save tree (save_tree.c). While a save is played, every lasting change the player makes (a flag that starts a
// branch, save_tree.c background_flag: a rock broken, a chest opened, a talk, a cutscene, an item, a door, a switch)
// makes the game save itself to the slot it plays, and that save, or one the game makes itself after such a change,
// becomes a node of one tree under the node the game was on, labelled with what changed since. A node is a real save of
// one slot: it loads in the game and can be given to someone else. The tree holds every path ever played: something
// else done from a node is a new branch beside the first, and a step made again from the same node (the same flags to
// the same values in the same place) goes to the node it made before. Where the game makes no save (the escape from the
// collapsing castle keeps its saves aside), the tree writes the running game's flags over the current node's files
// itself (save_tree_build).
//
// <game folder>/saves/tree:
//   tree.jsonl        one line per node, oldest first: {"id", "parent" (-1 for a root), "plen", "hash", "sig",
//                     "time", "slot", "kind" (load, flag, autosave, manual), "area", "label", "n" (how many flags
//                     changed), "changes" [[type, flag name, old value, new value], ...] (at most CHANGES_KEPT)}
//   nodes/<id / 1000>/<id>/slot_NN/caption.sav and progress.sav   the node's save: the game's own files of slot NN as
//                     it wrote them, the folder the launcher's Import a Save Folder takes
//   current.txt       the node the running game is on, a line "<id>"
//   load.txt          written by the launcher's Save Tree window: a node id to load in the running game
//   noisy.txt         flags always left out, one key (8 hex digits) per line, written by hand. Others are left out
//                     by themselves while they change all the time (a timer, the clock), until they settle
//   status.txt        a line for the window: what the tree is doing
// Off with debug_save_tree = 0.
#pragma once
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// From the debug frame hook (debug_warp.c dbg_game_frame, through debug_transform.c): starts the tree's thread once.
void save_tree_tick(void);
// fs_host.c: the game opened slot `slot`'s progress.sav for reading at `path` (a host path): it loads that slot.
void save_tree_note_read(const char *path, int slot);
// fs_host.c live_copy: a Commit changed slot `slot`, whose folder in the newest save is dir; kind "autosave", "manual"
// or "" and place (the caption's LocationName) as live_copy read them. Reads the slot's files; tx_lock is held.
void save_tree_committed(const char *dir, int slot, const char *kind, const char *place);
// The node the game is on, -1 when none; nodes in the tree. For the menu.
int save_tree_current(void);
int save_tree_count(void);
// 1 once after a node to load was staged while the game played: the render thread restarts the game, which loads it
// (ui/debug_tab.cpp debug_overlay_draw, as for a profile switch). Not while the game writes its save.
int save_tree_restart_due(void);
// Writes the running game's flags over a save file's values (progress.sav or caption.sav, data of n bytes, a template
// the game wrote), for a save where the game makes none. patched: the values it wrote, changed: those that differed.
// 1 when it wrote any; 0 when data is not a save file or the flag stores are not found yet.
int save_tree_build(unsigned char *data, size_t n, int *patched, int *changed);

#ifdef __cplusplus
}
#endif
