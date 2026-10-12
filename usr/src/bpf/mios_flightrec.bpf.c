// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
/* AI-hint: In-kernel eBPF circular flight recorder probe for MiOS (task_460).
 * AI-doc: usr/share/doc/mios/manual/ch02-architecture.md
 */

#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

#define EVENT_TYPE_SYSCALL 1
#define EVENT_TYPE_IO      2
#define EVENT_TYPE_GPU     3
#define EVENT_TYPE_PANIC   4

#define RING_ENTRIES 2048

struct flight_record_t {
    __u64 timestamp_ns;
    __u32 pid;
    __u32 tid;
    __u32 event_type;
    __u32 latency_us;
    char  comm[16];
    char  detail[64];
};

/* Circular array for persistent pre-panic event retention */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, RING_ENTRIES);
    __type(key, __u32);
    __type(value, struct flight_record_t);
} flight_ring SEC(".maps");

/* Ring buffer for real-time daemon streaming */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 1 << 20); // 1MB ring buffer
} flight_ringbuf SEC(".maps");

/* Monotonic ring index */
struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u32);
} ring_cursor SEC(".maps");

static __always_inline void record_flight_event(__u32 type, __u32 latency, const char *detail) {
    __u32 zero = 0;
    __u32 *cursor = bpf_map_lookup_elem(&ring_cursor, &zero);
    if (!cursor)
        return;

    __u32 idx = __sync_fetch_and_add(cursor, 1) % RING_ENTRIES;

    struct flight_record_t rec = {};
    rec.timestamp_ns = bpf_ktime_get_ns();
    __u64 pid_tgid = bpf_get_current_pid_tgid();
    rec.pid = pid_tgid >> 32;
    rec.tid = (__u32)pid_tgid;
    rec.event_type = type;
    rec.latency_us = latency;
    bpf_get_current_comm(&rec.comm, sizeof(rec.comm));

    if (detail) {
        #pragma unroll
        for (int i = 0; i < 63; i++) {
            rec.detail[i] = detail[i];
            if (detail[i] == '\0')
                break;
        }
        rec.detail[63] = '\0';
    }

    bpf_map_update_elem(&flight_ring, &idx, &rec, BPF_ANY);

    /* Stream to ring buffer if active */
    struct flight_record_t *buf_rec = bpf_ringbuf_reserve(&flight_ringbuf, sizeof(struct flight_record_t), 0);
    if (buf_rec) {
        *buf_rec = rec;
        bpf_ringbuf_submit(buf_rec, 0);
    }
}

SEC("tracepoint/raw_syscalls/sys_enter")
int trace_sys_enter(void *ctx) {
    record_flight_event(EVENT_TYPE_SYSCALL, 0, "syscall_enter");
    return 0;
}

SEC("tracepoint/block/block_rq_complete")
int trace_block_rq_complete(void *ctx) {
    record_flight_event(EVENT_TYPE_IO, 150, "io_rq_complete");
    return 0;
}

SEC("kprobe/panic")
int trace_panic(struct pt_regs *ctx) {
    record_flight_event(EVENT_TYPE_PANIC, 0, "kernel_panic_trap");
    return 0;
}
