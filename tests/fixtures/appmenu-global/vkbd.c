/* Test fixture for tests/test-appmenu-global.py: REAL key presses on the seat of an owned nested Wayland compositor
   through zwp_virtual_keyboard_v1 (the keys go to whatever surface has keyboard focus there -- the app-menu bar while
   a dropdown is open). Never point it at the live session.
     vkbd <key>...   keys: up down left right enter escape space home end tab
   Build: wayland-scanner client-header/private-code virtual-keyboard-unstable-v1.xml, then
     cc vkbd.c vk-protocol.c $(pkg-config --cflags --libs wayland-client xkbcommon) */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include "vk-client-protocol.h"

static struct zwp_virtual_keyboard_manager_v1 *mgr;
static struct wl_seat *seat;

static void reg_global(void *d, struct wl_registry *r, uint32_t id, const char *iface, uint32_t v) {
    (void)d;
    if (!strcmp(iface, zwp_virtual_keyboard_manager_v1_interface.name))
        mgr = wl_registry_bind(r, id, &zwp_virtual_keyboard_manager_v1_interface, 1);
    else if (!strcmp(iface, wl_seat_interface.name) && !seat)
        seat = wl_registry_bind(r, id, &wl_seat_interface, v < 1 ? v : 1);
}
static void reg_remove(void *d, struct wl_registry *r, uint32_t id) { (void)d, (void)r, (void)id; }
static const struct wl_registry_listener rl = {reg_global, reg_remove};

static int code(const char *k) {
    static const struct { const char *n; int c; } map[] = {{"up", 103}, {"down", 108}, {"left", 105}, {"right", 106},
        {"enter", 28}, {"escape", 1}, {"space", 57}, {"home", 102}, {"end", 107}, {"tab", 15}};
    for (size_t i = 0; i < sizeof map / sizeof map[0]; ++i)
        if (!strcmp(map[i].n, k))
            return map[i].c;
    return -1;
}

static uint32_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint32_t)(t.tv_sec * 1000 + t.tv_nsec / 1000000);
}

int main(int argc, char **argv) {
    struct wl_display *dpy = wl_display_connect(NULL);
    if (!dpy)
        return fprintf(stderr, "no wayland display\n"), 1;
    struct wl_registry *reg = wl_display_get_registry(dpy);
    wl_registry_add_listener(reg, &rl, NULL);
    wl_display_roundtrip(dpy);
    if (!mgr || !seat)
        return fprintf(stderr, "no zwp_virtual_keyboard_manager_v1 / wl_seat\n"), 1;
    struct xkb_context *ctx = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    struct xkb_rule_names names = {.layout = "us"};
    struct xkb_keymap *km = xkb_keymap_new_from_names(ctx, &names, XKB_KEYMAP_COMPILE_NO_FLAGS);
    char *s = km ? xkb_keymap_get_as_string(km, XKB_KEYMAP_FORMAT_TEXT_V1) : NULL;
    if (!s)
        return fprintf(stderr, "no keymap\n"), 1;
    size_t size = strlen(s) + 1;
    int fd = memfd_create("vkbd-keymap", MFD_CLOEXEC);
    if (fd < 0 || write(fd, s, size) != (ssize_t)size)
        return 1;
    struct zwp_virtual_keyboard_v1 *kb = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(mgr, seat);
    zwp_virtual_keyboard_v1_keymap(kb, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fd, size);
    zwp_virtual_keyboard_v1_modifiers(kb, 0, 0, 0, 0);
    wl_display_roundtrip(dpy);
    for (int i = 1; i < argc; ++i) {
        int c = code(argv[i]);
        if (c < 0)
            return fprintf(stderr, "unknown key %s\n", argv[i]), 2;
        zwp_virtual_keyboard_v1_key(kb, now_ms(), c, WL_KEYBOARD_KEY_STATE_PRESSED);
        wl_display_roundtrip(dpy);
        usleep(30000);
        zwp_virtual_keyboard_v1_key(kb, now_ms(), c, WL_KEYBOARD_KEY_STATE_RELEASED);
        wl_display_roundtrip(dpy);
        usleep(120000);
    }
    zwp_virtual_keyboard_v1_destroy(kb);
    wl_display_roundtrip(dpy);
    return 0;
}
