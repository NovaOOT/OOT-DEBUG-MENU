#pragma once
#include "host.h"

// Dark Link's standing effects (the black smoke, the eyes' glow and trail) kept playing on the adult player while the
// Dark Link mod plays with the remake's models (dark_link_fx.c). Once a frame, on the game thread.
void dark_link_fx_frame(Cpu *c);
