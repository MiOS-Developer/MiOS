/* Test fixture for tests/test-shell-hud-live.py and tests/test-shell-appmenu-live.py: a real pointer on ONE output of
   an owned nested Wayland compositor through zwlr_virtual_pointer_v1. Never point it at the live session.
     vptr <output> <x> <y> [hover|click|scroll:N]   output-local logical coordinates; N wheel notches, + = wheel up
   VPTR_W / VPTR_H give the output's logical size (the extent of motion_absolute). Built by the tests with
   wayland-scanner + gcc from wlr-virtual-pointer-unstable-v1.xml next to this file. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <wayland-client.h>
#include "vp-client-protocol.h"

struct out { struct wl_output *o; char name[64]; };
static struct zwlr_virtual_pointer_manager_v1 *mgr;
static struct wl_seat *seat;
static struct out outs[16];
static int nouts;

static void o_geom(void *d, struct wl_output *o, int32_t x, int32_t y, int32_t pw, int32_t ph, int32_t sp,
                   const char *mk, const char *md, int32_t t) {}
static void o_mode(void *d, struct wl_output *o, uint32_t f, int32_t w, int32_t h, int32_t r) {}
static void o_done(void *d, struct wl_output *o) {}
static void o_scale(void *d, struct wl_output *o, int32_t s) {}
static void o_name(void *d, struct wl_output *o, const char *n) { snprintf(((struct out *)d)->name, 64, "%s", n); }
static void o_desc(void *d, struct wl_output *o, const char *n) {}
static const struct wl_output_listener ol = { o_geom, o_mode, o_done, o_scale, o_name, o_desc };

static void reg_global(void *d, struct wl_registry *r, uint32_t id, const char *iface, uint32_t v) {
    if (!strcmp(iface, zwlr_virtual_pointer_manager_v1_interface.name))
        mgr = wl_registry_bind(r, id, &zwlr_virtual_pointer_manager_v1_interface, v < 2 ? v : 2);
    else if (!strcmp(iface, wl_seat_interface.name) && !seat)
        seat = wl_registry_bind(r, id, &wl_seat_interface, 1);
    else if (!strcmp(iface, wl_output_interface.name) && nouts < 16 && v >= 4) {
        struct out *u = &outs[nouts++];
        memset(u, 0, sizeof *u);
        u->o = wl_registry_bind(r, id, &wl_output_interface, 4);
        wl_output_add_listener(u->o, &ol, u);
    }
}
static void reg_remove(void *d, struct wl_registry *r, uint32_t id) {}
static const struct wl_registry_listener rl = { reg_global, reg_remove };
static uint32_t now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

int main(int argc, char **argv) {
    if (argc < 4 || !getenv("VPTR_W") || !getenv("VPTR_H")) {
        fprintf(stderr, "usage: VPTR_W=w VPTR_H=h vptr <output> <x> <y> [hover|click|scroll:N]\n");
        return 2;
    }
    const char *mode = argc > 4 ? argv[4] : "hover";
    struct wl_display *dpy = wl_display_connect(NULL);
    if (!dpy) { fprintf(stderr, "no display\n"); return 1; }
    struct wl_registry *reg = wl_display_get_registry(dpy);
    wl_registry_add_listener(reg, &rl, NULL);
    wl_display_roundtrip(dpy);
    wl_display_roundtrip(dpy);
    if (!mgr) { fprintf(stderr, "no zwlr_virtual_pointer_manager_v1\n"); return 1; }
    struct out *u = NULL;
    for (int i = 0; i < nouts; i++) if (!strcmp(outs[i].name, argv[1])) u = &outs[i];
    if (!u) { fprintf(stderr, "no output %s\n", argv[1]); return 1; }
    struct zwlr_virtual_pointer_v1 *p = zwlr_virtual_pointer_manager_v1_create_virtual_pointer_with_output(mgr, seat, u->o);
    uint32_t lw = atoi(getenv("VPTR_W")), lh = atoi(getenv("VPTR_H"));
    zwlr_virtual_pointer_v1_motion_absolute(p, now(), atoi(argv[2]), atoi(argv[3]), lw, lh);
    zwlr_virtual_pointer_v1_frame(p);
    wl_display_roundtrip(dpy);
    if (!strcmp(mode, "click")) {
        usleep(60000);
        zwlr_virtual_pointer_v1_button(p, now(), 0x110, WL_POINTER_BUTTON_STATE_PRESSED);
        zwlr_virtual_pointer_v1_frame(p);
        wl_display_roundtrip(dpy);
        usleep(40000);
        zwlr_virtual_pointer_v1_button(p, now(), 0x110, WL_POINTER_BUTTON_STATE_RELEASED);
        zwlr_virtual_pointer_v1_frame(p);
        wl_display_roundtrip(dpy);
    } else if (!strncmp(mode, "scroll:", 7)) {
        int n = atoi(mode + 7), dir = n > 0 ? -1 : 1, cnt = n > 0 ? n : -n;
        for (int i = 0; i < cnt; i++) {
            usleep(80000);
            zwlr_virtual_pointer_v1_axis_source(p, WL_POINTER_AXIS_SOURCE_WHEEL);
            zwlr_virtual_pointer_v1_axis_discrete(p, now(), WL_POINTER_AXIS_VERTICAL_SCROLL, wl_fixed_from_int(15 * dir), dir);
            zwlr_virtual_pointer_v1_frame(p);
            wl_display_roundtrip(dpy);
        }
    }
    usleep(30000);
    zwlr_virtual_pointer_v1_destroy(p);
    wl_display_roundtrip(dpy);
    wl_display_disconnect(dpy);
    return 0;
}
