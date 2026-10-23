#!/usr/bin/env python3
# AI-hint: MiOS Unified TUI App -- The single cross-platform shared surface.
"""
MiOS-Mon -- The ONE singular unified MiOS monitoring, dashboard & TUI application.
Provides static snapshot modes (--mini, --dash) using pure rich, and a fully interactive
fullscreen TUI (--monitor) using Textual for btop-like hardware monitoring and live logs.
"""

import sys
import os
import time
import re
import glob
import json
import socket
import shutil
import platform
import subprocess
from datetime import datetime
import argparse
import threading
import signal
import xml.etree.ElementTree as ET
from collections import deque

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "lib", "mios")))
from mios_toml import colors as mios_colors, layer_paths, load_merged, process_val

def _install_deps(pkgs=None):
    if pkgs is None:
        pkgs = ["rich", "textual", "psutil"]
    try:
        import pip  # noqa: F401
    except ImportError:
        return False
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])
        return True
    except Exception:
        return False

try:
    from rich.console import Console, Group
    from rich.theme import Theme as RichTheme
    from rich.panel import Panel
    from rich.text import Text
    from rich.markup import escape
    from rich.table import Table
    from rich.align import Align
    from rich.columns import Columns
    from rich import box
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    if _install_deps(["rich"]):
        try:
            from rich.console import Console, Group
            from rich.panel import Panel
            from rich.text import Text
            from rich.markup import escape
            from rich.table import Table
            from rich.align import Align
            from rich.columns import Columns
            from rich import box
            RICH_AVAILABLE = True
        except ImportError:
            pass

if os.environ.get("TMUX") and os.environ.get("TERM", "").startswith(("tmux", "screen")):
    os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import Header, Footer, Static, RichLog, TabbedContent, TabPane, DataTable, Sparkline, Label
    from textual.containers import Grid, Vertical, Horizontal
    from textual.reactive import reactive
    from textual.theme import Theme
    from textual import on
    from mios_agent_tui import ClientView, AgentView, SystemSummary
    import psutil
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

IS_WINDOWS = platform.system() == 'Windows'
if RICH_AVAILABLE:
    _rich_palette = mios_colors(data=load_merged())
    _rich_theme = RichTheme({name: _rich_palette[key] for name, key in {
        "red": "error", "green": "success", "yellow": "warning",
        "blue": "accent", "cyan": "subtle", "magenta": "earth",
        "white": "fg", "black": "bg"}.items()})
    console = Console(safe_box=False, theme=_rich_theme)
else:
    class FallbackConsole:
        def print(self, *args, **kwargs):
            for a in args:
                print(str(a))
        def clear(self):
            subprocess.run(["cmd.exe", "/c", "cls"] if os.name == "nt" else ["clear"], check=False)
    console = FallbackConsole()

_SYS_INFO_CACHE = None
_USB_INFO_CACHE = "Scanning USB..."
_GIT_STATUS_CACHE = "[dim]Git state loading...[/]"
PIPELINE_MODE = False
AI_MODE = False
TAB_CHOICE = None

_MONITOR_CONFIG_CACHE = None
_AGENT_SNAPSHOT_CACHE = None

def monitor_config():
    """Reuse parsed SSOT until a layer or fragment changes on disk."""
    global _MONITOR_CONFIG_CACHE
    paths = _monitor_layer_paths()
    stamps = []
    for path in paths:
        try:
            stat = os.stat(path)
            stamps.append((path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append((path, None, None))
    signature = tuple(stamps)
    if _MONITOR_CONFIG_CACHE is None or _MONITOR_CONFIG_CACHE[0] != signature:
        _MONITOR_CONFIG_CACHE = (signature, _load_monitor_config())
    return _MONITOR_CONFIG_CACHE[1]

def _load_monitor_config():
    return load_merged(layers=_monitor_layer_paths())

def _monitor_layer_paths():
    """Use the shared resolver for vendor, host, user and fragment precedence."""
    paths = layer_paths()
    if IS_WINDOWS:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        paths = [os.path.join(root, path.lstrip("/"))
                 if path.startswith(("/usr/", "/etc/")) else path for path in paths]
        expanded = []
        for index, path in enumerate(paths):
            expanded.append(path)
            if index == 0 or os.path.basename(path) == "mios.toml":
                fragments = (os.path.join(root, "usr", "lib", "mios", "mios.d")
                             if index == 0 else os.path.join(os.path.dirname(path), "mios.d"))
                expanded.extend(sorted(glob.glob(os.path.join(fragments, "*.toml")), key=os.path.basename))
        paths = list(dict.fromkeys(expanded))
    return paths

def monitor_sources_config():
    """Read collector cadence, history and Linux identities from the SSOT."""
    data = monitor_config()
    pipeline = data.get("logging", {}).get("pipeline", {})
    terminal = data.get("terminal", {})
    vm = data.get("bootstrap", {}).get("dev_vm", {})
    return {"batch_size": max(1, int(pipeline.get("batch_size", 50))),
            "flush_interval_s": max(1, int(pipeline.get("flush_interval_s", 5))),
            "scrollback_rows": max(100, int(terminal.get("scrollback_rows", 9000))),
            "distros": (f"podman-{vm.get('machine_name', 'MiOS-DEV')}", vm.get("wsl_distro", "MiOS"))}

def parse_windows_events(output):
    """wevtutil emits adjacent Event XML records, without a wrapper element."""
    ns = "{http://schemas.microsoft.com/win/2004/08/events/event}"
    for match in re.finditer(r"<Event\s.*?</Event>", output, re.S):
        try:
            root = ET.fromstring(match.group())
            system = root.find(ns + "System")
            if system is None:
                continue
            record = int(system.findtext(ns + "EventRecordID") or 0)
            level = int(system.findtext(ns + "Level") or 4)
            event_id = system.findtext(ns + "EventID") or "?"
            provider = system.find(ns + "Provider")
            source = provider.get("Name", "Windows") if provider is not None else "Windows"
            created = system.find(ns + "TimeCreated")
            timestamp = created.get("SystemTime", "") if created is not None else ""
            data = [" ".join((item.text or "").split()) for item in root.iter(ns + "Data")]
            detail = "; ".join(filter(None, data))[:240]
            yield record, level, f"{timestamp} {source} #{event_id}" + (f" {detail}" if detail else "")
        except (ET.ParseError, ValueError):
            continue

def running_wsl_distros():
    try:
        proc = subprocess.run(["wsl.exe", "--list", "--running", "--quiet"],
                              capture_output=True, timeout=10)
        if proc.returncode != 0:
            return set()
        raw = proc.stdout
        decoded = raw.decode("utf-16-le", errors="replace") if b"\x00" in raw else raw.decode("utf-8", errors="replace")
        return {line.strip().strip("\ufeff") for line in decoded.splitlines() if line.strip()}
    except (OSError, subprocess.TimeoutExpired):
        return set()

def listening_tcp_ports(paths=("/proc/net/tcp", "/proc/net/tcp6")):
    """Local TCP ports in LISTEN state (st 0A) from procfs; None where procfs is unavailable.

    A connect probe opens a real session on every service at each refresh: xrdp forked a child and
    logged a failed X.224 handshake for every probe (37,841 in one day). Reading the socket table
    observes the same "is it listening" fact without touching the service."""
    found, ports = False, set()
    for path in paths:
        try:
            with open(path, encoding="ascii") as fh:
                next(fh, None)
                for line in fh:
                    fields = line.split()
                    if len(fields) > 3 and fields[3] == "0A":
                        ports.add(int(fields[1].rsplit(":", 1)[1], 16))
            found = True
        except (OSError, ValueError, IndexError):
            continue
    return ports if found else None

def check_port(host, port, listening=None):
    if type(port) is not int or not 0 < port < 65536:
        return False
    if listening is not None and host in ("127.0.0.1", "localhost", "::1"):
        return port in listening
    try:
        with socket.create_connection((host, int(port)), timeout=0.03):
            return True
    except Exception:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.03):
                return True
        except Exception:
            return False

def engine_online():
    """A successful Podman host response proves the selected engine is reachable."""
    try:
        probe = subprocess.run(["podman", "info", "--format", "json"],
                               capture_output=True, text=True, timeout=2)
        info = json.loads(probe.stdout) if probe.returncode == 0 else {}
        return isinstance(info, dict) and isinstance(info.get("host"), dict) and bool(info["host"])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False

def get_services():
    ports = monitor_config().get("ports", {})
    offset = int(ports.get("stack_id", 0)) * 10000
    svcs = []
    listening = listening_tcp_ports()
    for name, port in ports.items():
        if type(port) is int and name != "stack_id":
            actual_port = process_val(f"ports.{name}", port, offset)
            svcs.append((name, actual_port, check_port("127.0.0.1", actual_port, listening)))
    wsl_online = bool(running_wsl_distros()) if IS_WINDOWS else "microsoft" in platform.release().lower()
    svcs.append(("wsl-engine", 0, wsl_online))
    svcs.append(("podman-machine", 0, engine_online()))
    return svcs

def get_sys_info():
    global _SYS_INFO_CACHE
    if _SYS_INFO_CACHE is not None:
        uptime_str = "0h 0m"
        if not IS_WINDOWS:
            try:
                with open("/proc/uptime") as f:
                    u_sec = float(f.read().split()[0])
                    uptime_str = f"{int(u_sec // 3600)}h {int((u_sec % 3600) // 60)}m"
            except: pass
        else:
            try:
                u_sec = time.time() - psutil.boot_time()
                uptime_str = f"{int(u_sec // 3600)}h {int((u_sec % 3600) // 60)}m"
            except: pass
        res = dict(_SYS_INFO_CACHE)
        res["uptime"] = uptime_str
        return res

    host = platform.node() or 'localhost'
    kernel = platform.release()
    user = os.environ.get("USER", os.environ.get("USERNAME", "mios"))
    os_name = "Linux"
    uptime_str = "0h 0m"
    cpu_model = "Unknown CPU"

    if not IS_WINDOWS:
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        os_name = line.split("=")[1].strip().strip('"')
        except: pass
        try:
            with open("/proc/uptime") as f:
                u_sec = float(f.read().split()[0])
                uptime_str = f"{int(u_sec // 3600)}h {int((u_sec % 3600) // 60)}m"
        except: pass
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        cpu_model = line.split(":")[1].strip()
                        break
        except: pass
    else:
        os_name = "Windows"
        try:
            cpu_model = os.environ.get("PROCESSOR_IDENTIFIER", "x86/x64 Processor")
            out = subprocess.check_output(["wmic", "cpu", "get", "name"], text=True, stderr=subprocess.DEVNULL, timeout=1.5)
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            if len(lines) > 1: cpu_model = lines[1]
        except: pass

    _SYS_INFO_CACHE = {"os": os_name, "host": host, "kernel": kernel, "user": user, "uptime": uptime_str, "cpu_model": cpu_model}
    return _SYS_INFO_CACHE

def get_telemetry():
    if not TEXTUAL_AVAILABLE: return 0, 0, 0, 0, "0.00"
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    c_pct = m_pct = 0
    try:
        c_pct = psutil.disk_usage('C:\\' if IS_WINDOWS else '/').percent
        m_path = 'M:\\' if IS_WINDOWS else '/mnt/m'
        if os.path.exists(m_path):
            m_pct = psutil.disk_usage(m_path).percent
    except: pass
    load_avg = "-"
    if not IS_WINDOWS and hasattr(os, "getloadavg"):
        try: load_avg = f"{os.getloadavg()[0]:.2f}"
        except: pass
    return cpu, ram, c_pct, m_pct, load_avg

def _bg_update_usb():
    global _USB_INFO_CACHE
    while True:
        try:
            if IS_WINDOWS:
                cmd = ["powershell.exe", "-NoProfile", "-Command", "Get-Disk | Where-Object BusType -eq 'USB' | Select-Object -First 1 -Property Number, FriendlyName, Size | ConvertTo-Json"]
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=3.0)
                if out.strip():
                    data = json.loads(out)
                    if isinstance(data, dict):
                        size_gb = int(data.get('Size', 0) / (1024**3))
                        name = data.get('FriendlyName', 'USB Drive')
                        _USB_INFO_CACHE = f"D: {name} ({size_gb}GB)"
                    else:
                        _USB_INFO_CACHE = "D: USB Drive Detected"
                else:
                    _USB_INFO_CACHE = "No USB Drive Detected"
            else:
                _USB_INFO_CACHE = "No USB Drive Detected"
        except Exception:
            _USB_INFO_CACHE = "No USB Drive Detected"
        time.sleep(10)

