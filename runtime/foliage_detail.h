// Cosmetic detail policy for indirect foliage draws. Ordinary geometry and colour passes keep their shadows policy unchanged.
#ifndef OOTPC_FOLIAGE_DETAIL_H
#define OOTPC_FOLIAGE_DETAIL_H
// Modes: 0 full, 1 reduced (omit task-stage grass/moss), 2 off.
static inline int foliage_detail_skip(int foliage, int task_stage, int depth_only, int detail, int shadows) {
    if (!foliage) return 0;
    if (detail == 2 || (detail == 1 && task_stage)) return 1;
    return depth_only && (shadows == 2 || (shadows == 1 && task_stage));
}
#endif
