// AI-hint: Electron/Chromium-like global-menu fixture for tests/test-appmenu-global.py: ONE process that maps a
// Wayland xdg_toplevel and, exactly like Chromium/Electron's ozone-wayland DbusAppmenu, exports its menubar as
// com.canonical.dbusmenu at /com/canonical/menu/<hex> ONLY when the compositor offers org_kde_kwin_appmenu_manager,
// then announces it per surface with set_address(unique bus name, path). Without the global it exports nothing (the
// negative control for a compositor without the MiOS mios-appmenu plugin). The menu carries every item kind the pills
// render: submenu, separator, disabled item, check item, radio group and shortcuts. Every dbusmenu "clicked" event is
// appended to the marker file as "<id> <label>" (check/radio items flip their state first and emit LayoutUpdated).
// Build: wayland-scanner client-header/private-code for xdg-shell.xml and appmenu.xml, then
//   cc kde_dbusmenu.c xdg-shell.c appmenu.c $(pkg-config --cflags --libs gio-2.0 gio-unix-2.0 wayland-client)
// AI-related: usr/share/mios/hyprland/plugins/mios-appmenu/appmenu.xml, src/mios-rs/miosd/src/appmenu.rs, tests/test-appmenu-global.py
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <gio/gio.h>
#include <glib-unix.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client.h>

#include "appmenu-client-protocol.h"
#include "xdg-shell-client-protocol.h"

typedef struct {
    int         id, parent;
    const char *label, *type, *toggle; // type: "" | "separator"; toggle: "" | "checkmark" | "radio"
    int         state, enabled, group;  // group: radio group id (0 = none)
    const char *mods, *key;             // shortcut: "Control+Shift" style modifier list and key ("" = none)
} Item;

static Item MENU[] = {
    {1, 0, "_File", "", "", 0, 1, 0, "", ""},
    {2, 1, "_Open…", "", "", 0, 1, 0, "Control", "o"},
    {3, 1, "New _Window", "", "", 0, 1, 0, "Control+Shift", "n"},
    {4, 1, "Encoding", "", "", 0, 1, 0, "", ""},
    {5, 4, "UTF-8", "", "radio", 1, 1, 2, "", ""},
    {6, 4, "Latin-1", "", "radio", 0, 1, 2, "", ""},
    {7, 1, "", "separator", "", 0, 1, 0, "", ""},
    {8, 1, "Disabled Item", "", "", 0, 0, 0, "", ""},
    {9, 0, "_View", "", "", 0, 1, 0, "", ""},
    {10, 9, "Full Screen", "", "checkmark", 0, 1, 0, "", "F11"},
    {11, 9, "", "separator", "", 0, 1, 0, "", ""},
    {12, 9, "Zoom 100%", "", "radio", 1, 1, 1, "Control", "0"},
    {13, 9, "Zoom 200%", "", "radio", 0, 1, 1, "Control", "plus"},
};
#define N_MENU (sizeof(MENU) / sizeof(MENU[0]))

static const char *g_marker;
static guint32     g_revision = 1;
static GDBusConnection *g_bus;
static char        g_path[64];

static Item *find(int id) {
    for (size_t i = 0; i < N_MENU; ++i)
        if (MENU[i].id == id)
            return &MENU[i];
    return NULL;
}

static int has_children(int id) {
    for (size_t i = 0; i < N_MENU; ++i)
        if (MENU[i].parent == id)
            return 1;
    return 0;
}

static GVariant *props(const Item *it) {
    GVariantBuilder b;
    g_variant_builder_init(&b, G_VARIANT_TYPE("a{sv}"));
    if (!it)
        return g_variant_builder_end(&b);
    if (*it->type) {
        g_variant_builder_add(&b, "{sv}", "type", g_variant_new_string(it->type));
        return g_variant_builder_end(&b);
    }
    g_variant_builder_add(&b, "{sv}", "label", g_variant_new_string(it->label));
    g_variant_builder_add(&b, "{sv}", "enabled", g_variant_new_boolean(it->enabled));
    g_variant_builder_add(&b, "{sv}", "visible", g_variant_new_boolean(TRUE));
    if (has_children(it->id))
        g_variant_builder_add(&b, "{sv}", "children-display", g_variant_new_string("submenu"));
    if (*it->toggle) {
        g_variant_builder_add(&b, "{sv}", "toggle-type", g_variant_new_string(it->toggle));
        g_variant_builder_add(&b, "{sv}", "toggle-state", g_variant_new_int32(it->state));
    }
    if (*it->key) {
        GVariantBuilder chord, chords;
        g_variant_builder_init(&chords, G_VARIANT_TYPE("aas"));
        g_variant_builder_init(&chord, G_VARIANT_TYPE("as"));
        char **mods = g_strsplit(it->mods, "+", -1);
        for (char **m = mods; *m; ++m)
            if (**m)
                g_variant_builder_add(&chord, "s", *m);
        g_strfreev(mods);
        g_variant_builder_add(&chord, "s", it->key);
        g_variant_builder_add_value(&chords, g_variant_builder_end(&chord));
        g_variant_builder_add(&b, "{sv}", "shortcut", g_variant_builder_end(&chords));
    }
    return g_variant_builder_end(&b);
}

