#!/usr/bin/env python3
# AI-hint: Regression test for the zz-mios-motd snippet of etc/profile.d/50-mios.sh: an interactive non-tmux login shell auto-execs mios-terminal exactly once; nested interactive shells (MIOS_MOTD_SHOWN exported) stay plain shells; tmux panes still run --workspace-ensure.
# AI-related: etc/profile.d/50-mios.sh (Snippet: zz-mios-motd.sh), usr/libexec/mios/mios-terminal
# Never runs the real mios-terminal: the snippet is extracted and its absolute
# helper paths are rewritten to stubs in a private temp directory.
import os
import pty
import select
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = Path(os.environ.get("MIOS_PROFILE", ROOT / "etc/profile.d/50-mios.sh"))
GUARD = '    [ -z "${MIOS_MOTD_SHOWN:-}" ] || return 0\n'
FAILS = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{(': ' + detail) if detail and not cond else ''}")
    if not cond:
        FAILS.append(name)


def extract_snippet(text):
    start = text.index("# Snippet: zz-mios-motd.sh")
    end = text.index("unset -f _mios_profile_step", start)
    return text[start:end]


def build(tmp, snippet, auto_attach="true"):
    stub = tmp / "stub"
    stub.mkdir()
    log = tmp / "calls.log"
    for real, name in (("/usr/libexec/mios/mios-terminal", "mios-terminal"),
                       ("/usr/bin/mios-toml-get", "mios-toml-get"),
                       ("/usr/libexec/mios/mios-mcp-server", "mios-mcp-server")):
        snippet = snippet.replace(real, str(stub / name))
    (tmp / "snippet.sh").write_text(snippet)
    # The stub terminal records itself, then opens a nested interactive shell
    # that sources the same snippet (what a user does inside the terminal).
    # Depth is capped so a defective snippet cannot loop forever.
    (stub / "mios-terminal").write_text(
        "#!/bin/bash\n"
        f"echo \"terminal SHOWN=${{MIOS_MOTD_SHOWN:-}}\" >> {log}\n"
        f"[ \"$(grep -c ^terminal {log})\" -lt 3 ] || exit 0\n"
        f"exec /bin/bash --noprofile --norc -i -c '. {tmp}/snippet.sh; echo NESTED-PLAIN >> {log}'\n")
    (stub / "mios-toml-get").write_text(
        "#!/bin/bash\n"
        "case \"$1 $2\" in\n"
        f"  'terminal.startup auto_attach') echo {auto_attach} ;;\n"
        f"  'mcp python') echo {stub}/py ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n")
    (stub / "py").write_text(f"#!/bin/bash\necho \"workspace $*\" >> {log}\n")
    (stub / "mios-mcp-server").write_text("")
    for f in ("mios-terminal", "mios-toml-get", "py"):
        (stub / f).chmod(0o755)


def run_tty(tmp, env_extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("TMUX", "STY", "MIOS_MOTD_SHOWN", "MIOS_SKIP_MOTD",
                        "MIOS_TMUX_HELPER", "MIOS_SESSION_BARE")}
    env.update(env_extra)
    log = tmp / "calls.log"
    cmd = ["/bin/bash", "--noprofile", "--norc", "-i", "-c",
           f". {tmp}/snippet.sh; echo LOGIN-RETURNED >> {log}"]
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(cmd[0], cmd, env)
    deadline = time.time() + 20
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                if not os.read(fd, 4096):
                    break
            except OSError:
                break
        if os.waitpid(pid, os.WNOHANG)[0]:
            pid = 0
            break
    if pid:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    os.close(fd)
    return log.read_text().splitlines() if log.exists() else []


def scenario(snippet, env_extra, auto_attach="true"):
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        build(tmp, snippet, auto_attach)
        return run_tty(tmp, env_extra)


def main():
    snippet = extract_snippet(PROFILE.read_text())
    check("snippet carries the MIOS_MOTD_SHOWN re-entry guard", GUARD in snippet)

    # POSITIVE: a login shell auto-execs mios-terminal once, guard exported to it.
    calls = scenario(snippet, {})
    terminals = [c for c in calls if c.startswith("terminal")]
    check("login shell execs mios-terminal exactly once", terminals == ["terminal SHOWN=1"], str(calls))
    check("nested interactive shell stays plain (no re-exec)", "NESTED-PLAIN" in calls, str(calls))
    check("login shell does not continue past exec", "LOGIN-RETURNED" not in calls, str(calls))

    # NEGATIVE: an inherited guard skips auto-exec entirely.
    calls = scenario(snippet, {"MIOS_MOTD_SHOWN": "1"})
    check("inherited MIOS_MOTD_SHOWN skips auto-exec", calls == ["LOGIN-RETURNED"], str(calls))

    # POSITIVE: a tmux pane (inherits the guard from the server) still ensures its workspace.
    calls = scenario(snippet, {"MIOS_MOTD_SHOWN": "1", "TMUX": "/tmp/fake,1,0"})
    check("tmux pane still runs --workspace-ensure",
          any("--workspace-ensure" in c for c in calls) and not any(c.startswith("terminal") for c in calls),
          str(calls))

    # POSITIVE: auto_attach=false never execs and leaves the shell usable.
    calls = scenario(snippet, {}, auto_attach="false")
    check("auto_attach=false does not exec", calls == ["LOGIN-RETURNED"], str(calls))

    # DEFECT CONTROL: the same snippet without the guard re-execs in nested shells,
    # proving the scenarios above detect the regression.
    calls = scenario(snippet.replace(GUARD, ""), {})
    check("defect control: unguarded snippet re-execs in the nested shell",
          sum(c.startswith("terminal") for c in calls) > 1, str(calls))

    print(f"{'FAIL' if FAILS else 'OK'}: {len(FAILS)} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
