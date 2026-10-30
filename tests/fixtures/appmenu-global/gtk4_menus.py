#!/usr/bin/env python3
# AI-hint: GTK 4 fixture for tests/test-appmenu-global.py. Default: a GtkApplication menubar (GMenuModel exported on
# org.gtk.Menus, actions on org.gtk.Actions) with a STATEFUL boolean action (check item), a string-state radio action,
# accelerators (set_accels_for_action), a submenu and a disabled action. --hamburger: a libadwaita-style window with NO
# menubar and only a header GtkMenuButton (the GNOME app shape: Nautilus, Ptyxis, Showtime), which the top bar can reach
# only through AT-SPI as a menu-button pill. Activations append "<what>" to the marker file.
# AI-related: src/mios-rs/miosd/src/appmenu.rs (gtk + accessibility sources), tests/test-appmenu-global.py
# AI-functions: main
"""Usage: gtk4_menus.py APP_ID MARKER_FILE [--hamburger]"""
import sys

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gtk  # noqa: E402


def main():
    app_id, marker = sys.argv[1], sys.argv[2]
    hamburger = '--hamburger' in sys.argv[3:]
    app = Gtk.Application(application_id=app_id, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def mark(line):
        with open(marker, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    def menu_model():
        bar = Gio.Menu()
        f = Gio.Menu()
        open_item = Gio.MenuItem.new('_Open…', 'app.open')
        open_item.set_attribute_value('accel', GLib.Variant.new_string('<Control>o'))  # GTK 4 renders and exports it
        f.append_item(open_item)
        recent = Gio.Menu()
        recent.append('one.txt', 'app.recent::one.txt')
        f.append_submenu('_Recent', recent)
        sec = Gio.Menu()
        sec.append('Disabled Item', 'app.disabled')
        f.append_section(None, sec)
        bar.append_submenu('_File', f)
        v = Gio.Menu()
        v.append('Show _Grid', 'win.grid')
        zoom = Gio.Menu()
        zoom.append('Zoom 100%', 'win.zoom::100')
        zoom.append('Zoom 200%', 'win.zoom::200')
        v.append_section(None, zoom)
        bar.append_submenu('_View', v)
        return bar

    def on_startup(a):
        for name, cb in (('open', lambda *_: mark('Open…')), ('disabled', None)):
            act = Gio.SimpleAction.new(name, None)
            if cb:
                act.connect('activate', cb)
            act.set_enabled(cb is not None)
            a.add_action(act)
        recent = Gio.SimpleAction.new('recent', GLib.VariantType.new('s'))
        recent.connect('activate', lambda _a, p: mark(p.get_string()))
        a.add_action(recent)
        a.set_accels_for_action('app.open', ['<Control>o'])
        a.set_accels_for_action('win.grid', ['<Control>g'])
        if not hamburger:
            a.set_menubar(menu_model())

    def on_activate(a):
        win = Gtk.ApplicationWindow(application=a, title='GTK 4 fixture')
        win.set_default_size(520, 340)
        grid = Gio.SimpleAction.new_stateful('grid', None, GLib.Variant.new_boolean(True))

        def toggle(act, _p):
            act.set_state(GLib.Variant.new_boolean(not act.get_state().get_boolean()))
            mark('Show Grid ' + ('on' if act.get_state().get_boolean() else 'off'))
        grid.connect('activate', toggle)
        win.add_action(grid)
        zoom = Gio.SimpleAction.new_stateful('zoom', GLib.VariantType.new('s'), GLib.Variant.new_string('100'))

        def set_zoom(act, p):
            act.set_state(p)
            mark('Zoom ' + p.get_string() + '%')
        zoom.connect('activate', set_zoom)
        win.add_action(zoom)
        if hamburger:
            header = Gtk.HeaderBar()
            button = Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu_model())
            button.set_tooltip_text('Main Menu')
            button.update_property([Gtk.AccessibleProperty.LABEL], ['Main Menu'])
            header.pack_end(button)
            win.set_titlebar(header)
        else:
            win.set_show_menubar(False)  # the top bar shows it
        win.set_child(Gtk.Label(label='GTK 4 fixture'))
        win.present()

    app.connect('startup', on_startup)
    app.connect('activate', on_activate)
    app.run([sys.argv[0]])


if __name__ == '__main__':
    main()