static GVariant *layout(int id, int depth) {
    GVariantBuilder kids;
    g_variant_builder_init(&kids, G_VARIANT_TYPE("av"));
    if (depth != 0)
        for (size_t i = 0; i < N_MENU; ++i)
            if (MENU[i].parent == id)
                g_variant_builder_add(&kids, "v", layout(MENU[i].id, depth < 0 ? -1 : depth - 1));
    return g_variant_new("(i@a{sv}av)", id, props(find(id)), &kids);
}

static void mark(const Item *it) {
    FILE *f = fopen(g_marker, "a");
    if (!f)
        return;
    fprintf(f, "%d %s\n", it->id, it->label);
    fclose(f);
}

static void clicked(Item *it) {
    if (!strcmp(it->toggle, "checkmark"))
        it->state = !it->state;
    else if (!strcmp(it->toggle, "radio"))
        for (size_t i = 0; i < N_MENU; ++i)
            if (MENU[i].group == it->group)
                MENU[i].state = &MENU[i] == it;
    mark(it);
    if (*it->toggle)
        g_dbus_connection_emit_signal(g_bus, NULL, g_path, "com.canonical.dbusmenu", "LayoutUpdated", g_variant_new("(ui)", ++g_revision, 0), NULL);
}

static void method(GDBusConnection *c, const char *sender, const char *path, const char *iface, const char *name, GVariant *args, GDBusMethodInvocation *inv,
                   gpointer data) {
    (void)c, (void)sender, (void)path, (void)iface, (void)data;
    if (!strcmp(name, "GetLayout")) {
        gint32 parent, depth;
        g_variant_get(args, "(ii^a&s)", &parent, &depth, NULL);
        g_dbus_method_invocation_return_value(inv, g_variant_new("(u@(ia{sv}av))", g_revision, layout(parent, depth)));
    } else if (!strcmp(name, "GetGroupProperties")) {
        GVariantIter *ids;
        gint32        id;
        GVariantBuilder out;
        g_variant_builder_init(&out, G_VARIANT_TYPE("a(ia{sv})"));
        g_variant_get(args, "(aias)", &ids, NULL);
        while (g_variant_iter_next(ids, "i", &id))
            g_variant_builder_add(&out, "(i@a{sv})", id, props(find(id)));
        g_variant_iter_free(ids);
        g_dbus_method_invocation_return_value(inv, g_variant_new("(a(ia{sv}))", &out));
    } else if (!strcmp(name, "GetProperty")) {
        gint32      id;
        const char *prop;
        g_variant_get(args, "(i&s)", &id, &prop);
        GVariant *all = props(find(id));
        GVariant *v   = g_variant_lookup_value(all, prop, NULL);
        g_variant_unref(g_variant_ref_sink(all));
        if (v)
            g_dbus_method_invocation_return_value(inv, g_variant_new("(v)", v));
        else
            g_dbus_method_invocation_return_dbus_error(inv, "com.canonical.dbusmenu.Error", "no such property");
    } else if (!strcmp(name, "Event")) {
        gint32      id;
        const char *event;
        g_variant_get(args, "(i&svu)", &id, &event, NULL, NULL);
        Item *it = find(id);
        if (it && !strcmp(event, "clicked") && it->enabled && !*it->type && !has_children(id))
            clicked(it);
        g_dbus_method_invocation_return_value(inv, NULL);
    } else if (!strcmp(name, "EventGroup")) {
        g_dbus_method_invocation_return_value(inv, g_variant_new("(ai)", NULL));
    } else if (!strcmp(name, "AboutToShow")) {
        g_dbus_method_invocation_return_value(inv, g_variant_new("(b)", FALSE));
    } else if (!strcmp(name, "AboutToShowGroup")) {
        g_dbus_method_invocation_return_value(inv, g_variant_new("(aiai)", NULL, NULL));
    } else
        g_dbus_method_invocation_return_dbus_error(inv, "org.freedesktop.DBus.Error.UnknownMethod", name);
}