def _resolve_git_dir():
    for c in [os.environ.get("MIOS_ROOT"), os.getcwd(), "/workspaces/MiOS", "/", "/mnt/m", "C:\\MiOS", "C:\\mios-bootstrap"]:
        if c and os.path.isdir(os.path.join(c, ".git")): return c
    return None

def _resolve_git_status():
    global _GIT_STATUS_CACHE
    d = _resolve_git_dir()
    if not d:
        _GIT_STATUS_CACHE = "[dim]Git repo not found[/]"
        return _GIT_STATUS_CACHE
    try:
        out = subprocess.check_output(["git", "--no-optional-locks", "status", "--porcelain", "-b"], cwd=d, text=True, timeout=2.0, stderr=subprocess.DEVNULL)
        lines = out.splitlines()
        branch = lines[0].replace("##", "").strip() if lines and "##" in lines[0] else (lines[0].strip() if lines else "unknown")
        staged = sum(1 for l in lines[1:] if l and l[0] not in (" ", "?"))
        mod = sum(1 for l in lines[1:] if l and l[:2] != "??" and l[1] != " ")
        untr = sum(1 for l in lines[1:] if l and l[:2] == "??")
        _GIT_STATUS_CACHE = f"Branch: {branch} | [green]{staged} staged[/] | [yellow]{mod} mod[/] | [dim]{untr} untracked[/]"
    except Exception:
        _GIT_STATUS_CACHE = "[dim]Git state unavailable[/]"
    return _GIT_STATUS_CACHE

def _bg_update_git():
    while True:
        _resolve_git_status()
        time.sleep(5)

threading.Thread(target=_bg_update_usb, daemon=True).start()
threading.Thread(target=_bg_update_git, daemon=True).start()

def get_usb_drive_info():
    return _USB_INFO_CACHE

def get_git_tree_status():
    return _GIT_STATUS_CACHE

def get_credentials_text():
    u = "mios"
    lp = "mios"
    fp = "mios"
    lp_file = "/etc/mios/login-password"
    fp_file = "/var/lib/mios/forge/admin-password"
    if os.path.isfile(lp_file):
        try:
            with open(lp_file, "r") as f: lp = f.read().strip() or lp
        except Exception: pass
    if os.path.isfile(fp_file):
        try:
            with open(fp_file, "r") as f: fp = f.read().strip() or lp
        except Exception: pass
    return f"[dim]login[/] [cyan]{u}[/]/[yellow]{lp}[/]    [dim]forge[/] [cyan]{u}[/]/[yellow]{fp}[/]"

def get_ascii_logo():
    p = "C:\\MiOS\\usr\\share\\mios\\branding\\mios.txt" if IS_WINDOWS else "/usr/share/mios/branding/mios.txt"
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if not l.strip().startswith("#")]
                while lines and not lines[0].strip(): lines.pop(0)
                while lines and not lines[-1].strip(): lines.pop()
                return "\n".join(lines)
        except Exception: pass
    return r"""\
  __  __ _  ___  ____
 |  \/  (_)/ _ \/ ___|
 | |\/| | | | | \___ \
 | |  | | | |_| |___) |
 |_|  |_|_|\___/|____/
"""

def run_fastfetch():
    try:
        cfg = "C:\\MiOS\\usr\\share\\mios\\fastfetch\\config.jsonc" if IS_WINDOWS else "/usr/share/mios/fastfetch/config.jsonc"
        cmd = ["fastfetch", "-c", cfg, "--logo", "none"] if os.path.exists(cfg) else ["fastfetch", "--logo", "none"]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=2.0)
        sh = os.path.basename(os.environ.get("SHELL", "bash"))
        clean, skip = [], False
        for line in out.splitlines():
            if skip:
                if any(line.strip().startswith(p) for p in ("CPU", "GPU", "Memory", "Swap", "Disk", "Local IP", "Locale", "Battery", "Power")):
                    skip = False; clean.append(line)
                continue
            if "Shell" in line and not any(line.strip().startswith(p) for p in ("CPU", "GPU", "OS", "Kernel", "Memory")):
                clean.append(f"\033[33mShell\033[0m  \033[36m{sh}\033[0m")
                skip = True; continue
            clean.append(line)
        return Text.from_ansi("\n".join(clean))
    except Exception: return None

def get_sys_info_table():
    sys_info, telem = get_sys_info(), get_telemetry()
    t = Table(box=box.ROUNDED, border_style="dim cyan", show_header=False, expand=True, padding=(0, 1))
    for col, rat in [("yellow bold", 1), ("white", 3), ("yellow bold", 1), ("white", 3)]:
        t.add_column(style=col, ratio=rat)
    sh = os.path.basename(os.environ.get("SHELL", "bash"))
    ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ip = s.getsockname()[0]; s.close()
    except Exception: pass
    if isinstance(telem, dict):
        mem_str = f"{telem.get('ram', 0)} GiB ({telem.get('m_pct', 0)}%)"
        load_str = str(telem.get("load_avg", "-"))
    else:
        mem_str = f"{telem[1]}%"
        load_str = str(telem[4])
    t.add_row("OS", sys_info.get("os", "Linux"), "CPU", f"{sys_info.get('cpu_model', 'CPU')}")
    t.add_row("Kernel", sys_info.get("kernel", "Linux"), "Memory", mem_str)
    t.add_row("Uptime", sys_info.get("uptime", "0m"), "Load", load_str)
    t.add_row("Shell", sh, "Host", f"{sys_info.get('host', 'localhost')} ({ip})")
    return t

def get_agent_and_mcp_data():
    """Bound native observation startup cost independently of UI frame rate."""
    global _AGENT_SNAPSHOT_CACHE
    interval = float(monitor_config().get("terminal", {}).get("monitor", {}).get("agent_poll_s", 5))
    if interval <= 0:
        raise ValueError("terminal.monitor.agent_poll_s must be positive")
    now = time.monotonic()
    if _AGENT_SNAPSHOT_CACHE is None or now - _AGENT_SNAPSHOT_CACHE[0] >= interval:
        _AGENT_SNAPSHOT_CACHE = (now, _collect_agent_and_mcp_data())
    return _AGENT_SNAPSHOT_CACHE[1]

