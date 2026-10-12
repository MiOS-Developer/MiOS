# AI-hint: Shared fixed-table widgets for the unified MiOS Monitor; presentation never consumes or acknowledges relay messages.
# AI-related: /usr/libexec/mios/mios-mon.py, /usr/libexec/mios/mios-mcp-server, /usr/lib/mios/agent-pipe/test_mios_agent_tui.py
# AI-functions: clean, peer_name, sync_rows, ClientView, AgentView, SystemSummary

import asyncio
import re
import unicodedata

from rich.text import Text
from textual import on
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import DataTable, Input, Static


def clean(value, limit=100):
    """Render peer metadata literally, excluding terminal and bidi controls."""
    return "".join(c for c in str(value) if not unicodedata.category(c).startswith("C"))[:limit]


def peer_name(row):
    kind = clean(row.get("kind", "agent"), 12).capitalize()
    label = clean(row.get("label", ""))
    if "·" in label:
        return f"{kind} {label.split()[-1]}"
    return label or kind


def sync_rows(table, rows):
    """Refresh cells in place so polling does not reset selection or scroll."""
    wanted = {key for key, _ in rows}
    for key in list(table.rows):
        if key.value not in wanted:
            table.remove_row(key)
    columns = list(table.columns)
    for key, cells in rows:
        values = [Text(clean(cell)) for cell in cells]
        if key in table.rows:
            for column, value in zip(columns, values):
                table.update_cell(key, column, value)
        else:
            table.add_row(*values, key=key)


