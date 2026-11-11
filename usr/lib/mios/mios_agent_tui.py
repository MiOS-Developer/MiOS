# AI-hint: Shared fixed-table widgets for the unified MiOS Monitor; presentation never consumes or acknowledges relay messages.
# AI-related: /usr/libexec/mios/mios-mon.py, /usr/libexec/mios/mios-mcp-server, /usr/lib/mios/agent-pipe/test_mios_agent_tui.py
# AI-functions: clean, peer_name, sync_rows, set_table_header, ClientView, AgentView, SystemSummary

import asyncio
import re
import unicodedata

from mios_toml import colors, load_merged
from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.geometry import Size
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.message import Message
from textual.widgets import Button, DataTable, Input, Static


def _theme_css(css):
    """Resolve each widget color from the same layered SSOT as the parent app."""
    palette = colors(data=load_merged())
    return re.sub(r"@MIOS:([a-z]+)@", lambda match: palette[match[1]], css)


def clean(value, limit=100):
    """Render peer metadata literally, excluding terminal and bidi controls."""
    return "".join(c for c in str(value) if not unicodedata.category(c).startswith("C"))[:limit]


def peer_name(row):
    kind = clean(row.get("kind", "agent"), 12).capitalize()
    label = clean(row.get("label", ""))
    if "·" in label:
        return f"{kind} {label.split()[-1]}"
    return label or kind


def sync_rows(table, rows, *, ordered=False):
    """Refresh keyed cells, keeping selection and the visible row independently."""
    previous = table.ordered_rows
    old_keys = [row.key.value for row in previous]
    selected_index = min(table.cursor_row, max(0, len(old_keys) - 1))
    selected = old_keys[selected_index] if old_keys else None
    x, y = table.scroll_x, table.scroll_y
    top_index, intra_row = 0, y
    for index, row in enumerate(previous):
        if intra_row < row.height:
            top_index = index
            break
        intra_row -= row.height
    else:
        top_index, intra_row = max(0, len(previous) - 1), 0

    wanted = {key for key, _ in rows}
    desired_order = [key for key, _ in rows]
    cursor_column = table.cursor_column
    # Textual's cursor watcher scrolls to the selected row even when
    # move_cursor(scroll=False) is used. A refresh must retain an offscreen
    # selection without manufacturing a deferred scroll-to-cursor operation.
    table.set_reactive(DataTable.cursor_coordinate, Coordinate(0, cursor_column))
    for key in list(table.rows):
        if key.value not in wanted:
            table.remove_row(key)
    columns = list(table.columns)
    for key, cells in rows:
        values = [Text(clean(cell), style=cell.style) if isinstance(cell, Text)
                  else Text(clean(cell)) for cell in cells]
        if key in table.rows:
            for column, value in zip(columns, values):
                if table.get_cell(key, column) != value:
                    table.update_cell(key, column, value)
        else:
            table.add_row(*values, key=key)

    if ordered and [row.key.value for row in table.ordered_rows] != desired_order:
        # Sort the existing keyed rows without clearing geometry or data. Each
        # first cell is a distinct Text object, including duplicate labels.
        first = columns[0]
        rank = {id(table.get_cell(key, first)): index for index, key in enumerate(desired_order)}
        table.sort(first, key=lambda cell: rank[id(cell)])

    def survivor(index):
        if not old_keys:
            return None
        for key in old_keys[index:] + list(reversed(old_keys[:index])):
            if key in wanted:
                return key
        return None

    selected = selected if selected in wanted else survivor(selected_index)
    anchor = survivor(top_index)
    if selected is not None:
        table.set_reactive(DataTable.cursor_coordinate,
                           Coordinate(table.get_row_index(selected), cursor_column))
    table.refresh()
    if anchor is not None:
        current = table.ordered_rows
        anchor_index = table.get_row_index(anchor)
        # A removed top row falls forward to its next retained neighbour.
        residual = intra_row if old_keys[top_index] == anchor else 0
        y = sum(row.height for row in current[:anchor_index]) + min(residual, max(0, current[anchor_index].height - 1))
    table.scroll_to(x=x, y=y, animate=False, force=True, immediate=True)
    immediate = (table.scroll_x, table.scroll_y, table.cursor_coordinate)

    def restore_after_layout():
        # Dimensions may still describe the previous row set. Do not override
        # input that moved the cursor or viewport before this layout completed.
        if (table.scroll_x, table.scroll_y, table.cursor_coordinate) == immediate:
            table.scroll_to(x=x, y=y, animate=False, force=True, immediate=True)

    table.call_after_refresh(restore_after_layout)


