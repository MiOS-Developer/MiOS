#!/usr/bin/env python3
# AI-hint: GTK 3 fixture for tests/test-appmenu-global.py: a native Wayland Gtk.Window with a plain GtkMenuBar WIDGET (no
# GMenuModel, so nothing on org.gtk.Menus; unity-gtk-module only works on X11) -- the shape of GTK 3 apps such as
# virt-manager. Its menus reach the top bar only through AT-SPI (the accessible menu bar). Items: File (Open with the
# Ctrl+O accelerator, a Recent submenu, a disabled item), View (a check item, a radio group). Activating an item appends
# "<label>[ <active>]" to the marker file. --no-menubar maps the same window without one (negative control).
# AI-related: src/mios-rs/miosd/src/appmenu.rs (accessibility source), tests/test-appmenu-global.py
# AI-functions: main
"""Usage: gtk3_menubar.py APP_ID MARKER_FILE [--no-menubar]"""
import sys

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import GLib, Gtk  # noqa: E402


def main():
    app_id, marker = sys.argv[1], sys.argv[2]
    GLib.set_prgname(app_id)  # GTK 3 uses the program name as the Wayland app_id
    win = Gtk.Window(title='GTK 3 menubar fixture')
    win.set_default_size(520, 340)
    win.connect('destroy', Gtk.main_quit)
    accel = Gtk.AccelGroup()
    win.add_accel_group(accel)

    def mark(line):
        with open(marker, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    def item(menu, label, cb=None, key=None, sensitive=True):
        it = Gtk.MenuItem.new_with_mnemonic(label)
        if key:
            k, mods = Gtk.accelerator_parse(key)
            it.add_accelerator('activate', accel, k, mods, Gtk.AccelFlags.VISIBLE)
        if cb:
            it.connect('activate', cb)
        it.set_sensitive(sensitive)
        menu.append(it)
        return it

    def top(bar, label):
        m = Gtk.Menu()
        it = Gtk.MenuItem.new_with_mnemonic(label)
        it.set_submenu(m)
        bar.append(it)
        return m

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    if '--no-menubar' not in sys.argv[3:]:
        bar = Gtk.MenuBar()
        f = top(bar, '_File')
        item(f, '_Open…', lambda *_: mark('Open…'), '<Control>o')
        recent = Gtk.Menu()
        r = Gtk.MenuItem.new_with_mnemonic('_Recent')
        r.set_submenu(recent)
        f.append(r)
        item(recent, 'one.txt', lambda *_: mark('one.txt'))
        f.append(Gtk.SeparatorMenuItem())
        item(f, 'Disabled Item', sensitive=False)
        v = top(bar, '_View')
        grid = Gtk.CheckMenuItem.new_with_mnemonic('Show _Grid')
        grid.set_active(True)
        k, mods = Gtk.accelerator_parse('<Control>g')
        grid.add_accelerator('activate', accel, k, mods, Gtk.AccelFlags.VISIBLE)
        grid.connect('toggled', lambda w: mark('Show Grid ' + ('on' if w.get_active() else 'off')))
        v.append(grid)
        v.append(Gtk.SeparatorMenuItem())
        group = None
        for label in ('Zoom 100%', 'Zoom 200%'):
            rb = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = group or rb
            rb.set_active(label == 'Zoom 100%')
            rb.connect('toggled', lambda w, l=label: w.get_active() and mark(l))
            v.append(rb)
        box.pack_start(bar, False, False, 0)
    box.pack_start(Gtk.Label(label='GTK 3 menubar fixture'), True, True, 0)
    win.add(box)
    win.show_all()
    Gtk.main()


if __name__ == '__main__':
    main()
