# AI-hint: task_210 seccomp filter builder for the risk-tier dispatch sandbox.
# AI-doc: usr/share/doc/mios/manual/access.md
"""Classic-BPF seccomp filters for the dispatch sandbox (task_210)."""

from __future__ import annotations

import struct
from typing import Iterable, Sequence

# BPF opcodes (linux/filter.h) -- the three this program needs.
_LD_ABS_W = 0x20      # BPF_LD | BPF_W | BPF_ABS
_JEQ_K = 0x15         # BPF_JMP | BPF_JEQ | BPF_K
_RET_K = 0x06         # BPF_RET | BPF_K

# seccomp return actions (linux/seccomp.h).
RET_ALLOW = 0x7FFF0000
RET_KILL_PROCESS = 0x80000000
_RET_ERRNO = 0x00050000
_EPERM = 1

# struct seccomp_data { int nr; __u32 arch; ... } -- the two offsets used.
_OFF_NR = 0
_OFF_ARCH = 4

AUDIT_ARCH = {
    "x86_64": 0xC000003E,
}

# Kernel ABI, per architecture. Only architectures whose numbers are VERIFIED
# appear here; see the sibling test, which re-derives them from asm/unistd.
SYSCALLS = {
    "x86_64": {
        "mount": 165, "umount2": 166, "pivot_root": 155, "chroot": 161,
        "ptrace": 101, "process_vm_readv": 310, "process_vm_writev": 311,
        "kexec_load": 246, "kexec_file_load": 320,
        "init_module": 175, "finit_module": 313, "delete_module": 176,
        "bpf": 321, "perf_event_open": 298,
        "add_key": 248, "keyctl": 250, "request_key": 249,
        "swapon": 167, "swapoff": 168, "reboot": 169,
        "settimeofday": 164, "clock_settime": 227,
        "setns": 308, "userfaultfd": 323,
        "open_by_handle_at": 304, "name_to_handle_at": 303,
    },
}

# The floor. An operator may extend the SSOT list; they cannot shrink below this.
BASELINE_DENY = (
    "mount", "umount2", "pivot_root", "chroot", "ptrace",
    "init_module", "finit_module", "delete_module",
    "kexec_load", "kexec_file_load", "bpf", "perf_event_open",
    "add_key", "keyctl", "request_key", "setns", "userfaultfd",
    "open_by_handle_at", "name_to_handle_at",
)

class SeccompUnsupported(Exception):
    """No verified syscall table for this architecture."""

def audit_arch(machine: str) -> int:
    arch = AUDIT_ARCH.get(str(machine or ""))
    if arch is None:
        raise SeccompUnsupported(f"no verified seccomp table for {machine!r}")
    return arch

def syscall_numbers(machine: str) -> dict:
    tbl = SYSCALLS.get(str(machine or ""))
    if not tbl:
        raise SeccompUnsupported(f"no verified seccomp table for {machine!r}")
    return dict(tbl)

def resolve_denylist(machine: str, extra: Iterable[str] = ()) -> "list[int]":
    """Sorted syscall numbers to deny: the baseline floor plus SSOT extras.

    An unknown name is dropped, never guessed. Manual ch62."""
    tbl = syscall_numbers(machine)
    names = set(BASELINE_DENY) | {str(e).strip() for e in (extra or ()) if str(e).strip()}
    nrs = sorted({tbl[n] for n in names if n in tbl})
    if not nrs:
        raise SeccompUnsupported("denylist resolved to nothing on this architecture")
    return nrs

def _insn(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("<HBBI", code, jt & 0xFF, jf & 0xFF, k & 0xFFFFFFFF)

def build_filter(machine: str, denied: Sequence[int], *, action: str = "errno") -> bytes:
    """The sock_filter program bwrap reads from --seccomp FD.

    Arch mismatch denies; >255 denied syscalls is refused. Manual ch62."""
    arch = audit_arch(machine)
    nrs = list(denied or ())
    if not nrs:
        raise SeccompUnsupported("refusing to build a filter that denies nothing")
    if len(nrs) > 255:
        raise SeccompUnsupported(f"{len(nrs)} denied syscalls exceeds the jump range")
    deny = RET_KILL_PROCESS if str(action).strip().lower() == "kill" else (_RET_ERRNO | _EPERM)

    n = len(nrs)
    prog = [
        _insn(_LD_ABS_W, 0, 0, _OFF_ARCH),
        _insn(_JEQ_K, 0, n + 1, arch),      # wrong arch -> fall to DENY
        _insn(_LD_ABS_W, 0, 0, _OFF_NR),
    ]
    for idx, nr in enumerate(nrs):
        prog.append(_insn(_JEQ_K, n - idx, 0, nr))   # hit -> jump to DENY
    prog.append(_insn(_RET_K, 0, 0, RET_ALLOW))
    prog.append(_insn(_RET_K, 0, 0, deny))
    return b"".join(prog)