def _collect_agent_and_mcp_data():
    """Use the native sanitized observer; never read another user's private state."""
    try:
        cmd = ["/usr/bin/mios", "agents", "--observe"]
        if IS_WINDOWS:
            cmd = ["wsl.exe", "-d", "podman-MiOS-DEV", "-u", "mios", "--", "/usr/bin/mios", "agents", "--observe"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if result.returncode:
            return [], [], []
        snapshot = json.loads(result.stdout)
        if snapshot.get("errors"):
            return [], [], []
        agents = [{**a, "id": a["agent_id"]} for a in snapshot.get("agents", [])]
        panes = []
        for p in snapshot.get("panes", []):
            if p.get("session") == "mios-anchor":
                continue
            command = p.get("agent_kind") or p.get("command", "")
            panes.append({"socket": p.get("socket", ""), "pane": f"{p.get('session', '')}:{p.get('window', '')}:{p.get('pane', '')}",
                          "identity": str(p.get("socket", "")) + ":" + str(p.get("pane", "")), "pid": p.get("pid", 0), "cmd": command,
                          "active": not p.get("dead", False) and command not in ("bash", "sh", ""),
                          "raw_cmd": p.get("command", "")})
        return agents, snapshot.get("messages", []), panes
    except Exception:
        return [], [], []


def create_metal_layout():
    sys_info = get_sys_info()
    services = get_services()
    try:
        term_cols, term_lines = shutil.get_terminal_size((80, 24))
    except Exception:
        term_cols, term_lines = 80, 24

    is_portrait = term_cols < 60 or term_lines > term_cols

    t = Table(show_header=False, box=box.SIMPLE, expand=True)
    if is_portrait:
        for s in services:
            c = "green" if s[2] else "red"
            m = f"[{c}]{'*' if s[2] else 'x'}[/] {s[0]}"
            t.add_row(m)
    else:
        for i in range(0, len(services), 2):
            s1 = services[i]
            c1 = "green" if s1[2] else "red"
            m1 = f"[{c1}]{'*' if s1[2] else 'x'}[/] {s1[0]}"
            m2 = ""
            if i + 1 < len(services):
                s2 = services[i+1]
                c2 = "green" if s2[2] else "red"
                m2 = f"[{c2}]{'*' if s2[2] else 'x'}[/] {s2[0]}"
            t.add_row(m1, m2)
    up = sum(1 for s in services if s[2])
    return Align.center(Panel(t, title=f"[cyan bold]MiOS Mini[/] - [dim]{sys_info['host']} ({sys_info['os']})[/]", subtitle=f"[green]{up} UP[/] | [red]{len(services) - up} DOWN[/]", border_style="cyan"))

create_mini_layout = create_metal_layout

def create_dash_layout():
    services = get_services()
    logo = Align.center(Text(get_ascii_logo(), style="cyan bold", no_wrap=True))
    fetch = run_fastfetch()
    try:
        term_cols, term_lines = shutil.get_terminal_size((80, 24))
    except Exception:
        term_cols, term_lines = 80, 24

    is_portrait = term_cols < 75 or term_lines > term_cols

    svcs = Table(box=box.SIMPLE, expand=True)
    if is_portrait:
        svcs.add_column("Service", style="cyan")
        svcs.add_column("Port", style="dim", justify="right")
        svcs.add_column("Status", justify="center")
        for s in services:
            st = "[green bold]*[/]" if s[2] else "[red bold]x[/]"
            svcs.add_row(s[0], str(s[1]) if s[1] else "-", st)
    else:
        for _ in range(2):
            svcs.add_column("Service", style="cyan"); svcs.add_column("Port", style="dim", justify="right"); svcs.add_column("Status", justify="center")
        for i in range(0, len(services), 2):
            s1 = services[i]
            st1 = "[green bold]*[/]" if s1[2] else "[red bold]x[/]"
            s2_row = ["", "", ""]
            if i + 1 < len(services):
                s2 = services[i+1]
                s2_row = [s2[0], str(s2[1]) if s2[1] else "-", "[green bold]*[/]" if s2[2] else "[red bold]x[/]"]
            svcs.add_row(s1[0], str(s1[1]) if s1[1] else "-", st1, *s2_row)

    footer = Align.center(f"{get_credentials_text()}\n\n[bold]Tree:[/] {get_git_tree_status()}")
    header_box = Panel(Group(logo, Text(""), Align.center(fetch) if fetch else get_sys_info_table()), box=box.SIMPLE, border_style="cyan")
    
    agent_panel = None
    try:
        agents, messages, slots = get_agent_and_mcp_data()
        online_count = sum(1 for a in agents if a.get("online"))
        ag_table = Table(box=box.SIMPLE, expand=True)
        ag_table.add_column("Agent / Session", style="cyan")
        ag_table.add_column("Kind", style="dim")
        ag_table.add_column("Status", justify="center")
        ag_table.add_column("Pending", justify="right")
        for a in agents[:6]:
            st = "[green bold]ONLINE[/]" if a.get("online") else "[red]OFFLINE[/]"
            aid = a.get("id", "agent")
            aid_disp = aid if len(aid) <= 30 else (aid[:15] + ".." + aid[-12:])
            p_str = f"[yellow]{a['pending']}[/]" if a.get("pending") else "[dim]0[/]"
            ag_table.add_row(aid_disp, str(a.get("kind", "-")), st, p_str)

        pane_table = Table(box=box.SIMPLE, expand=True)
        pane_table.add_column("Slot / Pane", style="cyan")
        pane_table.add_column("PID", style="dim", justify="right")
        pane_table.add_column("Command", style="yellow")
        for sl in slots[:4]:
            pane_table.add_row(str(sl["pane"]), str(sl["pid"]), str(sl["cmd"]))

        agent_items = [
            Text(f"Relay Participants: {len(agents)} registered ({online_count} online)", style="bold cyan"),
            ag_table,
            Text(f"Tmux Automation Slots: {len(slots)} detected", style="bold yellow"),
            pane_table if slots else Text("  No active tmux panes detected\n", style="dim"),
        ]
        if messages:
            agent_items.append(Text(f"Message Receipts: {len(messages)} total", style="bold magenta"))
            msg_table = Table(box=box.SIMPLE, expand=True)
            msg_table.add_column("Status", justify="center")
            msg_table.add_column("Message ID", style="cyan")
            msg_table.add_column("Route", style="dim")
            for m in messages[-4:]:
                st = m.get("status", "msg")
                st_str = "[green]received[/]" if st == "received" else "[yellow]queued[/]" if st == "queued" else f"[dim]{st}[/]"
                mid = m.get("message_id", "")
                mid_disp = mid if len(mid) <= 24 else (mid[:11] + ".." + mid[-10:])
                frm = m.get("from", "?")
                if len(frm) > 14: frm = frm[:6] + ".." + frm[-6:]
                to = m.get("to", "?")
                if len(to) > 14: to = to[:6] + ".." + to[-6:]
                msg_table.add_row(st_str, mid_disp, f"{frm} ➔ {to}")
            agent_items.append(msg_table)

        agent_panel = Panel(Group(*agent_items), title="[cyan]AGENT RELAY & AUTOMATION (MCP)[/]", border_style="cyan")
    except Exception:
        agent_panel = None

    dash_panels = [header_box, Panel(svcs, title="[yellow]UNIFIED SYSTEM STACK & SERVICES[/]", border_style="cyan")]
    if agent_panel:
        dash_panels.append(agent_panel)
    dash_panels.append(Panel(footer, box=box.SIMPLE, border_style="cyan"))

    return Panel(Group(*dash_panels), border_style="blue", title="[bold cyan]MiOS Dashboard[/]", padding=(1, 1))

if TEXTUAL_AVAILABLE:
    def load_ssot_colors():
        data = monitor_config()
        colors = mios_colors(data=data)
        colors["surface"] = data.get("colors", {}).get("surface", colors["bg"])
        theme = data.get("theme", {})
        transparent_terminal = bool(theme.get("acrylic", False)) and int(theme.get("opacity", 100)) < 100
        return colors, transparent_terminal

    SSOT, TRANSPARENT_TERMINAL = load_ssot_colors()
    SCREEN_BACKGROUND = SSOT['bg']
    PANEL_BACKGROUND = SSOT['surface']

    def make_bar(pct, width=15):
        pct = max(0.0, min(100.0, float(pct)))
        filled = int((pct / 100.0) * width)
        empty = width - filled
        if pct > 80: color = SSOT['error']
        elif pct > 60: color = SSOT['warning']
        else: color = SSOT['success']
        return f"[{color}]{'█' * filled}[/][dim]{'░' * empty}[/]"

    class MiosMonitorApp(App):
        TITLE = "MiOS Unified System & AI Monitor"
        refresh_interval = reactive(0.5)

        def __init__(self, ui_request=None, ui_mode=None, observer=None, collectors=True, **kwargs):
            super().__init__(**kwargs)
            self.ui_request = ui_request or {}
            self.ui_mode = ui_mode
            self.collectors_enabled = collectors
            self.collectors_started = False
            self.observer = observer or self.observe_agents

        def observe_agents(self):
            request = self.ui_request.get("observation_request")
            if not request:
                raise RuntimeError("Relay configuration unavailable")
            try:
                cmd = [request["config"]["binary"], "--state", self.ui_request["state"], "--observe"]
                if IS_WINDOWS:
                    cmd = ["wsl.exe", "-d", "podman-MiOS-DEV", "-u", "mios", "--", request["config"]["binary"], "--state", self.ui_request["state"], "--observe"]
                result = subprocess.run(cmd, input=json.dumps(request), capture_output=True, text=True, timeout=10)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip() or f"Observer exit {result.returncode}")
                receipt = json.loads(result.stdout)
                if not isinstance(receipt, dict):
                    raise RuntimeError("Invalid native observation receipt")
                if receipt.get("ok") is not True or not isinstance(receipt.get("result"), dict):
                    raise RuntimeError(receipt.get("error") or "Invalid native observation receipt")
                snapshot = receipt["result"]
                if any(not isinstance(snapshot.get(key), list)
                       for key in ("agents", "panes", "messages", "errors")):
                    raise RuntimeError("Invalid native observation snapshot")
                return snapshot
            except Exception as e:
                # AgentView displays refresh failures without clearing its last
                # successful snapshot, rows, cursor or message receipt counts.
                raise RuntimeError(f"Native relay observation failed: {e}") from e

        DEFAULT_CSS = f"""
        Screen {{
            height: 100%;
            width: 100%;
            background: {SCREEN_BACKGROUND};
            color: {SSOT['fg']};
            overflow: hidden;
        }}
        App, TabbedContent, ContentSwitcher, TabPane,
        #main-container, #build-container, #flash-container, #ai-container,
        #left-pane, #right-pane, #top-right-bar {{
            background: {SCREEN_BACKGROUND};
        }}
        TabbedContent {{
            height: 1fr;
            width: 100%;
        }}
        ContentSwitcher {{
            height: 1fr;
            width: 100%;
        }}
        TabPane {{
            height: 1fr;
            width: 100%;
            padding: 0;
        }}
        #main-container, #build-container, #flash-container, #ai-container {{
            height: 1fr;
            width: 100%;
        }}
        .box {{
            background: {PANEL_BACKGROUND};
            border: round {SSOT['accent']};
            padding: 0 1;
        }}
        #build-stats-pane, #flash-stats-pane, #ai-stats-pane {{
            width: 32;
            height: 100%;
            border: round {SSOT['accent']};
            background: {PANEL_BACKGROUND};
            padding: 0 1;
            overflow-x: hidden;
            overflow-y: hidden;
        }}
        #build-log-box, #flash-log-box, #ai-log-box {{
            width: 1fr;
            height: 100%;
            border: round {SSOT['success']};
            background: {PANEL_BACKGROUND};
        }}
        #left-pane {{
            width: 38;
            height: 100%;
        }}
        #hw-box {{
            height: auto;
            max-height: 16;
            margin-bottom: 1;
        }}
        .compact #hw-box {{
            height: 6;
            max-height: 6;
        }}
        #svc-table {{
            height: 1fr;
            border: round {SSOT['accent']};
        }}
        #right-pane {{
            width: 1fr;
            height: 100%;
            margin-left: 1;
        }}
        #top-right-bar {{
            height: 4;
            margin-bottom: 1;
        }}
        #sys-identity {{
            width: 1fr;
            height: 100%;
            border: round {SSOT['subtle']};
            margin-right: 1;
        }}
        #forge-box {{
            width: 1fr;
            height: 100%;
            border: round {SSOT['warning']};
            content-align: center middle;
        }}
        #spark-container {{
            height: 3;
            border: round {SSOT['accent']};
            background: {PANEL_BACKGROUND};
            padding: 0 1;
            margin-bottom: 1;
        }}
        #spark-widget {{
            height: 100%;
            width: 100%;
            color: {SSOT['subtle']};
        }}
        #log-box {{
            height: 1fr;
            width: 100%;
            border: round {SSOT['success']};
        }}
        ScrollBar {{
            width: 1;
            min-width: 1;
            max-width: 1;
            background: transparent;
        }}
        ScrollBar.-horizontal {{
            display: none;
            height: 0;
            min-height: 0;
            max-height: 0;
            background: transparent;
        }}
        ScrollBar.-vertical {{
            width: 1;
            min-width: 1;
            max-width: 1;
            background: transparent;
        }}
        ScrollBarCorner {{
            display: none;
            background: transparent;
        }}
        ScrollBarThumb {{
            background: {SSOT['accent']};
            color: {SSOT['accent']};
        }}
        ScrollBarThumb:hover {{
            background: {SSOT['subtle']};
        }}
        RichLog {{
            scrollbar-size: 1 0;
            scrollbar-size-vertical: 1;
            scrollbar-size-horizontal: 0;
            overflow-x: hidden;
            background: {PANEL_BACKGROUND};
        }}
        DataTable {{
            scrollbar-size: 0 0;
            scrollbar-size-vertical: 0;
            scrollbar-size-horizontal: 0;
            overflow-x: hidden;
            overflow-y: hidden;
        }}
        Footer {{
            dock: bottom;
            height: 1;
        }}
        #monitor-title {{ height: 1; padding: 0 1; text-style: bold; color: {SSOT['fg']}; background: {SSOT['accent']}; }}
        #monitor-help {{ dock: bottom; height: 1; padding: 0 1; color: {SSOT['fg']}; background: transparent; }}
        ClientView, AgentView, SystemSummary, DataTable {{ background: {SCREEN_BACKGROUND}; }}
        DataTable > .datatable--header {{ background: {SSOT['accent']}; color: {SSOT['fg']}; text-style: bold; }}
        DataTable > .datatable--odd-row {{ background: transparent; }}
        DataTable > .datatable--even-row {{ background: transparent; }}
        DataTable > .datatable--cursor {{ background: {SSOT['accent']}; color: {SSOT['fg']}; }}
        .compact Header, .compact Footer, .compact Tabs, .compact ContentTabs, .compact #monitor-title, .compact #monitor-help {{ display: none; }}
        #system-summary {{ display: none; }}
        .compact #ai-log-box {{ display: none; }}
        #ai-container > AgentView {{ width: 1fr; height: 100%; }}
        #ai-container > #ai-log-box {{ width: 1fr; height: 100%; border: round {SSOT['success']}; background: {PANEL_BACKGROUND}; }}
        .compact #ai-container > AgentView {{ width: 100%; height: 100%; }}
        """

        BINDINGS = [
            ("q", "quit", "Quit"),
            ("d", "toggle_dark", "Toggle Dark Mode"),
            ("1", "tab_global", "1:Systems"),
            ("2", "tab_build", "2:Build"),
            ("3", "tab_flash", "3:Flash"),
            ("4", "tab_agents", "4:Agents & AI"),
            Binding("escape", "quit", "Quit", priority=True),
            Binding("f1", "tab_clients", "Clients", priority=True),
            Binding("f2", "tab_agents", "Agents & AI", priority=True),
            Binding("f3", "tab_global", "System", priority=True),
            Binding("f4", "tab_build", "Build", priority=True),
            Binding("f5", "tab_flash", "Flash", priority=True),
            Binding("ctrl+right", "next_view", "Next view", priority=True),
            Binding("ctrl+left", "previous_view", "Previous view", priority=True),
            ("minus", "speed_up", "Faster (-)"),
            ("underscore", "speed_up", "Faster (-)"),
            ("kp_minus", "speed_up", "Faster (-)"),
            ("plus", "slow_down", "Slower (+)"),
            ("equals", "slow_down", "Slower (+)"),
            ("kp_plus", "slow_down", "Slower (+)"),
        ]

        def _activate_tab(self, tab_id: str):
            if tab_id == "tab-ai":
                tab_id = "tab-agents"
            self.set_focus(None)
            self.query_one(TabbedContent).active = tab_id
            if tab_id == "tab-clients":
                try: self.query_one("#client-input").focus()
                except Exception: pass
            elif tab_id == "tab-agents":
                try: self.query_one("#peer-table").focus()
                except Exception: pass
            elif tab_id == "tab-global":
                try: self.query_one("#svc-table").focus()
                except Exception: pass

        def action_tab_global(self): self._activate_tab("tab-global")
        def action_tab_build(self): self._activate_tab("tab-build")
        def action_tab_flash(self): self._activate_tab("tab-flash")
        def action_tab_ai(self): self._activate_tab("tab-agents")
        def action_tab_clients(self): self._activate_tab("tab-clients")
        def action_tab_agents(self): self._activate_tab("tab-agents")

        def cycle_view(self, step):
            views = ["tab-clients", "tab-agents", "tab-global", "tab-build", "tab-flash"]
            tabs = self.query_one(TabbedContent)
            next_tab = views[(views.index(tabs.active) + step) % len(views)]
            self._activate_tab(next_tab)

        def action_next_view(self): self.cycle_view(1)
        def action_previous_view(self): self.cycle_view(-1)

        @on(ClientView.Selected)
        def launch_client(self, event):
            self.exit(event.name)

        @on(TabbedContent.TabActivated)
        def view_changed(self, event):
            view = event.tab.id.removeprefix("--content-tab-")
            title = {"tab-clients": "Clients", "tab-agents": "Agents & AI", "tab-global": "System",
                     "tab-build": "Build", "tab-flash": "Flash"}.get(view, "Monitor")
            self.query_one("#monitor-title", Static).update(f"MiOS Monitor · {title}")
            if self.ui_mode and view in {"tab-global", "tab-build", "tab-flash", "tab-agents"}:
                if view == "tab-global" and (self.size.width < 78 or self.size.height < 26):
                    return
                self.start_collectors()

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static("MiOS Monitor", id="monitor-title", markup=False)
            mode = "agents" if self.ui_mode == "ai" else self.ui_mode
            tab_choice = "agents" if TAB_CHOICE == "ai" else TAB_CHOICE
            init_tab = f"tab-{mode}" if mode else ("tab-agents" if AI_MODE else ("tab-build" if PIPELINE_MODE else (f"tab-{tab_choice}" if tab_choice else "tab-global")))
            with TabbedContent(initial=init_tab):
                with TabPane("Clients", id="tab-clients"):
                    yield ClientView(self.ui_request.get("agents", []))
                with TabPane("Agents & AI", id="tab-agents"):
                    with Horizontal(id="ai-container"):
                        yield AgentView(self.observer, self.ui_request.get("observation_request", {}).get("observation", {}).get("refresh_s", 2), id="agent-view")
                        yield RichLog(id="ai-log-box", classes="box", markup=True, wrap=True)
                with TabPane("System", id="tab-global"):
                    yield SystemSummary(get_telemetry, get_services, id="system-summary")
                    with Horizontal(id="main-container"):
                        with Vertical(id="left-pane"):
                            yield Static(id="hw-box", classes="box")
                            yield DataTable(id="svc-table", classes="box")
                        with Vertical(id="right-pane"):
                            with Horizontal(id="top-right-bar"):
                                yield Static(id="sys-identity", classes="box")
                                yield Static(id="forge-box", classes="box")
                            with Vertical(id="spark-container"):
                                yield Sparkline(data=[], id="spark-widget")
                            yield RichLog(id="log-box", classes="box", markup=True, wrap=True,
                                          max_lines=monitor_sources_config()["scrollback_rows"])
                with TabPane("Build", id="tab-build"):
                    with Horizontal(id="build-container"):
                        with Vertical(id="build-stats-pane", classes="box"):
                            yield Static(id="build-stats", markup=True)
                        yield RichLog(id="build-log-box", classes="box", markup=True, wrap=True)
                with TabPane("Flash", id="tab-flash"):
                    with Horizontal(id="flash-container"):
                        with Vertical(id="flash-stats-pane", classes="box"):
                            yield Static(id="flash-stats", markup=True)
                        yield RichLog(id="flash-log-box", classes="box", markup=True, wrap=True)
            yield Footer()
            yield Static("F1–F5 views · Ctrl+←/→ · Esc quit", id="monitor-help", markup=False)

        def on_mount(self) -> None:
            self.dark = True
            self.console.push_theme(_rich_theme)
            custom_theme = Theme(
                name="mios-ssot",
                primary=SSOT['subtle'],
                secondary=SSOT['accent'],
                warning=SSOT['warning'],
                error=SSOT['error'],
                success=SSOT['success'],
                accent=SSOT['accent'],
                background=SSOT['bg'],
                surface=SSOT['surface'],
                panel=SSOT['surface'],
            )
            self.register_theme(custom_theme)
            self.theme = "mios-ssot"

            table = self.query_one("#svc-table", DataTable)
            table.add_columns("Service", "Port", "Status")
            table.zebra_stripes = True

            self.apply_responsive_layout(self.size.width, self.size.height)
            mode = "agents" if self.ui_mode == "ai" else self.ui_mode
            initial = f"tab-{mode}" if mode else self.query_one(TabbedContent).active
            self.call_after_refresh(self._activate_tab, initial)
            if not self.ui_mode:
                self.start_collectors()

        def start_collectors(self):
            if self.collectors_started or not self.collectors_enabled:
                return
            self.collectors_started = True
            self.cpu_history = []
            self.tailing = True
            self.journal_procs = []
            self.log_thread = threading.Thread(target=self.tail_all_logs, daemon=True)
            self.log_thread.start()
            self.build_log_path = None
            self.build_log_offset = 0
            self.build_log_identity = None
            self.build_log_phase = "-"
            self.set_interval(1.0, self.refresh_build_log)
            self.refresh_build_log()

            self.telemetry_timer = self.set_interval(self.refresh_interval, self.update_telemetry)
            self.set_interval(3.0, self.async_update_services)
            self.async_update_services()
            self.update_titles()
            try:
                self.apply_responsive_layout(self.size.width, self.size.height)
            except Exception: pass

        def update_titles(self):
            ms = int(self.refresh_interval * 1000)
            self.query_one("#hw-box").border_title = f"Telemetry ({ms}ms)"
            self.query_one("#sys-identity").border_title = "Identity"
            self.query_one("#forge-box").border_title = "Forge / Git"
            self.query_one("#spark-container").border_title = f"CPU History ({ms}ms)"
            self.query_one("#log-box").border_title = "Live System Stream"
            self.query_one("#svc-table", DataTable).border_title = "Core Services"
            try:
                self.query_one("#build-log-box").border_title = "MiOS Build (Live)"
                self.query_one("#flash-log-box").border_title = "USB Flash (Live)"
                self.query_one("#ai-log-box").border_title = "Agent Stream (Live)"
            except Exception: pass

        def action_speed_up(self):
            self.refresh_interval = max(0.1, self.refresh_interval - 0.1)
            if hasattr(self, "telemetry_timer"):
                self.telemetry_timer.stop()
            self.telemetry_timer = self.set_interval(self.refresh_interval, self.update_telemetry)
            self.update_titles()

        def action_slow_down(self):
            self.refresh_interval = min(5.0, self.refresh_interval + 0.1)
            if hasattr(self, "telemetry_timer"):
                self.telemetry_timer.stop()
            self.telemetry_timer = self.set_interval(self.refresh_interval, self.update_telemetry)
            self.update_titles()

        def tail_all_logs(self):
            log_box = self.query_one("#log-box", RichLog)
            settings = monitor_sources_config()
            try:
                flash_log_box = self.query_one("#flash-log-box", RichLog)
                ai_log_box = self.query_one("#ai-log-box", RichLog)
            except Exception:
                flash_log_box = None
                ai_log_box = None
            def stream_proc(cmd, label):
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                            text=True, encoding="utf-8", bufsize=1, errors="replace")
                    self.journal_procs.append(proc)
                    while self.tailing:
                        if proc.poll() is not None:
                            break
                        line = proc.stdout.readline()
                        if not line:
                            time.sleep(0.05)
                            continue
                        line = line.strip()
                        if not line: continue
                        is_err = bool(re.search(r'\b(error|failed|critical|fatal)\b', line, re.I))
                        is_warn = bool(re.search(r'\bwarn(ing)?\b', line, re.I))
                        rendered = Text(line)
                        if is_err: rendered.stylize(SSOT['error'])
                        elif is_warn: rendered.stylize(SSOT['warning'])
                        self.call_from_thread(log_box.write,
                                              Text.assemble((f"[Linux:{label}] ", f"dim {SSOT['subtle']}"), rendered))
                    try:
                        proc.terminate()
                    except Exception: pass
                    finally:
                        if proc in self.journal_procs:
                            self.journal_procs.remove(proc)
                except Exception as exc:
                    if self.tailing:
                        self.call_from_thread(log_box.write, Text(f"[Linux:{label}] collector: {exc}", style=SSOT['warning']))

            def stream_windows_events():
                cursors = {"System": None, "Application": None}
                while self.tailing:
                    for channel in cursors:
                        cursor = cursors[channel]
                        cmd = ["wevtutil", "qe", channel, "/f:xml",
                               f"/c:{settings['batch_size']}"]
                        if cursor is None:
                            cmd.append("/rd:true")
                        else:
                            cmd.extend(("/rd:false", f"/q:*[System[EventRecordID>{cursor}]]"))
                        try:
                            result = subprocess.run(cmd, capture_output=True,
                                                    timeout=settings["flush_interval_s"],
                                                    text=True, encoding="utf-8", errors="replace")
                            if result.returncode:
                                raise OSError(result.stderr.strip() or f"wevtutil exit {result.returncode}")
                            events = list(parse_windows_events(result.stdout))
                            if cursor is None:
                                events.reverse()
                            for record, level, message in events:
                                if cursor is not None and record <= cursor:
                                    continue
                                rendered = Text(f"[Windows:{channel}] {message}")
                                if level in (1, 2): rendered.stylize(SSOT['error'])
                                elif level == 3: rendered.stylize(SSOT['warning'])
                                self.call_from_thread(log_box.write, rendered)
                                cursors[channel] = record
                        except (OSError, subprocess.TimeoutExpired) as exc:
                            self.call_from_thread(log_box.write,
                                                  Text(f"[Windows:{channel}] collector: {exc}", style=SSOT['warning']))
                    time.sleep(settings["flush_interval_s"])

            def _find_flash_logs():
                candidates = [
                    r"C:\Windows\Temp\mios-cat-install.log",
                    r"C:\Windows\Temp\mios-cat-flash.log",
                    r"C:\mios-bootstrap\installation\mios-install-live.log",
                    os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "mios-cat-install.log"),
                    os.path.join(os.environ.get("TEMP", r"C:\Windows\Temp"), "mios-cat-flash.log"),
                    "/tmp/mios-cat-install.log"
                ]
                for d in [r"C:\mios-bootstrap\installation", r"C:\MiOS\logs", r"M:\MiOS\logs"]:
                    if os.path.isdir(d):
                        candidates.extend(glob.glob(os.path.join(d, "mios-field-*.log")))
                        candidates.extend(glob.glob(os.path.join(d, "MiOS-Field*.log")))
                        candidates.extend(glob.glob(os.path.join(d, "mios-cat-*.log")))
                        candidates.extend(glob.glob(os.path.join(d, "flash-*.log")))

                found = [c for c in dict.fromkeys(candidates) if os.path.exists(c)]
                found.sort(key=os.path.getmtime, reverse=True)
                return found

            def stream_flash_log():
                if not flash_log_box: return
                current_log = None
                file_obj = None

                while self.tailing:
                    logs = _find_flash_logs()
                    if not logs:
                        time.sleep(1)
                        continue

                    newest_log = logs[0]
                    if newest_log != current_log:
                        current_log = newest_log
                        if file_obj:
                            try: file_obj.close()
                            except Exception: pass
                        try:
                            self.call_from_thread(flash_log_box.write, Text(f"Streaming flash log: {os.path.basename(current_log)}", style=SSOT['success']))
                            file_obj = open(current_log, 'r', encoding='utf-8', errors='ignore')
                            lines = file_obj.readlines()
                            for line in lines[-40:]:
                                line = line.replace('\x00', '').strip()
                                if line:
                                    self.call_from_thread(flash_log_box.write, Text(line))
                            file_obj.seek(0, 2)
                        except Exception:
                            file_obj = None
                            time.sleep(1)
                            continue

                    if not file_obj:
                        time.sleep(1)
                        continue

                    idle_count = 0
                    while self.tailing and current_log == newest_log:
                        line = file_obj.readline()
                        if line:
                            line = line.replace('\x00', '').strip()
                            if line:
                                self.last_flash_log_time = time.time()
                                self.call_from_thread(flash_log_box.write, Text(line))
                                self.call_from_thread(log_box.write, Text.assemble(("flash ", "dim"), Text(line)))
                            idle_count = 0
                        else:
                            idle_count += 1
                            time.sleep(0.2)
                            if idle_count > 10:
                                idle_count = 0
                                check_logs = _find_flash_logs()
                                if check_logs and check_logs[0] != current_log:
                                    newest_log = check_logs[0]
                                    break
                                try:
                                    if os.path.getsize(current_log) < file_obj.tell():
                                        file_obj.seek(0)
                                except Exception: pass

            if flash_log_box: threading.Thread(target=stream_flash_log, daemon=True).start()
            # The build log is polled on the UI thread by refresh_build_log.
            # A detached tail thread could die when root merge replaces its log.
            if IS_WINDOWS:
                threading.Thread(target=stream_windows_events, daemon=True).start()
                workers = {}
                last_running = None
                while self.tailing:
                    running = {name.casefold(): name for name in running_wsl_distros()}
                    selected = tuple(running[name.casefold()] for name in settings["distros"]
                                     if name.casefold() in running)
                    if selected != last_running:
                        status = ", ".join(selected) if selected else "waiting for a running MiOS distro"
                        self.call_from_thread(log_box.write,
                                              Text(f"[Linux] {status}", style=SSOT['subtle']))
                        last_running = selected
                    for configured in settings["distros"]:
                        name = running.get(configured.casefold())
                        if name and (name not in workers or not workers[name].is_alive()):
                            cmd = ["wsl.exe", "-d", name, "-u", "root", "--",
                                   "journalctl", "-f", "-n", str(settings["batch_size"]),
                                   "--no-pager", "-o", "short-iso"]
                            workers[name] = threading.Thread(target=stream_proc, args=(cmd, name), daemon=True)
                            workers[name].start()
                    time.sleep(settings["flush_interval_s"])
            else:
                stream_proc(["journalctl", "-f", "-n", str(settings["batch_size"]),
                             "--no-pager", "-o", "short-iso"], "MiOS")

        def refresh_build_log(self):
            try:
                build_box = self.query_one("#build-log-box", RichLog)
                global_box = self.query_one("#log-box", RichLog)
                paths = [os.environ.get("MIOS_UNIFIED_LOG"), os.environ.get("MIOS_BUILD_LOG")]
                for directory in (os.environ.get("MIOS_LOG_DIR"),
                                  r"M:\MiOS\logs", r"C:\MiOS\logs",
                                  r"C:\mios-bootstrap\installation",
                                  "/mnt/c/MiOS/logs", "/mnt/m/MiOS/logs", "/var/log/mios"):
                    if directory and os.path.isdir(directory):
                        for pattern in ("mios-install-*.log", "mios-build-*.log",
                                        "deploy*.log", "build*.log"):
                            paths.extend(glob.glob(os.path.join(directory, pattern)))
                available = []
                for path in set(filter(None, paths)):
                    try:
                        stat = os.stat(path)
                        if os.path.isfile(path):
                            available.append((stat.st_mtime_ns, path))
                    except OSError:
                        continue
                if not available:
                    return
                _, path = max(available)

                def display(line):
                    line = line.rstrip("\r\n")
                    if not line.strip():
                        return
                    if "step:" in line:
                        self.build_log_phase = line.split("step:", 1)[1].strip()
                    low = line.lower()
                    rendered = Text(line)
                    if any(marker in low for marker in ("[error]", "traceback", "exception", "panic")):
                        rendered.stylize(SSOT['error'])
                    elif "[warn]" in low or "warning" in low:
                        rendered.stylize(SSOT['warning'])
                    build_box.write(rendered)
                    global_box.write(rendered)

                with open(path, "r", encoding="utf-8", errors="replace") as stream:
                    stat = os.fstat(stream.fileno())
                    identity = (stat.st_dev, stat.st_ino)
                    replaced = (path != self.build_log_path or
                                identity != self.build_log_identity)
                    if replaced:
                        history = deque(stream, maxlen=150)
                        build_box.clear()
                        self.build_log_phase = "-"
                        banner = Text(f"Streaming build log: {os.path.basename(path)}", style=SSOT['success'])
                        build_box.write(banner)
                        global_box.write(banner)
                        for line in history:
                            display(line)
                    else:
                        if stat.st_size < self.build_log_offset:
                            build_box.clear()
                            self.build_log_offset = 0
                            self.build_log_phase = "-"
                        stream.seek(self.build_log_offset)
                        for _ in range(300):
                            line = stream.readline()
                            if not line:
                                break
                            display(line)
                    self.build_log_offset = stream.tell()
                    self.build_log_path = path
                    self.build_log_identity = identity
                    self.last_build_log_time = stat.st_mtime
            except (OSError, ValueError):
                # Root merge may replace the log directory between stat/open/read.
                self.build_log_path = None
                self.build_log_offset = 0
                self.build_log_identity = None

        def update_telemetry(self):
            cpu, ram, root, m_disk, load = get_telemetry()
            sys_info = get_sys_info()

            self.cpu_history.append(float(cpu))
            if len(self.cpu_history) > 60: self.cpu_history.pop(0)
            try:
                self.query_one("#spark-widget", Sparkline).data = list(self.cpu_history)
            except Exception: pass

            if self.size.width < 95 or self.size.height < 28:
                # 80x20 SSOT compact mode: 4 clean lines, max 36 chars wide, 0 line wraps
                mem = psutil.virtual_memory() if psutil else None
                swap = psutil.swap_memory() if psutil else None
                net = None
                if psutil:
                    try: net = psutil.net_io_counters()
                    except Exception: pass

                hw_lines = [
                    f"[{SSOT['subtle']} bold]CPU:[/] {make_bar(cpu, 10)} [{SSOT['subtle']} bold]{cpu:4.1f}%[/] | [{SSOT['subtle']}]Ld {str(load)[:4]}[/]"
                ]
                if mem:
                    hw_lines.append(f"[{SSOT['warning']} bold]RAM:[/] {make_bar(mem.percent, 10)} {mem.used/(1024**3):.1f}/{mem.total/(1024**3):.1f}G")
                if swap:
                    hw_lines.append(f"[{SSOT['warning']} bold]Swp:[/] {make_bar(swap.percent, 8)} {swap.percent}% | C:{root}% M:{m_disk}%")
                if net:
                    hw_lines.append(f"[{SSOT['success']} bold]Net:[/] ↑{net.bytes_sent/(1024**2):.0f}M ↓{net.bytes_recv/(1024**2):.0f}M")
            else:
                # Fullscreen / expanded mode: per-core breakdown
                hw_lines = [
                    f"[{SSOT['subtle']} bold]CPU Model:[/] {sys_info['cpu_model'][:36]}",
                    f"[{SSOT['subtle']} bold]Load:[/] {load} | [{SSOT['subtle']} bold]Usage:[/] {make_bar(cpu, 18)} [{SSOT['subtle']} bold]{cpu:.1f}%[/]",
                    ""
                ]
                if psutil:
                    cpu_percs = psutil.cpu_percent(percpu=True)
                    half = (len(cpu_percs) + 1) // 2
                    for i in range(min(half, 8)):
                        c1_num = i
                        c1_val = cpu_percs[c1_num]
                        c1_str = f"C{c1_num:02d} {make_bar(c1_val, 8)} [dim]{c1_val:4.1f}%[/]"

                        c2_num = i + half
                        if c2_num < len(cpu_percs):
                            c2_val = cpu_percs[c2_num]
                            c2_str = f"C{c2_num:02d} {make_bar(c2_val, 8)} [dim]{c2_val:4.1f}%[/]"
                        else:
                            c2_str = ""
                        hw_lines.append(f"  {c1_str:<32}  {c2_str}")

                    hw_lines.append("")
                    mem = psutil.virtual_memory()
                    swap = psutil.swap_memory()
                    hw_lines.append(f"[{SSOT['warning']} bold]RAM:[/]  {make_bar(mem.percent, 16)} {mem.used/(1024**3):.1f}/{mem.total/(1024**3):.1f} GB ({mem.percent}%)")
                    hw_lines.append(f"[{SSOT['warning']} bold]Swap:[/] {make_bar(swap.percent, 16)} {swap.used/(1024**3):.1f}/{swap.total/(1024**3):.1f} GB ({swap.percent}%)")
                    hw_lines.append("")
                    hw_lines.append(f"[{SSOT['subtle']} bold]Disk C:[/] {make_bar(root, 12)} {root}%   |   [{SSOT['subtle']} bold]Disk M:[/] {make_bar(m_disk, 12)} {m_disk}%")

                    try:
                        net = psutil.net_io_counters()
                    except Exception:
                        net = None
                    if net is None:
                        hw_lines.append(f"[{SSOT['success']} bold]Network I/O:[/] unavailable")
                    else:
                        hw_lines.append(f"[{SSOT['success']} bold]Net Sent:[/] {net.bytes_sent/(1024**2):.1f} MB   |   [{SSOT['success']} bold]Net Recv:[/] {net.bytes_recv/(1024**2):.1f} MB")

            self.query_one("#hw-box", Static).update("\n".join(hw_lines))

            if self.size.width < 95 or self.size.height < 28:
                t_lines = [
                    f"[black on {SSOT['subtle']}] USER [/] {sys_info['user']}@{sys_info['host']}",
                    f"[black on {SSOT['warning']}] UPTIME [/] {sys_info['uptime']}"
                ]
            else:
                t_lines = [
                    f"[black on {SSOT['subtle']}]  USER [/] {sys_info['user']}@{sys_info['host']}",
                    f"[black on {SSOT['success']}]  KERNEL [/] {sys_info['kernel']}",
                    f"[black on {SSOT['warning']}] ⏱ UPTIME [/] {sys_info['uptime']}"
                ]
            self.query_one("#sys-identity", Static).update("\n".join(t_lines))

            u_lines = [
                f"[{SSOT['warning']} bold]USB:[/] {get_usb_drive_info()}",
                f"[{SSOT['success']} bold]GIT:[/] {get_git_tree_status()}"
            ]
            self.query_one("#forge-box", Static).update("\n".join(u_lines))

            try:
                agents, messages, slots = get_agent_and_mcp_data()
                ai_log_box = self.query_one("#ai-log-box", RichLog)

                # Stream slot state transitions into #ai-log-box
                if not hasattr(self, "_seen_slot_states"):
                    self._seen_slot_states = {}
                current_slot_states = {str(sl["identity"]): (sl["pid"], sl["cmd"], sl.get("active", False)) for sl in slots}
                ts_now = datetime.now().strftime("%H:%M:%S")
                for pane, (pid, cmd, active) in current_slot_states.items():
                    if pane not in self._seen_slot_states:
                        if active:
                            ai_log_box.write(f"[{SSOT['success']}]\\[{ts_now}] \\[SLOT ACTIVE][/] Slot [cyan]{escape(str(pane))}[/] (PID {pid}): [green]{escape(str(cmd))}[/]")
                        else:
                            ai_log_box.write(f"[{SSOT['subtle']}]\\[{ts_now}] \\[SLOT READY][/] Slot [cyan]{escape(str(pane))}[/] (PID {pid}): [dim]{escape(str(cmd))}[/]")
                    else:
                        prev_pid, prev_cmd, prev_active = self._seen_slot_states[pane]
                        if active and (not prev_active or prev_cmd != cmd):
                            ai_log_box.write(f"[{SSOT['warning']}]\\[{ts_now}] \\[SLOT EXEC][/] Slot [cyan]{escape(str(pane))}[/] (PID {pid}): [yellow]{escape(str(cmd))}[/]")
                        elif not active and prev_active:
                            ai_log_box.write(f"[{SSOT['subtle']}]\\[{ts_now}] \\[SLOT IDLE][/] Slot [cyan]{escape(str(pane))}[/] (PID {pid}): released")
                for pane, (pid, prev_cmd, prev_active) in self._seen_slot_states.items():
                    if pane not in current_slot_states:
                        ai_log_box.write(f"[{SSOT['subtle']}]\\[{ts_now}] \\[SLOT CLOSED][/] Slot [cyan]{escape(str(pane))}[/] (PID {pid}): closed")
                self._seen_slot_states = current_slot_states
                seen = getattr(self, "_seen_ai_messages", None)
                for m in (messages[-10:] if seen is None else messages):
                    key = (m.get("message_id"), m.get("status"))
                    if seen is not None and key in seen:
                        continue
                    ts = datetime.fromtimestamp(m.get("created", time.time())).strftime("%H:%M:%S")
                    status = m.get("status", "msg").upper()
                    color = {"RECEIVED": "green", "QUEUED": "yellow"}.get(status, "cyan")
                    peers = [str(m.get(k, "?")) for k in ("from", "to")]
                    peers = [p if len(p) <= 22 else p[:10] + ".." + p[-10:] for p in peers]
                    preview = escape(str(m.get("message_id", "")))
                    ai_log_box.write(f"[{color}]\\[{ts}] \\[{status}][/] [cyan]{escape(peers[0])}[/] → [magenta]{escape(peers[1])}[/]\n  [dim]\"{preview}\"[/]")
                # Bound deduplication to the retained native receipt snapshot.
                self._seen_ai_messages = {(m.get("message_id"), m.get("status")) for m in messages}
            except Exception: pass

            try:
                last_log_t = getattr(self, 'last_flash_log_time', None)
                if last_log_t:
                    elapsed = int(time.time() - last_log_t)
                    if elapsed < 15:
                        status_str = f"[{SSOT['success']} bold]FLASHING IN PROGRESS (Active)[/]"
                    elif elapsed < 60:
                        status_str = f"[{SSOT['warning']} bold]FLASHING ACTIVE ({elapsed}s since line)[/]"
                    else:
                        status_str = f"[{SSOT['subtle']}]INACTIVE ({elapsed}s ago)[/]"
                else:
                    status_str = f"[{SSOT['subtle']}]Waiting for log stream...[/]"

                flash_lines = [
                    f"[{SSOT['accent']} bold]MiOS Field USB Builder[/]",
                    f"[{SSOT['subtle']}]Target Drive:[/] {get_usb_drive_info()}",
                    f"[{SSOT['subtle']}]Status:[/] {status_str}",
                    "",
                    "Real-time compilation & imaging logs stream ->"
                ]
                self.query_one("#flash-stats", Static).update("\n".join(flash_lines))
            except Exception: pass

            try:
                last_build_t = getattr(self, "last_build_log_time", None)
                bpath = getattr(self, "build_log_path", None)
                phase_str = escape(getattr(self, "build_log_phase", "-")[:38])
                if last_build_t:
                    el = int(time.time() - last_build_t)
                    if el < 20: bstat = f"[{SSOT['success']} bold]BUILDING (active)[/]"
                    elif el < 120: bstat = f"[{SSOT['warning']} bold]IDLE ({el}s since log)[/]"
                    else: bstat = f"[{SSOT['subtle']}]NO RECENT OUTPUT ({el}s since log)[/]"
                else:
                    bstat = f"[{SSOT['subtle']}]Waiting for build/install...[/]"
                build_lines = [
                    f"[{SSOT['accent']} bold]MiOS Build / Install[/]",
                    f"[{SSOT['subtle']}]Status:[/] {bstat}",
                    f"[{SSOT['subtle']}]Phase:[/] {phase_str}",
                    f"[{SSOT['subtle']}]Log:[/] {escape(os.path.basename(bpath)) if bpath else '-'}",
                    "",
                    "Live install/build pipeline stream ->",
                ]
                self.query_one("#build-stats", Static).update("\n".join(build_lines))
            except Exception: pass

        def async_update_services(self):
            threading.Thread(target=self.update_services, daemon=True).start()

        def update_services(self):
            svcs = get_services()
            self.service_status = {name: online for name, _, online in svcs}
            try:
                table = self.query_one("#svc-table", DataTable)
                def apply_updates():
                    table.clear()
                    for s in svcs:
                        status = f"[{SSOT['success']} bold]ONLINE[/]" if s[2] else f"[{SSOT['error']} bold]OFFLINE[/]"
                        name_str = s[0].ljust(35)
                        port_str = str(s[1]).ljust(15)
                        table.add_row(Text.from_markup(name_str), Text.from_markup(port_str), Text.from_markup(status))
                self.call_from_thread(apply_updates)
            except Exception: pass

        def apply_responsive_layout(self, width: int, height: int) -> None:
            self.set_class(width < 95 or height < 28, "compact")
            try:
                main_c = self.query_one("#main-container")
                build_c = self.query_one("#build-container")
                flash_c = self.query_one("#flash-container")
                ai_c = self.query_one("#ai-container")
                left_p = self.query_one("#left-pane")
                right_p = self.query_one("#right-pane")

                b_stats = self.query_one("#build-stats-pane")
                f_stats = self.query_one("#flash-stats-pane")

                b_log = self.query_one("#build-log-box")
                f_log = self.query_one("#flash-log-box")
                a_log = self.query_one("#ai-log-box")

                # Responsive orientation detection:
                # Multi-tier responsive orientation and scaling:
                # 1. Narrow / Mobile Portrait (width < 78 or height > width)
                # 2. Compact Height / Mobile Landscape (height < 28 or width < 95)
                # 3. Standard / Desktop
                is_narrow = (width < 78)
                is_portrait = (height > width) or is_narrow
                is_compact_height = (height < 28)
                is_compact = is_narrow or is_compact_height or (width < 95)

                try:
                    agent_view = self.query_one(AgentView)
                except Exception:
                    agent_view = None

                if is_compact:
                    if a_log:
                        a_log.styles.display = "none"
                    if agent_view:
                        agent_view.styles.width = "100%"
                        agent_view.styles.height = "100%"
                else:
                    if a_log:
                        a_log.styles.display = "block"

                if is_portrait:
                    for c in (main_c, build_c, flash_c, ai_c):
                        c.styles.layout = "vertical"
                    left_p.styles.width = "100%"
                    left_p.styles.height = "auto"
                    left_p.styles.max_height = 18 if height > 40 else 12
                    left_p.styles.margin_left = 0
                    left_p.styles.margin_top = 0
                    right_p.styles.width = "100%"
                    right_p.styles.height = "1fr"
                    right_p.styles.margin_left = 0
                    right_p.styles.margin_top = 0

                    for stats in (b_stats, f_stats):
                        stats.styles.width = "100%"
                        stats.styles.height = "auto"
                        stats.styles.max_height = 14 if height > 40 else 10

                    for lbox in (b_log, f_log):
                        lbox.styles.width = "100%"
                        lbox.styles.height = "1fr"

                    if not is_compact:
                        if agent_view:
                            agent_view.styles.width = "100%"
                            agent_view.styles.height = "1fr"
                        if a_log:
                            a_log.styles.width = "100%"
                            a_log.styles.height = "1fr"
                else:
                    for c in (main_c, build_c, flash_c, ai_c):
                        c.styles.layout = "horizontal"
                    if is_compact_height:
                        left_p.styles.width = 34 if width >= 90 else "1fr"
                        left_p.styles.height = "100%"
                        left_p.styles.margin_left = 0
                        left_p.styles.margin_top = 0
                        right_p.styles.width = "1fr"
                        right_p.styles.height = "100%"
                        right_p.styles.margin_left = 1
                        right_p.styles.margin_top = 0

                        for stats in (b_stats, f_stats):
                            stats.styles.width = 30 if width >= 90 else 26
                            stats.styles.height = "100%"

                        for lbox in (b_log, f_log):
                            lbox.styles.width = "1fr"
                            lbox.styles.height = "100%"
                    else:
                        left_p.styles.width = 48 if width >= 130 else 38
                        left_p.styles.height = "100%"
                        left_p.styles.margin_left = 0
                        left_p.styles.margin_top = 0
                        right_p.styles.width = "1fr"
                        right_p.styles.height = "100%"
                        right_p.styles.margin_left = 1
                        right_p.styles.margin_top = 0

                        for stats in (b_stats, f_stats):
                            stats.styles.width = 38
                            stats.styles.height = "100%"

                        for lbox in (b_log, f_log):
                            lbox.styles.width = "1fr"
                            lbox.styles.height = "100%"

                        if not is_compact:
                            if agent_view:
                                agent_view.styles.width = 48 if width >= 120 else "1fr"
                                agent_view.styles.height = "100%"
                            if a_log:
                                a_log.styles.width = "1fr"
                                a_log.styles.height = "100%"
            except Exception: pass

        def on_click(self, event) -> None:
            # Touch: compact/phone views hide the tab strip and have no F1-F5 keys, so
            # tapping a plain status/heading line cycles to the next view. Tables,
            # inputs and logs keep their own tap behaviour (row select, focus, scroll).
            if not self.has_class("compact"):
                return
            try:
                widget, _ = self.get_widget_at(event.screen_x, event.screen_y)
            except Exception:
                return
            if getattr(widget, "id", None) in {"client-intro", "client-status", "relay-count", "worker-count",
                                               "receipt", "agent-error", "system-status"}:
                self.cycle_view(1)

        def on_resize(self, event) -> None:
            self.apply_responsive_layout(event.size.width, event.size.height)

        def action_toggle_dark(self) -> None:
            self.dark = not self.dark

        def on_unmount(self) -> None:
            self.tailing = False
            for proc in list(getattr(self, "journal_procs", [])):
                try: proc.terminate()
                except OSError: pass

