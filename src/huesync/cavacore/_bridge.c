/*
 * Thin accessor bridge for the cava_plan struct.
 *
 * cava_plan contains opaque fftw_plan pointer fields (typedef void *fftw_plan),
 * so defining the full struct in Python ctypes would require fftw3 type knowledge.
 * These helper functions expose only the fields that the Python binding needs,
 * keeping the rest of the struct opaque.
 */

#include <stdlib.h>
#include "cavacore.h"

int cavacore_status(struct cava_plan *p) {
    return p->status;
}

int cavacore_n_bars(struct cava_plan *p) {
    return p->number_of_bars;
}

int cavacore_channels(struct cava_plan *p) {
    return p->audio_channels;
}

const char *cavacore_error(struct cava_plan *p) {
    return p->error_message;
}

/*
 * Free a cava_plan that was returned by cava_init() with a non-zero status
 * (invalid parameters).  On the error path cava_init() returns early after
 * malloc()-ing only the plan struct itself — the inner audio buffers and FFTW
 * plans are not allocated.  Calling cava_destroy() on such a struct would
 * invoke free() on uninitialised pointer fields, causing undefined behaviour.
 * This function frees only the plan struct, which is the only allocation made
 * by cava_init() before an early return.
 */
void cavacore_free_failed(struct cava_plan *p) {
    free(p);
}

/*
 * Fully destroy a successfully initialised cava_plan.
 *
 * cava_destroy() (upstream) releases all inner audio buffers and FFTW plans
 * allocated during cava_init() but does NOT call free() on the plan struct
 * itself.  This wrapper performs both steps so the caller has a single,
 * auditable cleanup path for plans that initialised successfully.
 *
 * Do NOT use this for failed plans (non-zero status) — use
 * cavacore_free_failed() for those, because their inner pointers were never
 * initialised and calling cava_destroy() on them is undefined behaviour.
 */
void cavacore_close_plan(struct cava_plan *p) {
    cava_destroy(p);  /* free inner buffers and FFTW plans */
    free(p);          /* free the plan struct itself (cava_destroy does not) */
}