static GVariant *getprop(GDBusConnection *c, const char *s, const char *p, const char *i, const char *name, GError **e, gpointer d) {
    (void)c, (void)s, (void)p, (void)i, (void)e, (void)d;
    if (!strcmp(name, "Version"))
        return g_variant_new_uint32(3);
    if (!strcmp(name, "TextDirection"))
        return g_variant_new_string("ltr");
    if (!strcmp(name, "Status"))
        return g_variant_new_string("normal");
    return g_variant_new_strv(NULL, 0);
}

static const char XML[] = "<node><interface name='com.canonical.dbusmenu'>"
                          "<property name='Version' type='u' access='read'/><property name='TextDirection' type='s' access='read'/>"
                          "<property name='Status' type='s' access='read'/><property name='IconThemePath' type='as' access='read'/>"
                          "<method name='GetLayout'><arg type='i' direction='in'/><arg type='i' direction='in'/><arg type='as' direction='in'/>"
                          "<arg type='u' direction='out'/><arg type='(ia{sv}av)' direction='out'/></method>"
                          "<method name='GetGroupProperties'><arg type='ai' direction='in'/><arg type='as' direction='in'/><arg type='a(ia{sv})' direction='out'/></method>"
                          "<method name='GetProperty'><arg type='i' direction='in'/><arg type='s' direction='in'/><arg type='v' direction='out'/></method>"
                          "<method name='Event'><arg type='i' direction='in'/><arg type='s' direction='in'/><arg type='v' direction='in'/><arg type='u' direction='in'/></method>"
                          "<method name='EventGroup'><arg type='a(isvu)' direction='in'/><arg type='ai' direction='out'/></method>"
                          "<method name='AboutToShow'><arg type='i' direction='in'/><arg type='b' direction='out'/></method>"
                          "<method name='AboutToShowGroup'><arg type='ai' direction='in'/><arg type='ai' direction='out'/><arg type='ai' direction='out'/></method>"
                          "<signal name='LayoutUpdated'><arg type='u'/><arg type='i'/></signal>"
                          "<signal name='ItemsPropertiesUpdated'><arg type='a(ia{sv})'/><arg type='a(ias)'/></signal>"
                          "</interface></node>";

// ---- Wayland ----------------------------------------------------------------------------------------------------------
static struct wl_compositor                *g_compositor;
static struct wl_shm                       *g_shm;
static struct xdg_wm_base                  *g_wm;
static struct org_kde_kwin_appmenu_manager *g_appmenu_manager;
static struct wl_surface                   *g_surface;
static struct xdg_surface                  *g_xdg_surface;
static struct wl_buffer                    *g_buffer;
static int                                  g_configured;
static int                                  g_running = 1;
static struct wl_display                   *g_dpy;

static void global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t version) {
    (void)d;
    if (!strcmp(iface, wl_compositor_interface.name))
        g_compositor = wl_registry_bind(r, name, &wl_compositor_interface, 4);
    else if (!strcmp(iface, wl_shm_interface.name))
        g_shm = wl_registry_bind(r, name, &wl_shm_interface, 1);
    else if (!strcmp(iface, xdg_wm_base_interface.name))
        g_wm = wl_registry_bind(r, name, &xdg_wm_base_interface, 1);
    else if (!strcmp(iface, org_kde_kwin_appmenu_manager_interface.name))
        g_appmenu_manager = wl_registry_bind(r, name, &org_kde_kwin_appmenu_manager_interface, version < 2 ? version : 2);
}
static void global_remove(void *d, struct wl_registry *r, uint32_t name) {
    (void)d, (void)r, (void)name;
}
static const struct wl_registry_listener REGISTRY = {global, global_remove};

static void ping(void *d, struct xdg_wm_base *wm, uint32_t serial) {
    (void)d;
    xdg_wm_base_pong(wm, serial);
}
static const struct xdg_wm_base_listener WM = {ping};

static struct wl_buffer *solid(int w, int h) {
    int   stride = w * 4, size = stride * h;
    int   fd     = memfd_create("kde-dbusmenu-fixture", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, size) < 0)
        return NULL;
    uint32_t *px = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (px == MAP_FAILED)
        return NULL;
    for (int i = 0; i < w * h; ++i)
        px[i] = 0xff2a3a4a;
    munmap(px, size);
    struct wl_shm_pool *pool = wl_shm_create_pool(g_shm, fd, size);
    struct wl_buffer   *b    = wl_shm_pool_create_buffer(pool, 0, w, h, stride, WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    close(fd);
    return b;
}

