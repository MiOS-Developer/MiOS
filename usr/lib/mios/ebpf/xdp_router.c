// AI-hint: Native eBPF XDP network fastpath and WireGuard packet router (task_746).
// AI-doc: usr/share/doc/mios/manual/ch18-ebpf-xdp-wireguard-fastpath.md

#ifndef __KERNEL__
#define __KERNEL__
#endif

#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/ip.h>
#include <linux/in.h>
#include <linux/udp.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

#define WIREGUARD_PORT 51820

struct bpf_map_def {
    unsigned int type;
    unsigned int key_size;
    unsigned int value_size;
    unsigned int max_entries;
    unsigned int map_flags;
};

// Packet counter stats
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __type(key, __u32);
    __type(value, __u64);
    __uint(max_entries, 4); // 0=PASS, 1=DROP, 2=REDIRECT, 3=WIREGUARD
} xdp_stats SEC(".maps");

static __always_inline void increment_stat(__u32 action) {
    __u64 *val = bpf_map_lookup_elem(&xdp_stats, &action);
    if (val) {
        *val += 1;
    }
}

SEC("xdp")
int xdp_router_func(struct xdp_md *ctx) {
    void *data = (void *)(long)ctx->data;
    void *data_end = (void *)(long)ctx->data_end;

    // Parse Ethernet header
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) {
        increment_stat(1); // DROP
        return XDP_DROP;
    }

    if (eth->h_proto != bpf_htons(ETH_P_IP)) {
        increment_stat(0); // PASS
        return XDP_PASS;
    }

    // Parse IPv4 header
    struct iphdr *ip = (void *)(eth + 1);
    if ((void *)(ip + 1) > data_end) {
        increment_stat(1); // DROP
        return XDP_DROP;
    }

    // Check for UDP
    if (ip->protocol != IPPROTO_UDP) {
        increment_stat(0); // PASS
        return XDP_PASS;
    }

    // Parse UDP header
    struct udphdr *udp = (void *)(ip + 1);
    if ((void *)(udp + 1) > data_end) {
        increment_stat(1); // DROP
        return XDP_DROP;
    }

    // WireGuard packet inspection
    if (udp->dest == bpf_htons(WIREGUARD_PORT) || udp->source == bpf_htons(WIREGUARD_PORT)) {
        increment_stat(3); // WIREGUARD
        // Fastpath pass to WireGuard stack directly
        return XDP_PASS;
    }

    increment_stat(0); // PASS
    return XDP_PASS;
}

char _license[] SEC("license") = "GPL";
