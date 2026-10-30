#!/usr/bin/env python3
# AI-hint: GTK 3 fixture application for tests/test-shell-appmenu-live.py: a GtkApplication that exports a real menubar
# (org.gtk.Menus + org.gtk.Actions under its application id, the contract miosd appmenu reads) or, with --no-menus, none.
# Activating "Mark" writes the marker file given on the command line, proving the menu action reached the application.
# AI-related: src/mios-rs/miosd/src/appmenu.rs, usr/share/mios/quickshell/AppMenu.qml, tests/test-shell-appmenu-live.py
# AI-functions: main
"""Usage: menu_app.py APP_ID MARKER_FILE [--no-menus]"""
import sys

import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gio, GLib, Gtk  # noqa: E402


def main():
    app_id, marker = sys.argv[1], sys.argv[2]
    menus = '--no-menus' not in sys.argv[3:]
    GLib.set_prgname(app_id)  # GTK 3 uses the program name as the Wayland app_id (the exporter matches it)
    app = Gtk.Application(application_id=app_id, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def on_mark(_action, _param):
        with open(marker, 'a', encoding='utf-8') as f:
            f.write('mark\n')

    def on_startup(a):
        mark = Gio.SimpleAction.new('mark', None)
        mark.connect('activate', on_mark)
        a.add_action(mark)
        disabled = Gio.SimpleAction.new('disabled', None)
        disabled.set_enabled(False)
        a.add_action(disabled)
        if menus:
            bar = Gio.Menu()
            file_menu = Gio.Menu()
            file_menu.append('_Mark', 'app.mark')
            file_menu.append('_Disabled', 'app.disabled')
            bar.append_submenu('_File', file_menu)
            view = Gio.Menu()
            view.append('_Mark again', 'app.mark')
            bar.append_submenu('_View', view)
            a.set_menubar(bar)

    def on_activate(a):
        w = Gtk.ApplicationWindow(application=a, title='MiOS app-menu fixture')
        w.set_default_size(480, 300)
        w.set_show_menubar(False)  # the shell's pills carry the menubar
        w.show_all()

    app.connect('startup', on_startup)
    app.connect('activate', on_activate)
    return app.run([sys.argv[0]])


if __name__ == '__main__':
    sys.exit(main())