static void surface_configure(void *d, struct xdg_surface *s, uint32_t serial) {
    (void)d;
    xdg_surface_ack_configure(s, serial);
    if (!g_buffer)
        g_buffer = solid(480, 320);
    wl_surface_attach(g_surface, g_buffer, 0, 0);
    wl_surface_damage(g_surface, 0, 0, 480, 320);
    wl_surface_commit(g_surface);
    g_configured = 1;
}
static const struct xdg_surface_listener XDG_SURFACE = {surface_configure};

static void toplevel_configure(void *d, struct xdg_toplevel *t, int32_t w, int32_t h, struct wl_array *s) {
    (void)d, (void)t, (void)w, (void)h, (void)s;
}
static void toplevel_close(void *d, struct xdg_toplevel *t) {
    (void)d, (void)t;
    g_running = 0;
}
static const struct xdg_toplevel_listener TOPLEVEL = {toplevel_configure, toplevel_close};

static gboolean on_wayland(gint fd, GIOCondition cond, gpointer loop) {
    (void)fd;
    if ((cond & (G_IO_ERR | G_IO_HUP)) || wl_display_dispatch(g_dpy) < 0 || !g_running) {
        g_main_loop_quit(loop);
        return G_SOURCE_REMOVE;
    }
    wl_display_flush(g_dpy);
    return G_SOURCE_CONTINUE;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s APP_ID MARKER_FILE\n", argv[0]);
        return 2;
    }
    g_marker               = argv[2];
    struct wl_display *dpy = g_dpy = wl_display_connect(NULL);
    if (!dpy) {
        fprintf(stderr, "no wayland display\n");
        return 1;
    }
    struct wl_registry *reg = wl_display_get_registry(dpy);
    wl_registry_add_listener(reg, &REGISTRY, NULL);
    wl_display_roundtrip(dpy);
    if (!g_compositor || !g_shm || !g_wm) {
        fprintf(stderr, "compositor lacks wl_compositor/wl_shm/xdg_wm_base\n");
        return 1;
    }
    xdg_wm_base_add_listener(g_wm, &WM, NULL);
    g_surface                    = wl_compositor_create_surface(g_compositor);
    g_xdg_surface                = xdg_wm_base_get_xdg_surface(g_wm, g_surface);
    struct xdg_toplevel *top     = xdg_surface_get_toplevel(g_xdg_surface);
    xdg_surface_add_listener(g_xdg_surface, &XDG_SURFACE, NULL);
    xdg_toplevel_add_listener(top, &TOPLEVEL, NULL);
    xdg_toplevel_set_app_id(top, argv[1]);
    xdg_toplevel_set_title(top, "kde dbusmenu fixture");
    wl_surface_commit(g_surface);

    GError *err = NULL;
    g_bus       = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, &err);
    if (!g_bus) {
        fprintf(stderr, "session bus: %s\n", err->message);
        return 1;
    }
    if (g_appmenu_manager) {
        // Chromium's shape: one menubar per toplevel at /com/canonical/menu/<hex window id>.
        snprintf(g_path, sizeof g_path, "/com/canonical/menu/%X", 0x4C0000Au);
        GDBusNodeInfo           *node   = g_dbus_node_info_new_for_xml(XML, NULL);
        static const GDBusInterfaceVTable VT = {method, getprop, NULL, {0}};
        if (!g_dbus_connection_register_object(g_bus, g_path, node->interfaces[0], &VT, NULL, NULL, &err)) {
            fprintf(stderr, "register: %s\n", err->message);
            return 1;
        }
        struct org_kde_kwin_appmenu *menu = org_kde_kwin_appmenu_manager_create(g_appmenu_manager, g_surface);
        org_kde_kwin_appmenu_set_address(menu, g_dbus_connection_get_unique_name(g_bus), g_path);
        printf("exported %s%s through org_kde_kwin_appmenu\n", g_dbus_connection_get_unique_name(g_bus), g_path);
    } else
        printf("no org_kde_kwin_appmenu_manager: no global menu exported\n");
    fflush(stdout);
    wl_display_flush(dpy);

    wl_display_roundtrip(dpy); // the first configure, before the loop takes over the fd
    wl_display_flush(dpy);
    GMainLoop *loop = g_main_loop_new(NULL, FALSE);
    g_unix_fd_add(wl_display_get_fd(dpy), G_IO_IN | G_IO_ERR | G_IO_HUP, on_wayland, loop);
    g_main_loop_run(loop);
    return 0;
}
