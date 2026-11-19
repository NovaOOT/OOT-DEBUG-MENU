// A MIDI file played on the ocarina (midi_ocarina.c): the file's melody becomes the controller presses the game
// reads for its ocarina notes, held for each note's length, while the ocarina is out. The Debug tab's "Ocarina MIDI
// player" rows pick the file and start it; a hotkey can start and stop it too.
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { MIDI_OC_IDLE, MIDI_OC_WAITING, MIDI_OC_PLAYING, MIDI_OC_DONE };

// Loads a .mid file (UTF-8 path) and picks its melody track; 1 on success, else 0 with the reason in err.
int midi_oc_load(const char *path, char *err, size_t cap);
int midi_oc_loaded(void);
const char *midi_oc_file(void);   // the loaded file's name without folders, "" when none

// The tracks with notes: midi_oc_track_count() of them, each labelled "3: Flute (212 notes)". The melody is one track
// or, with track -1, every track at once (the highest note sounding wins).
int midi_oc_track_count(void);
const char *midi_oc_track_label(int i);
int midi_oc_track(void);          // the track in use, -1 for all
void midi_oc_set_track(int i);    // -1 for all
int midi_oc_auto_track(void);     // the track picked when the file loaded

// Semitones the melody is moved by: the automatic shift (the one that puts most notes on the ocarina's own) plus the
// player's offset.
int midi_oc_transpose_auto(void);
int midi_oc_transpose(void);      // the player's offset
void midi_oc_set_transpose(int semitones);
// Playback speed in percent (25 to 400).
int midi_oc_speed(void);
void midi_oc_set_speed(int percent);

// Starts playback: at once with the ocarina out, else when it next comes out. midi_oc_stop ends it.
void midi_oc_play(void);
void midi_oc_stop(void);
void midi_oc_toggle(void);
int midi_oc_state(void);          // MIDI_OC_*
// Where playback is and how long the melody is, in seconds of the file (before the speed).
void midi_oc_progress(double *at, double *len);
int midi_oc_notes(void);          // notes in the melody as played
int midi_oc_bent(void);           // ... of them moved to a neighbouring note: the ocarina has no such note

// input.c input_poll, after the menu and text entry gates: ORs the notes' buttons into the game's input.
void midi_oc_input(uint64_t *buttons, int32_t *lx, int32_t *ly, int32_t *rx, int32_t *ry);
// The buttons the player held at its last poll (none once it is 0.1 s old), for the inputs readout; a stick it held
// replaces *lx, *ly (left) or *rx, *ry (right).
uint64_t midi_oc_pressed(int32_t *lx, int32_t *ly, int32_t *rx, int32_t *ry);

#ifdef __cplusplus
}
#endif
