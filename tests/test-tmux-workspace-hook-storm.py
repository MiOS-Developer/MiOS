#!/usr/bin/env python3
# AI-hint: Two-sided regression test for the tmux-os resize flood: a private tmux server with the MiOS theme hooks and a real pty client resized 60 times; the native relay hook must spawn no interpreter, keep concurrency bounded, drain, and leave the workspace laid out (MIOS_TMUX_STORM_CONF/MIOS_TMUX_STORM_RELAY select the candidate; a legacy conf is the negative control).
# AI-related: tools/native/mios-agent-relay/src/workspace_hook.rs, tools/native/mios-service-core/src/tmux_theme.rs, usr/share/mios/tmux/mios-theme.tmux.conf, usr/libexec/mios/mios-mcp-server
# Run with the LIVE theme conf (python hooks, negative control) and the candidate conf (native relay hooks).
import fcntl, json, os, pty, signal, struct, subprocess, sys, termios, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
conf = os.environ.get('MIOS_TMUX_STORM_CONF', str(ROOT / 'usr/share/mios/tmux/mios-theme.tmux.conf'))
label = os.environ.get('MIOS_TMUX_STORM_LABEL', 'native')
relay = os.environ.get('MIOS_TMUX_STORM_RELAY') or None
base = Path(os.environ['XDG_RUNTIME_DIR']) / f'mios-stormtest-{label}'
base.mkdir(mode=0o700, exist_ok=True)
os.chmod(base, 0o700)
sock = str(base / 'mios-stormtest')
c = Path(conf).read_text()
if relay:
    c = c.replace("'/usr/libexec/mios/mios-agent-relay'", f"'{relay}'")
(base / 'tmux.conf').write_text(c)
env = dict(os.environ)
for k in ('TMUX', 'TMUX_PANE'):
    env.pop(k, None)
if relay:
    env['PATH'] = f"{Path(relay).parent}:{env['PATH']}"
T = lambda *a: subprocess.run(['tmux', '-S', sock, *a], env=env, capture_output=True, text=True, timeout=10)
T('kill-server')
T('-f', str(base / 'tmux.conf'), 'new-session', '-d', '-s', 'tmux-os', '-x', '200', '-y', '50', 'sleep 3600')
T('set-option', '-g', 'exit-empty', 'off')

ARGS=set()
def census():
    out = subprocess.run(['ps', '-u', str(os.getuid()), '-o', 'pid=,args='], capture_output=True, text=True).stdout
    py, rl = set(), set()
    for line in out.splitlines():
        pid, _, args = line.strip().partition(' ')
        if 'mios-mcp-server' in args and ('--workspace' in args):
            py.add(pid); ARGS.add(args[:160])
        if '--workspace-hook' in args or ('mios-agent-relay' in args and '--workspace' in args):
            rl.add(pid)
    return py, rl

pid, fd = pty.fork()
if pid == 0:
    os.execvpe('tmux', ['tmux', '-S', sock, 'attach', '-t', 'tmux-os'], env)
seen_py, seen_rl, peak = set(), set(), 0
def winsz(cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    os.kill(pid, signal.SIGWINCH)
def drain():
    try:
        while True:
            if not os.read(fd, 65536):
                break
    except (BlockingIOError, OSError):
        pass
os.set_blocking(fd, False)
winsz(200, 50)
t_end = time.monotonic() + 3
while time.monotonic() < t_end:
    drain(); time.sleep(0.05)
sizes = [(200, 50), (120, 40), (80, 24), (160, 48), (100, 30), (220, 60)]
for i in range(60):
    winsz(*sizes[i % len(sizes)])
    drain()
    py, rl = census()
    seen_py |= py; seen_rl |= rl
    peak = max(peak, len(py) + len(rl))
    time.sleep(0.05)
# settle at a final size and let everything finish
winsz(160, 48)
settle_end = time.monotonic() + 20
while time.monotonic() < settle_end:
    drain()
    py, rl = census(); seen_py |= py; seen_rl |= rl; peak = max(peak, len(py) + len(rl))
    if not py and not rl:
        break
    time.sleep(0.2)
left_py, left_rl = census()
win = T('display-message', '-p', '-t', 'tmux-os:', '#{window_width}x#{window_height}|#{@mios-workspace-applied}|#{@mios-workspace-head}').stdout.strip()
panes = T('list-panes', '-a', '-F', '#{pane_id}').stdout.split()
res = {'label': label, 'python_spawned': len(seen_py), 'relay_spawned': len(seen_rl), 'peak_concurrent': peak,
       'left_running': len(left_py) + len(left_rl), 'window': win, 'panes': len(panes)}
print(json.dumps(res), flush=True)

try:
    os.kill(pid, signal.SIGTERM)
except ProcessLookupError:
    pass
T('kill-server')

if label == 'native':
    ok = (res['python_spawned'] == 0 and res['peak_concurrent'] <= 24 and res['left_running'] == 0
          and res['window'].split('|')[0] == res['window'].split('|')[1] and res['window'].split('|')[2].startswith('%'))
else:  # negative control: the legacy hooks flood
    ok = res['python_spawned'] > 20 and res['peak_concurrent'] > 50
print('PASS' if ok else 'FAIL', label)
sys.exit(0 if ok else 1)