class ClientView(Vertical):
    """The same chooser is embedded in a head pane and in MiOS Monitor."""
    class Selected(Message):
        def __init__(self, name):
            super().__init__()
            self.name = name

    DEFAULT_CSS = """
    ClientView {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        overflow-x: hidden;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-background: transparent;
        scrollbar-color: #1A407F;
        background: transparent;
    }
    ScrollBar {
        width: 1;
        min-width: 1;
        max-width: 1;
        background: transparent;
    }
    ScrollBar.-horizontal {
        height: 0;
        min-height: 0;
        display: none;
    }
    ScrollBar.-vertical {
        width: 1;
        min-width: 1;
        max-width: 1;
        background: transparent;
    }
    ScrollBarCorner {
        display: none;
        background: transparent;
    }
    ScrollBarThumb {
        background: #1A407F;
        color: #1A407F;
    }
    ScrollBarThumb:hover {
        background: #F35C15;
    }
    DataTable {
        height: auto;
        scrollbar-size: 0 0;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
        overflow-x: hidden;
        overflow-y: hidden;
        background: transparent;
    }
    DataTable > .datatable--header { background: #1A407F; color: #E7DFD3; text-style: bold; }
    DataTable > .datatable--odd-row { background: transparent; }
    DataTable > .datatable--even-row { background: transparent; }
    DataTable > .datatable--cursor { background: #1A407F; color: #E7DFD3; }
    #client-intro { height: 2; }
    #client-table { height: auto; min-height: 2; }
    #client-input, #client-status { height: 1; }
    #client-input { border: none; padding: 0; background: transparent; }
    #client-status { color: #F35C15; }
    """

    def __init__(self, agents, **kwargs):
        super().__init__(**kwargs)
        self.agents = [row for row in agents if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", str(row.get("name", "")))]

    def compose(self):
        yield Static("Choose a head CLI.\nMCP connects visible workers.", id="client-intro", markup=False)
        yield DataTable(id="client-table", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield Input(placeholder="Client number / name", id="client-input")
        yield Static("Arrows + Enter, or type a client", id="client-status", markup=False)

    def on_mount(self):
        table = self.query_one(DataTable)
        for title, width in (("#", 2), ("Client", 10), ("Tools", 8)):
            table.add_column(title, width=width)
        sync_rows(table, [(row["name"], (index, row["name"],
                   "Missing" if not row.get("installed") else "MCP" if row.get("mcp") else "CLI"))
                   for index, row in enumerate(self.agents, 1)])
        # The app assigns focus after TabbedContent mounts. A hidden chooser
        # focusing itself here would activate Clients in the observer pane.

    def select_client(self, name):
        row = next((row for row in self.agents if row["name"] == name), None)
        if row is None or not row.get("installed"):
            self.query_one("#client-status", Static).update("Choose an installed client")
            return
        self.post_message(self.Selected(name))

    @on(Input.Submitted)
    def submitted(self, event):
        value = event.value.strip().lower()
        if value.isdecimal() and 1 <= int(value) <= len(self.agents):
            value = self.agents[int(value) - 1]["name"]
        self.select_client(value)

    @on(DataTable.RowSelected)
    def row_selected(self, event):
        self.select_client(event.row_key.value)

    def on_key(self, event):
        if event.key in {"up", "down"} and isinstance(self.app.focused, Input):
            self.query_one(DataTable).focus()
            event.stop()
            event.prevent_default()


class AgentView(Vertical):
    """Separate registered peers from detected tmux processes and receipts."""
    DEFAULT_CSS = """
    AgentView {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        overflow-x: hidden;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-background: transparent;
        scrollbar-color: #1A407F;
        background: transparent;
    }
    ScrollBar {
        width: 1;
        min-width: 1;
        max-width: 1;
        background: transparent;
    }
    ScrollBar.-horizontal {
        height: 0;
        min-height: 0;
        display: none;
    }
    ScrollBar.-vertical {
        width: 1;
        min-width: 1;
        max-width: 1;
        background: transparent;
    }
    ScrollBarCorner {
        display: none;
        background: transparent;
    }
    ScrollBarThumb {
        background: #1A407F;
        color: #1A407F;
    }
    ScrollBarThumb:hover {
        background: #F35C15;
    }
    DataTable {
        height: auto;
        scrollbar-size: 0 0;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
        overflow-x: hidden;
        overflow-y: hidden;
        background: transparent;
    }
    DataTable > .datatable--header { background: #1A407F; color: #E7DFD3; text-style: bold; }
    DataTable > .datatable--odd-row { background: transparent; }
    DataTable > .datatable--even-row { background: transparent; }
    DataTable > .datatable--cursor { background: #1A407F; color: #E7DFD3; }
    #relay-count, #worker-count, #receipt, #agent-error { height: 1; }
    #peer-table { height: auto; min-height: 2; }
    #worker-table { height: auto; min-height: 2; }
    #receipt { color: #948E8E; }
    #agent-error { color: #F35C15; }
    """

    def __init__(self, observer, refresh_s=2, **kwargs):
        super().__init__(**kwargs)
        self.observer = observer
        self.refresh_s = refresh_s
        self.refreshing = False
        self.snapshot = {}

    def compose(self):
        yield Static("Relay: loading", id="relay-count", markup=False)
        yield DataTable(id="peer-table", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield Static("Tmux workers", id="worker-count", markup=False)
        yield DataTable(id="worker-table", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield Static("Receipts: read does not mean done", id="receipt", markup=False)
        yield Static("", id="agent-error", markup=False)

    def on_mount(self):
        peers = self.query_one("#peer-table", DataTable)
        for title, width in (("Agent", 18), ("State", 7), ("Q", 2)):
            peers.add_column(title, width=width)
        workers = self.query_one("#worker-table", DataTable)
        for title, width in (("Pane", 6), ("Client", 12), ("State", 7)):
            workers.add_column(title, width=width)
        self.set_interval(self.refresh_s, self.refresh_snapshot)
        self.call_after_refresh(self.refresh_snapshot)

    async def refresh_snapshot(self):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            snapshot = await asyncio.to_thread(self.observer)
            self.render_snapshot(snapshot)
        except Exception as exc:
            self.query_one("#agent-error", Static).update(f"Relay unavailable: {clean(exc, 60)}")
        finally:
            self.refreshing = False

    def render_snapshot(self, snapshot):
        self.snapshot = snapshot
        agents = snapshot.get("agents", [])
        online = sum(bool(row.get("online")) for row in agents)
        self.query_one("#relay-count", Static).update(f"Relay: {online} online / {len(agents)}")
        sync_rows(self.query_one("#peer-table", DataTable), [(row["agent_id"], (
            peer_name(row), "Online" if row.get("online") else "Offline", row.get("pending", 0))) for row in agents])
        panes = sorted(snapshot.get("panes", []), key=lambda row: (row.get("role", ""), row.get("pane", "")))
        self.query_one("#worker-count", Static).update(f"Tmux: {len(panes)} panes · not peers")
        sync_rows(self.query_one("#worker-table", DataTable), [(str(row.get("socket", "")) + row["pane"], (
            row.get("role") or row["pane"], row.get("agent_kind") or row.get("command", ""),
            "Exited" if row.get("dead") else "Empty" if row.get("command") == "sleep" else "Running")) for row in panes])
        messages = snapshot.get("messages", [])
        queued = sum(row.get("status") == "queued" for row in messages)
        read = sum(row.get("status") == "received" for row in messages)
        self.query_one("#receipt", Static).update(f"Messages: {queued} queued / {read} read")
        errors = snapshot.get("errors", [])
        self.query_one("#agent-error", Static).update(clean(errors[0]) if errors else "Read receipts do not certify work")
        self.adapt_columns(self.size.width)

    def adapt_columns(self, width):
        # Responsive column widths keep every status visible, without word-wrap.
        peer = self.query_one("#peer-table", DataTable)
        worker = self.query_one("#worker-table", DataTable)
        list(peer.columns.values())[0].width = max(8, width - 17)
        list(worker.columns.values())[1].width = max(7, width - 21)
        peer.refresh(layout=True)
        worker.refresh(layout=True)

    def on_resize(self, event):
        if self.is_mounted and self.query_one("#peer-table", DataTable).columns:
            self.adapt_columns(event.size.width)


class SystemSummary(Vertical):
    """Fixed tables reuse MiOS Monitor's existing hardware/service collectors."""
    DEFAULT_CSS = """
    SystemSummary {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        overflow-x: hidden;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-background: transparent;
        scrollbar-color: #1A407F;
        background: transparent;
    }
    ScrollBar {
        width: 1;
        min-width: 1;
        background: transparent;
    }
    ScrollBar.-horizontal {
        height: 0;
        min-height: 0;
        display: none;
    }
    ScrollBarCorner {
        background: transparent;
    }
    ScrollBarThumb {
        background: #1A407F;
        color: #1A407F;
    }
    ScrollBarThumb:hover {
        background: #F35C15;
    }
    DataTable {
        height: auto;
        scrollbar-size: 0 0;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 0;
        overflow-x: hidden;
        overflow-y: hidden;
        background: transparent;
    }
    DataTable > .datatable--header { background: #1A407F; color: #E7DFD3; text-style: bold; }
    DataTable > .datatable--odd-row { background: transparent; }
    DataTable > .datatable--even-row { background: transparent; }
    DataTable > .datatable--cursor { background: #1A407F; color: #E7DFD3; }
    #metric-table { height: auto; min-height: 2; }
    #summary-services { height: auto; min-height: 2; }
    #system-status { height: 1; color: #F35C15; }
    """

    def __init__(self, telemetry, services, **kwargs):
        super().__init__(**kwargs)
        self.telemetry, self.services = telemetry, services
        self.refreshing = False

    def compose(self):
        yield DataTable(id="metric-table", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield DataTable(id="summary-services", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield Static("", id="system-status", markup=False)

    def on_mount(self):
        self.query_one("#metric-table", DataTable).add_columns("Resource", "Use")
        self.query_one("#summary-services", DataTable).add_columns("Service", "State")
        self.set_interval(3, self.refresh_snapshot)
        self.call_after_refresh(self.refresh_snapshot)

    async def refresh_snapshot(self):
        if self.refreshing or not self.is_on_screen:
            return
        self.refreshing = True
        try:
            cpu, ram, disk, _, load = await asyncio.to_thread(self.telemetry)
            sync_rows(self.query_one("#metric-table", DataTable), [
                ("cpu", ("CPU", f"{cpu:.0f}%")), ("ram", ("Memory", f"{ram:.0f}%")),
                ("disk", ("Disk", f"{disk:.0f}%")), ("load", ("Load", load))])
            services = await asyncio.to_thread(self.services)
            sync_rows(self.query_one("#summary-services", DataTable), [(name, (name, "Up" if up else "Down"))
                       for name, _, up in services])
            self.query_one("#system-status", Static).update("")
        except Exception as exc:
            self.query_one("#system-status", Static).update(f"Collector: {clean(exc, 60)}")
        finally:
            self.refreshing = False