def _run_monitor_app(**kwargs):
    # Textual installs process-wide handlers in the driver constructor. Restore
    # the caller's handlers even when run/initialization fails, before another
    # terminal client inherits control after the Textual event loop closes.
    names = ("SIGINT", "SIGTERM", "SIGTSTP", "SIGCONT", "SIGWINCH", "SIGTTIN", "SIGTTOU")
    previous = {getattr(signal, name): signal.getsignal(getattr(signal, name))
                for name in names if hasattr(signal, name)}
    try:
        return MiosMonitorApp(**kwargs).run()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def main():
    global PIPELINE_MODE, AI_MODE, TAB_CHOICE
    parser = argparse.ArgumentParser(description="MiOS-Mon -- Unified TUI & System Monitor")
    parser.add_argument("--mini", "--metal", action="store_true", help="compact mini/metal service layout")
    parser.add_argument("--dash", action="store_true", help="full system dashboard layout")
    parser.add_argument("--monitor", action="store_true", help="fullscreen interactive TUI monitor")
    parser.add_argument("--pipeline", action="store_true",
                        help="open directly on the live installer/build log tab")
    parser.add_argument("--ai", action="store_true",
                        help="open directly on the live MiOS-Ai monitoring tab")
    parser.add_argument("--tab", choices=["global", "build", "flash", "ai"], default=None,
                        help="initial active tab")
    parser.add_argument("--ui-mode", choices=["clients", "agents"], help="initial embedded workspace view")
    parser.add_argument("--ui-request-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--once", action="store_true", help="print snapshot once and exit")
    args, unknown = parser.parse_known_args()
    PIPELINE_MODE = args.pipeline
    unknown_lower = [a.lower() for a in unknown]
    AI_MODE = args.ai or (args.tab == "ai") or ("ai" in unknown_lower)
    TAB_CHOICE = args.tab

    mode = "monitor"
    unknown_lower = [a.lower() for a in unknown]
    if args.mini or "-mini" in unknown_lower or "--metal" in unknown_lower or "-metal" in unknown_lower or os.environ.get("MIOS_COMPACT") == "1":
        mode = "mini"
    elif args.dash or "-dash" in unknown_lower or os.environ.get("MIOS_DASH_SERVICES") == "1":
        mode = "dash"

    if args.once:
        if mode == "dash":
            console.print(create_dash_layout())
        else:
            console.print(create_metal_layout())
        sys.exit(0)

    if mode == "mini":
        console.print(create_metal_layout())
        sys.exit(0)
    elif mode == "dash":
        console.print(create_dash_layout())
        sys.exit(0)

    if not TEXTUAL_AVAILABLE:
        if _install_deps(["textual", "psutil"]):
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception:
                pass
        print("\033[33m[MiOS-Mon] Interactive monitor mode requires 'textual' and 'psutil'.\033[0m")
        print("\033[33m[MiOS-Mon] Falling back to live dashboard refresh mode. Press Ctrl+C to exit.\033[0m")
        import time
        try:
            while True:
                console.clear()
                console.print(create_dash_layout())
                time.sleep(2)
        except KeyboardInterrupt:
            sys.exit(0)

    if args.ui_request_stdin:
        request = json.load(sys.stdin)
        # Textual's Linux driver reads sys.__stdin__ and its file descriptor.
        # Replacing only sys.stdin leaves raw mode/input on the JSON pipe.
        # Bind fd 0 too, so the selected CLI also inherits the controlling TTY.
        with open("/dev/tty", "r", encoding="utf-8") as terminal:
            os.dup2(terminal.fileno(), 0)
        sys.stdin = sys.__stdin__ = open(0, "r", encoding="utf-8", closefd=False)
    else:
        try:
            data = monitor_config()
            cmd = [data.get("mcp", {}).get("python", "python3"), "/usr/libexec/mios/mios-mcp-server", "--monitor-request"]
            if IS_WINDOWS:
                cmd = ["wsl.exe", "-d", "podman-MiOS-DEV", "-u", "mios", "--", "python3", "/usr/libexec/mios/mios-mcp-server", "--monitor-request"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
            request = json.loads(result.stdout)
        except Exception:
            try:
                data = monitor_config()
                tools = data.get("agent_cli", {}).get("tools", [])
                request = {
                    "workspace": data.get("mcp", {}).get("tmux", {}).get("workspace", {}),
                    "colors": mios_colors(data),
                    "agents": [{**t, "installed": True} for t in tools]
                }
            except Exception:
                request = {}
    while True:
        selected = _run_monitor_app(ui_request=request, ui_mode=args.ui_mode, ansi_color=TRANSPARENT_TERMINAL)
        if not selected:
            break
        if IS_WINDOWS:
            subprocess.run(["wsl.exe", "-d", "podman-MiOS-DEV", "-u", "mios", "--", "/usr/bin/mios", "agent", selected], check=False)
        else:
            subprocess.run(["/usr/bin/mios", "agent", selected], check=False)

if __name__ == '__main__':
    main()