def set_table_header(table, show):
    """Change responsive chrome without Textual scrolling to an offscreen cursor."""
    if table.show_header == show:
        return
    width, height = table.virtual_size
    delta = table.header_height if show else -table.header_height
    # DataTable.watch_show_header also scrolls the cursor into view. Keep its
    # geometry/cache updates, while presentation-only changes leave the reader's
    # independent viewport intact. No deferred restore can overwrite wheel input.
    table.set_reactive(DataTable.show_header, show)
    table.virtual_size = Size(width, max(0, height + delta))
    table._clear_caches()
    table.refresh(layout=True)


class ClientView(Vertical):
    """The same chooser is embedded in a head pane and in MiOS Monitor."""
    class Selected(Message):
        def __init__(self, name):
            super().__init__()
            self.name = name

    DEFAULT_CSS = _theme_css("""
    ClientView {
        height: 1fr;
        padding: 0 1;
        overflow-y: hidden;
        overflow-x: hidden;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-background: transparent;
        scrollbar-color: @MIOS:accent@;
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
        background: @MIOS:accent@;
        color: @MIOS:accent@;
    }
    ScrollBarThumb:hover {
        background: @MIOS:warning@;
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
    DataTable > .datatable--header { background: @MIOS:accent@; color: @MIOS:fg@; text-style: bold; }
    DataTable > .datatable--odd-row { background: transparent; }
    DataTable > .datatable--even-row { background: transparent; }
    DataTable > .datatable--cursor { background: @MIOS:accent@; color: @MIOS:fg@; }
    #client-intro { height: 2; }
    #client-table { height: 1fr; min-height: 0; overflow-y: auto; }
    #client-input, #client-status { height: 1; }
    #client-input { border: none; padding: 0; background: transparent; }
    #client-status { color: @MIOS:warning@; }
    """)

    def __init__(self, agents, **kwargs):
        super().__init__(**kwargs)
        self.agents = [row for row in agents if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", str(row.get("name", "")))]

    def compose(self):
        yield Static("Choose a head CLI.\nMCP connects visible workers.", id="client-intro", markup=False)
        yield DataTable(id="client-table", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield Input(placeholder="Client number / name", id="client-input")
        yield Static("Arrows + Enter, or type a client", id="client-status", markup=False)

    def on_resize(self, event):
        # The list scrolls independently; input stays inside the pane even
        # while a remote keyboard reduces the usable viewport.
        self.query_one("#client-intro").display = self.size.height >= 5
        self.query_one("#client-status").display = self.size.height >= 2

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


class InventoryDetails(ModalScreen):
    """Full selected identities and parent/source metadata at any viewport size."""
    BINDINGS = [Binding("escape", "close", "Close", priority=True)]
    DEFAULT_CSS = _theme_css("""
    InventoryDetails { align: center middle; background: transparent; }
    InventoryDetails > Vertical { width: 90%; height: 80%; border: solid @MIOS:accent@; background: @MIOS:bg@; }
    InventoryDetails VerticalScroll { height: 1fr; padding: 1; }
    InventoryDetails Static { height: auto; }
    InventoryDetails Button { height: 1; min-height: 1; border: none; }
    """)
    def __init__(self, detail):
        super().__init__()
        self.detail = detail

    def compose(self):
        with Vertical():
            with VerticalScroll():
                yield Static(clean(self.detail, 4000).replace(" · ", "\n"), markup=False)
            yield Button("Close (Esc)", id="close-inventory-detail")

    def action_close(self):
        self.dismiss()

    @on(Button.Pressed, "#close-inventory-detail")
    def close_pressed(self, event):
        self.dismiss()


class AgentView(Vertical):
    """Search every reported registration, session and child without row caps."""
    DEFAULT_CSS = _theme_css("""
    AgentView { height: 1fr; padding: 0 1; overflow: hidden; background: transparent; }
    AgentView #relay-count, AgentView #worker-count, AgentView #receipt,
    AgentView #agent-error { height: 1; }
    AgentView #inventory-scopes { height: 1; }
    AgentView Button { height: 1; min-width: 1; width: 1fr; border: none; padding: 0; background: transparent;
        text-wrap: nowrap; text-overflow: clip; }
    AgentView Button.scope-selected { background: @MIOS:accent@; color: @MIOS:fg@; }
    AgentView #agent-search { height: 1; border: none; padding: 0; background: transparent; }
    AgentView #peer-table { height: 1fr; min-height: 1; overflow-y: auto; overflow-x: hidden;
        scrollbar-size-vertical: 1; scrollbar-size-horizontal: 0; background: transparent; }
    AgentView DataTable > .datatable--header { background: @MIOS:accent@; color: @MIOS:fg@; text-style: bold; }
    AgentView DataTable > .datatable--cursor { background: @MIOS:accent@; color: @MIOS:fg@; }
    AgentView #agent-detail { height: 2; color: @MIOS:muted@; overflow: hidden; }
    AgentView #receipt { color: @MIOS:muted@; }
    AgentView #agent-error { color: @MIOS:warning@; }
    """)
    BINDINGS = [("ctrl+f", "search", "Search"), ("f6", "next_scope", "Inventory scope")]

    def __init__(self, observer, refresh_s=2, **kwargs):
        super().__init__(**kwargs)
        self.observer, self.refresh_s = observer, refresh_s
        self.refreshing = False
        self.snapshot = {}
        self.scope = "all"
        self.inventory = {}

    def compose(self):
        yield Static("Relay: loading", id="relay-count", markup=False)
        yield Static("Sessions and subagents: loading", id="worker-count", markup=False)
        with Horizontal(id="inventory-scopes"):
            for scope in ("all", "agents", "sessions", "subagents"):
                yield Button(scope.capitalize(), id="scope-" + scope, classes="scope-selected" if scope == "all" else "")
        yield Input(placeholder="Search name, ID, source or parent", id="agent-search")
        yield DataTable(id="peer-table", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield Static("Arrows / PgUp / PgDn / Home / End · F6 scope", id="agent-detail", markup=False)
        yield Static("Receipts: read does not mean done", id="receipt", markup=False)
        yield Static("", id="agent-error", markup=False)

    def on_mount(self):
        table = self.query_one("#peer-table", DataTable)
        for title, width in (("Agent / session", 18), ("State", 8), ("Type", 8)):
            table.add_column(title, width=width)
        self.set_interval(self.refresh_s, self.schedule_refresh)
        self.call_after_refresh(self.schedule_refresh)

    def schedule_refresh(self):
        if not self.refreshing:
            self.run_worker(self.refresh_snapshot(), group="snapshot", exit_on_error=False)

    async def refresh_snapshot(self):
        if self.refreshing:
            return
        self.refreshing = True
        try:
            self.render_snapshot(await asyncio.to_thread(self.observer))
        except Exception:
            # Never retain green live indicators when the source can no longer be read.
            stale = dict(self.snapshot)
            stale["stale"] = True
            self.render_snapshot(stale)
            self.query_one("#agent-error", Static).update("Relay unavailable · last snapshot; presence unknown")
        finally:
            self.refreshing = False

    def render_snapshot(self, snapshot):
        self.snapshot = snapshot
        agents = snapshot.get("agents", [])
        online = sum(row.get("online") is True and not row.get("stale") and not snapshot.get("stale") for row in agents)
        self.query_one("#relay-count", Static).update(f"Relay: {online} online / {len(agents)} registered")
        self.query_one("#worker-count", Static).update(
            f"Sessions: {len(snapshot.get('sessions', []))} · Subagents: {len(snapshot.get('subagents', []))} · Panes: {len(snapshot.get('panes', []))}")
        messages = snapshot.get("messages", [])
        queued = sum(row.get("status") == "queued" for row in messages)
        read = sum(row.get("status") == "received" for row in messages)
        self.query_one("#receipt", Static).update(f"Receipt window: {queued} queued / {read} read")
        sources = snapshot.get("sources", [])
        unavailable = sum(row.get("state") not in ("ready", "missing", "unsupported") for row in sources)
        errors = snapshot.get("errors", [])
        self.query_one("#agent-error", Static).update(
            f"Coverage partial · {unavailable} pending/unavailable sources · {len(errors)} errors" if unavailable or errors
            else f"Sources: {len(sources)} · F6 scope · Ctrl+F search")
        self.render_rows()
        self.adapt_columns(self.size.width)

    def render_rows(self):
        query = self.query_one("#agent-search", Input).value.casefold().strip()
        rows = {}
        snapshot = self.snapshot
        for category, key in (("agents", "agents"), ("sessions", "sessions"), ("sessions", "panes"), ("subagents", "subagents"), ("sources", "sources")):
            for row in snapshot.get(key, []):
                source = str(row.get("source", "local"))
                identity = row.get("agent_id") or row.get("session_id") or (row.get("source") if key == "sources" else None) or str(row.get("socket", "")) + str(row.get("pane", ""))
                row_key = source + ":" + ("agents" if row.get("agent_id") else "pane" if row.get("pane") else "session") + ":" + str(identity)
                parent = row.get("parent_agent_id") or row.get("parent_pane") or row.get("parent_pid") or "unknown"
                name = ("Source " + source if key == "sources" else "") or row.get("label") or row.get("session_name") or row.get("agent_kind") or row.get("command") or identity
                if row.get("pane"):
                    name = f"{row.get('role', 'Pane')} {row['pane']} {name}"
                stale = snapshot.get("stale") or row.get("stale")
                state = row.get("state", "Unknown").capitalize() if key == "sources" else "Unknown" if stale else ("Online" if row.get("online") else "Offline") if row.get("agent_id") else "Exited" if row.get("dead") else "Empty" if row.get("command") == "sleep" else "Detected"
                child = category == "subagents" or bool(row.get("parent_agent_id"))
                kind = "Source" if key == "sources" else "Subagent" if child else "Agent" if row.get("agent_id") else "Pane" if row.get("pane") else "Session"
                scopes = {category}
                if child: scopes.add("subagents")
                if row_key in rows: scopes |= rows[row_key]["scopes"]
                detail = f"{identity} · source: {source} · parent: {parent} · Q:{row.get('pending', 0)} · {row.get('evidence', 'registration' if row.get('agent_id') else 'detected')}"
                rows[row_key] = {"cells": (name, state, kind), "detail": detail, "scopes": scopes, "search": f"{name} {detail}".casefold()}
        self.inventory = rows
        wanted = [(key, row["cells"]) for key, row in rows.items()
                  if (self.scope == "all" or self.scope in row["scopes"]) and (not query or query in row["search"])]
        wanted.sort(key=lambda item: (item[1][1] != "Online", item[1][1] == "Offline", str(item[1][0]).casefold(), item[0]))
        table = self.query_one("#peer-table", DataTable)
        sync_rows(table, wanted, ordered=True)
        self.show_detail()

    @on(Button.Pressed)
    def select_scope(self, event):
        if event.button.id and event.button.id.startswith("scope-"):
            event.stop()
            self.set_scope(event.button.id.removeprefix("scope-"))

    def set_scope(self, scope):
        self.scope = scope
        for button in self.query("#inventory-scopes Button"):
            button.set_class(button.id == "scope-" + scope, "scope-selected")
        self.render_rows()
        self.query_one("#peer-table", DataTable).focus()

    def action_next_scope(self):
        scopes = ("all", "agents", "sessions", "subagents")
        self.set_scope(scopes[(scopes.index(self.scope) + 1) % len(scopes)])

    def action_search(self):
        search = self.query_one("#agent-search", Input)
        if getattr(self.app, "desktop_panel", False):
            search.display = True
        search.focus()

    @on(Input.Changed, "#agent-search")
    def search_changed(self, event):
        self.render_rows()

    @on(Input.Submitted, "#agent-search")
    def search_submitted(self, event):
        self.query_one("#peer-table", DataTable).focus()
        self.adapt_columns(self.size.width)

    @on(DataTable.RowHighlighted, "#peer-table")
    def highlighted(self, event):
        self.show_detail()

    @on(DataTable.RowSelected, "#peer-table")
    def inspect_row(self, event):
        event.stop()
        row = self.inventory.get(event.row_key.value)
        if row:
            self.app.push_screen(InventoryDetails(row["detail"]))

    def show_detail(self):
        table = self.query_one("#peer-table", DataTable)
        detail = "No matching rows · F6 scope · Ctrl+F search"
        if table.row_count and 0 <= table.cursor_row < table.row_count:
            key = table.ordered_rows[table.cursor_row].key.value
            detail = self.inventory.get(key, {}).get("detail", detail)
        self.query_one("#agent-detail", Static).update(clean(detail, 1000))

    def adapt_columns(self, width):
        table = self.query_one("#peer-table", DataTable)
        if table.columns:
            column = list(table.columns.values())[0]
            wanted = max(8, width - 25)
            if column.width != wanted:
                column.width = wanted
                table.refresh(layout=True)
        if getattr(self.app, "desktop_panel", False):
            height = self.size.height
            self.query_one("#relay-count").display = height >= 8
            self.query_one("#worker-count").display = height >= 10
            self.query_one("#inventory-scopes").display = height >= 3
            search = self.query_one("#agent-search", Input)
            search.display = height >= 4 or search.has_focus
            self.query_one("#agent-detail").display = height >= 14
            self.query_one("#receipt").display = height >= 16
            self.query_one("#agent-error").display = height >= 6
            set_table_header(table, height >= 5)
            return
        compact = self.size.height < 12
        self.query_one("#receipt").display = not compact
        self.query_one("#agent-detail").display = self.size.height >= 8

    def on_resize(self, event):
        if self.is_mounted:
            self.adapt_columns(event.size.width)


class SystemSummary(Vertical):
    """Fixed tables reuse MiOS Monitor's existing hardware/service collectors."""
    DEFAULT_CSS = _theme_css("""
    SystemSummary {
        height: 1fr;
        padding: 0 1;
        overflow-y: auto;
        overflow-x: hidden;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-background: transparent;
        scrollbar-color: @MIOS:accent@;
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
        background: @MIOS:accent@;
        color: @MIOS:accent@;
    }
    ScrollBarThumb:hover {
        background: @MIOS:warning@;
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
    DataTable > .datatable--header { background: @MIOS:accent@; color: @MIOS:fg@; text-style: bold; }
    DataTable > .datatable--odd-row { background: transparent; }
    DataTable > .datatable--even-row { background: transparent; }
    DataTable > .datatable--cursor { background: @MIOS:accent@; color: @MIOS:fg@; }
    #metric-table { height: auto; min-height: 2; }
    #summary-services { height: auto; min-height: 2; }
    #system-status { height: 1; color: @MIOS:warning@; }
    """)

    def __init__(self, telemetry, services, managed=False, **kwargs):
        super().__init__(**kwargs)
        self.telemetry, self.services = telemetry, services
        self.refreshing = False
        self.managed = managed

    def compose(self):
        yield DataTable(id="metric-table", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield DataTable(id="summary-services", cursor_type="row", zebra_stripes=True, show_row_labels=False)
        yield Static("", id="system-status", markup=False)

    def on_mount(self):
        self.query_one("#metric-table", DataTable).add_columns("Resource", "Use")
        self.query_one("#summary-services", DataTable).add_columns("Service", "State")
        if not self.managed:
            self.set_interval(3, self.schedule_refresh)
            self.call_after_refresh(self.schedule_refresh)

    def render_snapshot(self, telemetry, services):
        cpu, ram, disk, _, load = telemetry
        sync_rows(self.query_one("#metric-table", DataTable), [
            ("cpu", ("CPU", f"{cpu:.0f}%")), ("ram", ("Memory", f"{ram:.0f}%")),
            ("disk", ("Disk", f"{disk:.0f}%")), ("load", ("Load", load))])
        sync_rows(self.query_one("#summary-services", DataTable), [
            (name, (name, "Up" if up else "Down")) for name, _, up in services])
        for table in self.query(DataTable):
            # Textual 4 rejects CSS max-height:none; its inherited 100% cap
            # also clips rows inside this outer scroll view. A public numeric
            # content cap keeps every row available without viewport coupling.
            content_height = sum(row.height for row in table.ordered_rows)
            if table.show_header:
                content_height += table.header_height
            table.styles.max_height = max(2, content_height)
        self.query_one("#system-status", Static).update("")

    def schedule_refresh(self):
        if not self.refreshing:
            self.run_worker(self.refresh_snapshot(), group="snapshot", exit_on_error=False)

    async def refresh_snapshot(self):
        if self.refreshing or not self.is_on_screen:
            return
        self.refreshing = True
        try:
            telemetry = await asyncio.to_thread(self.telemetry)
            services = await asyncio.to_thread(self.services)
            self.render_snapshot(telemetry, services)
        except Exception as exc:
            self.query_one("#system-status", Static).update(f"Collector: {clean(exc, 60)}")
        finally:
            self.refreshing = False
