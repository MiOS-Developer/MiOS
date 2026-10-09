// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
/* AI-hint: In-kernel eBPF network flow aggregation probe for MiOS (task_455).
 * AI-doc: usr/share/doc/mios/manual/ch06-security.md
 */

#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/in.h>
#include <linux/tcp.h>
#include <linux/udp.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

char LICENSE[] SEC("license") = "Dual BSD/GPL";

struct flow_key_t {
    __u32 saddr;
    __u32 daddr;
    __u16 sport;
    __u16 dport;
    __u8  proto;
    __u8  pad[3];
};

struct flow_metrics_t {
    __u64 packets;
    __u64 bytes;
    __u64 start_ts;
    __u64 last_ts;
};

/* Per-CPU hash map for aggregated 5-tuple flows to eliminate cross-CPU locks */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_HASH);
    __uint(max_entries, 65536);
    __type(key, struct flow_key_t);
    __type(value, struct flow_metrics_t);
} flow_table SEC(".maps");

/* Ring buffer to stream summarized flow records to userspace mios-netflowd */
struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, 1 << 20); // 1MB ring buffer
} flow_ringbuf SEC(".maps");

SEC("tc")
int mios_tc_flow_probe(struct __sk_buff *skb) {
    void *data = (void *)(long)skb->data;
    void *data_end = (void *)(long)skb->data_end;

    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end)
        return BPF_OK;

    if (eth->h_proto != bpf_htons(ETH_P_IP))
        return BPF_OK;

    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end)
        return BPF_OK;

    struct flow_key_t key = {};
    key.saddr = ip->saddr;
    key.daddr = ip->daddr;
    key.proto = ip->protocol;

    if (ip->protocol == IPPROTO_TCP) {
        struct tcphdr *tcp = (void *)((__u32 *)ip + ip->ihl);
        if ((void *)(tcp + 1) > data_end)
            return BPF_OK;
        key.sport = tcp->source;
        key.dport = tcp->dest;
    } else if (ip->protocol == IPPROTO_UDP) {
        struct udphdr *udp = (void *)((__u32 *)ip + ip->ihl);
        if ((void *)(udp + 1) > data_end)
            return BPF_OK;
        key.sport = udp->source;
        key.dport = udp->dest;
    } else {
        return BPF_OK;
    }

    __u64 now = bpf_ktime_get_ns();
    __u32 len = skb->len;

    struct flow_metrics_t *metrics = bpf_map_lookup_elem(&flow_table, &key);
    if (metrics) {
        metrics->packets += 1;
        metrics->bytes += len;
        metrics->last_ts = now;
    } else {
        struct flow_metrics_t new_metrics = {
            .packets = 1,
            .bytes = len,
            .start_ts = now,
            .last_ts = now,
        };
        bpf_map_update_elem(&flow_table, &key, &new_metrics, BPF_NOEXIST);
    }

    return BPF_OK;
}
