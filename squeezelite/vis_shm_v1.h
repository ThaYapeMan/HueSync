/*
 * HueSync v1 SHM ABI — shared between squeezelite producer and Python consumer.
 *
 * This header defines the extension block appended immediately after the legacy
 * squeezelite vis_t header (at offset 80 = _HDR_OFFSET + _HDR_SIZE).
 *
 * Layout (40 bytes):
 *   offset  0: uint32_t magic         = VIS_SHM_V1_MAGIC (0x48555345 'HUSE')
 *   offset  4: uint16_t abi_version   = 1
 *   offset  6: uint16_t flags         = 0 (reserved)
 *   offset  8: uint32_t write_seq     = seqlock counter (even=stable, odd=writing)
 *   offset 12: uint64_t generation    = producer lifetime ID (random, changes on restart)
 *   offset 20: uint64_t abs_write_pos = exclusive next stereo-frame position (monotonic)
 *   offset 28: uint64_t gap_seq       = monotonic gap sequence (increments on skipped export)
 *   offset 36: uint8_t  _pad[4]       = 0
 *   Total: 40 bytes
 *
 * All multi-byte fields are little-endian.
 *
 * abs_write_pos unit: stereo frames (one stereo frame = 2* int16_t = 4 bytes).
 * It is the exclusive next write position — after writing N frames, it equals
 * the count of all frames ever written by this producer instance.
 *
 * Seqlock protocol:
 *   Writer: atomic increment write_seq (odd), update all fields, atomic increment write_seq (even)
 *   Reader: read write_seq1; reject if odd; read fields; read write_seq2; accept only if seq1==seq2
 *
 * Generation: initialised from arc4random_buf() or getrandom() on startup.
 * A restarted squeezelite process has a different generation with overwhelming probability.
 */

#pragma once
#include <stdint.h>
#include <stddef.h>

#define VIS_SHM_V1_MAGIC     UINT32_C(0x48555345)  /* 'HUSE' */
#define VIS_SHM_V1_VERSION   UINT16_C(1)

/* The extension block begins immediately after the legacy vis_t header. */
#define VIS_SHM_V1_EXT_OFFSET 80U   /* = offsetof(vis_t, pthread_rwlock) + sizeof(pthread_rwlock_t) +
                                        sizeof(buf_size) + sizeof(buf_index) + sizeof(running) +
                                        sizeof(rate) + sizeof(updated) */

typedef struct __attribute__((packed)) vis_shm_v1_ext {
    uint32_t magic;           /* VIS_SHM_V1_MAGIC */
    uint16_t abi_version;     /* VIS_SHM_V1_VERSION */
    uint16_t flags;           /* reserved, write 0 */
    uint32_t write_seq;       /* seqlock (even=stable, odd=writing) */
    uint64_t generation;      /* random uint64 per squeezelite process */
    uint64_t abs_write_pos;   /* exclusive next-stereo-frame position */
    uint64_t gap_seq;         /* skipped-export counter */
    uint8_t  _pad[4];
} vis_shm_v1_ext_t;

_Static_assert(sizeof(vis_shm_v1_ext_t) == 40, "vis_shm_v1_ext_t must be 40 bytes");
_Static_assert(offsetof(vis_shm_v1_ext_t, magic)         ==  0, "magic offset");
_Static_assert(offsetof(vis_shm_v1_ext_t, abi_version)   ==  4, "abi_version offset");
_Static_assert(offsetof(vis_shm_v1_ext_t, flags)         ==  6, "flags offset");
_Static_assert(offsetof(vis_shm_v1_ext_t, write_seq)     ==  8, "write_seq offset");
_Static_assert(offsetof(vis_shm_v1_ext_t, generation)    == 12, "generation offset");
_Static_assert(offsetof(vis_shm_v1_ext_t, abs_write_pos) == 20, "abs_write_pos offset");
_Static_assert(offsetof(vis_shm_v1_ext_t, gap_seq)       == 28, "gap_seq offset");
