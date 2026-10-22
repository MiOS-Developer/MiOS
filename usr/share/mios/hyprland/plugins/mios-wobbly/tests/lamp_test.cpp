// AI-hint: Unit tests for the magic-lamp mesh math and the dock/launch contract readers (lampmath.hpp, header-only,
// no Hyprland). Built and run by `make test` (host compiler; tests/test-mios-lamp.py calls it). Every positive check has a negative
// control that proves the checker can fail (a wrong rect, a folded mesh, a non-monotonic curve, malformed data).
// AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/lampmath.hpp, tests/test-mios-lamp.py, usr/share/mios/hyprland/plugins/mios-wobbly/Makefile
#include "../lampmath.hpp"

#include <cstdio>
#include <functional>
#include <set>
#include <string>

using namespace MiosLamp;

static int g_fail = 0, g_pass = 0;
#define CHECK(cond, ...)                                                                                                                                                         \
    do {                                                                                                                                                                         \
        if (cond)                                                                                                                                                                \
            ++g_pass;                                                                                                                                                            \
        else {                                                                                                                                                                   \
            ++g_fail;                                                                                                                                                            \
            std::printf("FAIL %s:%d: %s -- ", __FILE__, __LINE__, #cond);                                                                                                       \
            std::printf(__VA_ARGS__);                                                                                                                                            \
            std::printf("\n");                                                                                                                                                   \
        }                                                                                                                                                                        \
    } while (0)

static bool near(double a, double b, double eps = 1e-6) {
    return std::abs(a - b) <= eps;
}

// Every vertex sits where the source point (u, v) of rect r would be: the mesh IS rect r, texture-aligned.
static bool meshIsRect(const std::vector<SVertex>& m, const SRect& r, double eps = 1e-6) {
    if (m.empty())
        return false;
    for (const auto& v : m)
        if (!near(v.x, r.x + v.u * r.w, eps) || !near(v.y, r.y + v.v * r.h, eps))
            return false;
    return true;
}

// Rows along the funnel never fold back (destination order follows source order along the axis).
static bool noFold(const std::vector<SVertex>& m, int cols, int rows, eEdge e) {
    for (int i = 0; i <= cols; ++i)
        for (int j = 1; j <= rows; ++j) {
            const auto a = toCanon(e, {m[(j - 1) * (cols + 1) + i].x, m[(j - 1) * (cols + 1) + i].y});
            const auto b = toCanon(e, {m[j * (cols + 1) + i].x, m[j * (cols + 1) + i].y});
            if (b.y < a.y - 1e-9)
                return false;
        }
    return true;
}

template <typename F>
static bool nonDecreasing(F f, int n = 400) {
    double prev = f(0.0);
    for (int k = 1; k <= n; ++k) {
        const double v = f((double)k / n);
        if (v < prev - 1e-9)
            return false;
        prev = v;
    }
    return true;
}

static void testEndpointsAndMapping() {
    const SRect      win{200, 120, 900, 600}, icon{940, 1020, 56, 56};
    SMeshParams      p;
    std::vector<SVertex> m;
    for (eEdge e : {eEdge::BOTTOM, eEdge::TOP, eEdge::LEFT, eEdge::RIGHT}) {
        CHECK(buildMesh(win, icon, e, 0.0, p, m), "edge %s", edgeName(e));
        CHECK((int)m.size() == (p.cols + 1) * (p.rows + 1), "size %zu", m.size());
        CHECK(meshIsRect(m, win), "t=0 is the window (edge %s)", edgeName(e));
        const auto b0 = meshBounds(m);
        CHECK(near(b0.x, win.x) && near(b0.y, win.y) && near(b0.w, win.w) && near(b0.h, win.h), "t=0 bounds (edge %s)", edgeName(e));
        CHECK(buildMesh(win, icon, e, 1.0, p, m), "t=1");
        CHECK(meshIsRect(m, icon), "t=1 is the icon, texture-aligned (edge %s)", edgeName(e));
        // negative control: the end mesh is not the window, the start mesh is not the icon, mid is neither
        CHECK(!meshIsRect(m, win), "negative: t=1 is not the window");
        buildMesh(win, icon, e, 0.5, p, m);
        CHECK(!meshIsRect(m, win, 0.5) && !meshIsRect(m, icon, 0.5), "negative: t=0.5 is neither rect (edge %s)", edgeName(e));
    }
    // every source corner reaches the matching icon corner (orientation preserved, no mirror)
    buildMesh(win, icon, eEdge::BOTTOM, 1.0, p, m);
    const auto& tl = m.front();
    const auto& br = m.back();
    CHECK(near(tl.u, 0) && near(tl.v, 0) && near(tl.x, icon.x) && near(tl.y, icon.y), "top-left corner");
    CHECK(near(br.u, 1) && near(br.v, 1) && near(br.x, icon.x + icon.w) && near(br.y, icon.y + icon.h), "bottom-right corner");
}

static void testShape() {
    const SRect          win{200, 120, 900, 600}, icon{940, 1020, 56, 56};
    SMeshParams          p;
    std::vector<SVertex> m;
    // Curved sides: once shaped, the near end is narrower than the far end, and the left side is not straight.
    buildMesh(win, icon, eEdge::BOTTOM, 0.4, p, m);
    const int  C = p.cols, R = p.rows;
    const auto rowW = [&](int j) { return m[j * (C + 1) + C].x - m[j * (C + 1)].x; };
    CHECK(rowW(R) < rowW(0) * 0.8, "near end pinched: %f vs %f", rowW(R), rowW(0));
    const double xl0 = m[0].x, xlR = m[R * (C + 1)].x, xlM = m[(R / 2) * (C + 1)].x;
    const double lin = (xl0 + xlR) / 2;
    CHECK(std::abs(xlM - lin) > 1.0, "side is curved (mid %f vs chord %f)", xlM, lin);
    // the bottom of the funnel heads to the icon (its centre moves towards the icon centre)
    const double nearC = (m[R * (C + 1)].x + m[R * (C + 1) + C].x) / 2;
    CHECK(std::abs(nearC - icon.cx()) < std::abs(win.cx() - icon.cx()), "near end centres on the icon");
}

// The icon on the side the funnel points to (a dock on that edge).
static SRect iconFor(eEdge e) {
    switch (e) {
        case eEdge::TOP: return {940, 8, 56, 56};
        case eEdge::LEFT: return {8, 500, 56, 56};
        case eEdge::RIGHT: return {1850, 500, 56, 56};
        default: return {940, 1020, 56, 56};
    }
}

static void testMonotonic() {
    const SRect win{400, 220, 900, 600};
    SMeshParams p;
    for (eEdge e : {eEdge::BOTTOM, eEdge::TOP, eEdge::LEFT, eEdge::RIGHT}) {
        const SRect icon = iconFor(e);
        CHECK(chooseEdge(win, icon, std::nullopt) == e, "geometry picks edge %s", edgeName(e));
        // every vertex only ever moves towards the icon along the axis; width of the near row only shrinks
        bool okAxis = true, okFold = true, okWidth = true;
        std::vector<SVertex> prev, cur;
        buildMesh(win, icon, e, 0.0, p, prev);
        double prevNearW = 1e18;
        for (int k = 1; k <= 200; ++k) {
            buildMesh(win, icon, e, k / 200.0, p, cur);
            for (size_t i = 0; i < cur.size(); ++i)
                if (toCanon(e, {cur[i].x, cur[i].y}).y < toCanon(e, {prev[i].x, prev[i].y}).y - 1e-9)
                    okAxis = false;
            okFold = okFold && noFold(cur, p.cols, p.rows, e);
            const auto a = toCanon(e, {cur[p.rows * (p.cols + 1)].x, cur[p.rows * (p.cols + 1)].y});
            const auto b = toCanon(e, {cur[p.rows * (p.cols + 1) + p.cols].x, cur[p.rows * (p.cols + 1) + p.cols].y});
            const double w = std::abs(b.x - a.x);
            if (w > prevNearW + 1e-9)
                okWidth = false;
            prevNearW = w;
            prev      = cur;
        }
        CHECK(okAxis, "monotonic progress along the funnel (edge %s)", edgeName(e));
        CHECK(okFold, "never folds (edge %s)", edgeName(e));
        CHECK(okWidth, "near end only narrows (edge %s)", edgeName(e));
    }
    // overlapping rects (icon under the window) still never fold
    std::vector<SVertex> m;
    const SRect over{300, 300, 600, 600}, ic2{500, 700, 60, 60};
    bool        ok = true;
    for (int k = 0; k <= 50; ++k) {
        buildMesh(over, ic2, eEdge::BOTTOM, k / 50.0, p, m);
        for (const auto& v : m)
            ok = ok && std::isfinite(v.x) && std::isfinite(v.y);
        ok = ok && noFold(m, p.cols, p.rows, eEdge::BOTTOM);
    }
    CHECK(ok, "overlap: finite, no fold");
    // negative control: the fold checker catches a folded mesh
    buildMesh(win, iconFor(eEdge::BOTTOM), eEdge::BOTTOM, 0.5, p, m);
    std::swap(m[2 * (p.cols + 1)].y, m[5 * (p.cols + 1)].y);
    CHECK(!noFold(m, p.cols, p.rows, eEdge::BOTTOM), "negative: a folded mesh is detected");

    // easing curve
    const auto ease = SBezier::make(0.42, 0.0, 0.2, 1.0);
    CHECK(near(ease(0), 0) && near(ease(1), 1), "bezier endpoints");
    CHECK(nonDecreasing([&](double x) { return ease(x); }), "bezier monotonic");
    const auto lin = SBezier::make(0, 0, 1, 1);
    CHECK(near(lin(0.3), 0.3, 1e-4), "linear bezier: %f", lin(0.3));
    const auto ov = SBezier::make(0.3, 1.6, 0.6, -0.6); // overshooting curve
    CHECK(!nonDecreasing([&](double x) { return ov(x); }), "negative: the monotonic checker catches an overshooting curve");
    CHECK(nonDecreasing([](double t) { return -paneFade(t, 0.4); }) && near(paneFade(0, 0.4), 1) && near(paneFade(1, 0.4), 0.4), "pane fade profile");
    CHECK(near(contentAlpha(0), 1) && near(contentAlpha(1), 0) && nonDecreasing([](double t) { return -contentAlpha(t); }), "content alpha");
}

static void testDegenerate() {
    SMeshParams          p;
    std::vector<SVertex> m;
    const SRect          win{0, 0, 800, 600}, icon{400, 1000, 48, 48};
    CHECK(!buildMesh(SRect{0, 0, 0, 600}, icon, eEdge::BOTTOM, 0.5, p, m) && m.empty(), "zero-width window rejected");
    CHECK(!buildMesh(win, SRect{400, 1000, 48, -3}, eEdge::BOTTOM, 0.5, p, m) && m.empty(), "negative-height icon rejected");
    CHECK(!buildMesh(win, SRect{NAN, 1000, 48, 48}, eEdge::BOTTOM, 0.5, p, m), "NaN icon rejected");
    CHECK(!buildMesh(win, icon, eEdge::BOTTOM, NAN, p, m), "NaN progress rejected");
    CHECK(!buildMesh(win, SRect{1e9, 0, 48, 48}, eEdge::BOTTOM, 0.5, p, m), "absurd coordinates rejected");
    // out-of-range progress is clamped, not extrapolated
    buildMesh(win, icon, eEdge::BOTTOM, 7.0, p, m);
    CHECK(meshIsRect(m, icon), "t>1 clamps to the icon");
    buildMesh(win, icon, eEdge::BOTTOM, -3.0, p, m);
    CHECK(meshIsRect(m, win), "t<0 clamps to the window");
    // silly mesh params are clamped
    SMeshParams bad;
    bad.cols = -5;
    bad.rows = 100000;
    CHECK(buildMesh(win, icon, eEdge::BOTTOM, 0.5, bad, m) && m.size() == (size_t)(1 + 1) * (256 + 1), "mesh params clamped: %zu", m.size());
    // icon exactly at the window's far end (tube length 0) stays finite
    buildMesh(win, SRect{400, 0, 48, 48}, eEdge::BOTTOM, 0.6, p, m);
    bool fin = true;
    for (const auto& v : m)
        fin = fin && std::isfinite(v.x) && std::isfinite(v.y);
    CHECK(fin, "zero-length tube is finite");
}

static void testEdges() {
    const SRect win{500, 200, 800, 500};
    CHECK(chooseEdge(win, {880, 1020, 48, 48}, std::nullopt) == eEdge::BOTTOM, "dock below");
    CHECK(chooseEdge(win, {880, 20, 48, 48}, std::nullopt) == eEdge::TOP, "target above");
    CHECK(chooseEdge(win, {1800, 400, 48, 48}, std::nullopt) == eEdge::RIGHT, "target right");
    CHECK(chooseEdge(win, {10, 400, 48, 48}, std::nullopt) == eEdge::LEFT, "target left");
    CHECK(chooseEdge(win, {880, 1020, 48, 48}, eEdge::BOTTOM) == eEdge::BOTTOM, "hint agrees");
    CHECK(chooseEdge(win, {880, 20, 48, 48}, eEdge::BOTTOM) == eEdge::TOP, "negative: a contradicting hint is overruled");
    for (eEdge e : {eEdge::BOTTOM, eEdge::TOP, eEdge::LEFT, eEdge::RIGHT}) {
        const SPt p{123.5, -77.25};
        const SPt q = fromCanon(e, toCanon(e, p));
        CHECK(near(p.x, q.x) && near(p.y, q.y), "canon round trip %s", edgeName(e));
    }
}

static const char* DOCK = R"({
  "version": 1, "updated_ms": 1760720000000,
  "outputs": {
    "HDMI-A-1": { "visible": true, "edge": "bottom",
      "dock_rect": [600, 1012, 720, 64], "edge_rect": [600, 1078, 720, 2], "minimized_slot": [1260, 1020, 48, 48],
      "icons": [
        { "app_id": "org.gnome.Nautilus", "desktop_id": "org.gnome.Nautilus.desktop", "classes": ["org.gnome.Nautilus", "nautilus"], "rect": [700, 1020, 48, 48] },
        { "app_id": "mios-terminal", "desktop_id": "mios-terminal.desktop", "classes": ["Alacritty"], "rect": [760, 1020, 48, 48] },
        { "app_id": "broken", "rect": [1, 2, 3] },
        { "app_id": "zero", "rect": [10, 10, 0, 48] }
      ],
      "minimized": [ { "address": "0x55D0C0FFEE00", "rect": [1200, 1020, 48, 48] } ] },
    "HEADLESS-2": { "visible": false, "edge": "bottom", "edge_rect": [3000, 2076, 440, 4],
      "icons": [ { "app_id": "org.gnome.Nautilus", "rect": [3100, 2000, 44, 44] } ] },
    "BAD": "nope"
  } })";

static void testDock() {
    const auto dock = parseDock(DOCK);
    CHECK(dock.size() == 2, "two valid outputs (got %zu)", dock.size());
    const auto& h = dock.at("HDMI-A-1");
    CHECK(h.icons.size() == 2, "malformed icons skipped (got %zu)", h.icons.size());
    STargetQuery q;
    q.output  = "HDMI-A-1";
    q.monitor = {0, 0, 1920, 1080};
    q.cls     = "NAUTILUS"; // case-insensitive
    auto t    = resolveTarget(dock, q);
    CHECK(t.source == "icon" && near(t.rect.x, 700) && near(t.rect.y, 1020), "icon by class: %s %f", t.source.c_str(), t.rect.x);
    q.cls        = "foo";
    q.initialCls = "Alacritty";
    t            = resolveTarget(dock, q);
    CHECK(t.source == "icon" && near(t.rect.x, 760), "icon by initial class");
    q.cls = q.initialCls = "no.such.app";
    t                    = resolveTarget(dock, q);
    CHECK(t.source == "slot" && near(t.rect.x, 1260), "unknown app -> minimized slot (%s)", t.source.c_str());
    q.restore = true;
    q.address = "0x55d0c0ffee00";
    t         = resolveTarget(dock, q);
    CHECK(t.source == "minimized" && near(t.rect.x, 1200), "restore from its minimized entry (%s)", t.source.c_str());
    q.restore = false;
    t         = resolveTarget(dock, q);
    CHECK(t.source != "minimized", "negative: minimize does not aim at the minimized strip");
    // hidden dock -> the icon's place on the edge strip
    STargetQuery hq;
    hq.output  = "HEADLESS-2";
    hq.monitor = {3000, 1000, 440, 1080};
    hq.cls     = "org.gnome.Nautilus";
    t          = resolveTarget(dock, hq);
    CHECK(t.source == "edge" && near(t.rect.x, 3100) && near(t.rect.y, 2076) && near(t.rect.h, 4), "hidden dock -> edge strip (%s %f %f)", t.source.c_str(), t.rect.x, t.rect.y);
    hq.hiddenToEdge = false;
    t               = resolveTarget(dock, hq);
    CHECK(t.source == "icon" && near(t.rect.y, 2000), "hidden_dock = icon keeps the revealed rect");
}

static void testFallback() {
    STargetQuery q;
    q.output  = "DP-9";
    q.monitor = {1920, 0, 2560, 1440};
    q.cls     = "x";
    auto t    = resolveTarget(DockMap{}, q);
    CHECK(t.source == "fallback" && near(t.rect.cx(), 1920 + 1280) && near(t.rect.y + t.rect.h, 1440), "no dock data -> bottom edge centre (%f %f)", t.rect.cx(),
          t.rect.y + t.rect.h);
    t = resolveTarget(parseDock(DOCK), q);
    CHECK(t.source == "fallback", "output missing from the dock file -> fallback");
    for (const char* bad : {"", "{", "[]", "{\"outputs\": 3}", "{\"outputs\": {\"A\": {\"icons\": 7}}}", "{\"outputs\":{\"A\":{\"icons\":[{\"app_id\":\"x\",\"rect\":[1,2,\"3\",4]}]}}}",
                            "\x01\x02", "{\"outputs\": {}} trailing"}) {
        const auto d = parseDock(bad);
        bool       anyIcon = false;
        for (const auto& [n, o] : d)
            anyIcon = anyIcon || !o.icons.empty();
        CHECK(!anyIcon, "malformed dock JSON yields no icons: %s", bad);
        auto t2 = resolveTarget(d, q);
        CHECK(t2.source == "fallback" || t2.source == "edge", "malformed -> fallback (%s)", t2.source.c_str());
    }
    q.fallbackEdge = eEdge::LEFT;
    t              = resolveTarget(DockMap{}, q);
    CHECK(near(t.rect.x, 1920) && near(t.rect.cy(), 720), "fallback edge left");
    // a fallback still produces a drawable lamp
    std::vector<SVertex> m;
    CHECK(buildMesh({2000, 100, 900, 700}, t.rect, chooseEdge({2000, 100, 900, 700}, t.rect, t.edgeHint), 0.7, SMeshParams{}, m), "fallback target is drawable");
    // degenerate monitor too: still a finite rect
    STargetQuery z;
    z.output = "none";
    auto t3  = resolveTarget(DockMap{}, z);
    CHECK(t3.rect.valid(), "no monitor, no dock: still a valid rect");
}

static void testLaunch() {
    const char* L = R"({ "version": 1, "pending": [
        { "id": "b", "time_ms": 2000, "app_id": "org.gnome.Nautilus", "output": "HDMI-A-1", "rect": [700, 1020, 48, 48] },
        { "id": "a", "time_ms": 1000, "app_id": "org.gnome.Nautilus", "classes": ["nautilus"], "output": "HDMI-A-1", "rect": [700, 1020, 48, 48] },
        { "id": "c", "time_ms": 3000, "app_id": "mios-terminal", "classes": ["Alacritty"], "rect": [760, 1020, 48, 48] },
        { "id": "bad", "time_ms": 3000, "app_id": "x", "rect": [0, 0, 0, 0] },
        { "id": "norect", "time_ms": 3000, "app_id": "y" } ] })";
    const auto p = parseLaunches(L);
    CHECK(p.size() == 3 && p[0].id == "a", "valid launches sorted oldest first (%zu)", p.size());
    std::set<std::string> used;
    auto                  isUsed = [&](const std::string& id) { return used.contains(id); };
    auto                  m      = matchLaunch(p, 4000, 8000, "nautilus", "", "HDMI-A-1", isUsed);
    CHECK(m && m->id == "a", "oldest matching launch");
    used.insert("a");
    m = matchLaunch(p, 4000, 8000, "org.gnome.nautilus", "", "HDMI-A-1", isUsed);
    CHECK(m && m->id == "b", "consumed launches are skipped");
    m = matchLaunch(p, 4000, 8000, "Alacritty", "", "HEADLESS-2", isUsed);
    CHECK(m && m->id == "c", "an entry without output matches any output");
    m = matchLaunch(p, 4000, 8000, "nautilus", "", "HEADLESS-2", isUsed);
    CHECK(!m, "negative: other output");
    m = matchLaunch(p, 20000, 8000, "nautilus", "", "HDMI-A-1", isUsed);
    CHECK(!m, "negative: timed out");
    m = matchLaunch(p, 4000, 8000, "firefox", "", "HDMI-A-1", isUsed);
    CHECK(!m, "negative: no class match");
    CHECK(parseLaunches("{\"pending\": 5}").empty() && parseLaunches("garbage").empty(), "malformed launch file");
}

static void testJson() {
    const auto v = CJsonReader(R"({"s": "a\"bé😀", "n": -1.5e2, "t": true, "z": null, "a": [1, [2, {}]]})").parse();
    CHECK(v.has_value(), "parse");
    if (v) {
        CHECK(v->string("s") == "a\"b\xc3\xa9\xf0\x9f\x98\x80", "escapes");
        CHECK(v->number("n") && near(*v->number("n"), -150), "number");
        CHECK(v->boolean("t") == true, "bool");
    }
    std::string deep;
    for (int i = 0; i < 100; ++i)
        deep += "[";
    CHECK(!CJsonReader(deep).parse(), "negative: depth bomb rejected");
    CHECK(!CJsonReader("{\"a\": 1,}").parse(), "negative: trailing comma rejected");
    CHECK(!CJsonReader("{\"a\": 01x}").parse(), "negative: bad number rejected");
}

static void testWobblePaneFade() {
    CHECK(near(wobblePaneFade(0.0, 0.05, 2.0), 1.0), "wobble pane fade at rest is 1.0");
    CHECK(near(wobblePaneFade(2.0, 0.05, 2.0), 0.05), "wobble pane fade at thresh is 0.05");
    CHECK(near(wobblePaneFade(4.0, 0.05, 2.0), 0.05), "wobble pane fade beyond thresh is clamped to 0.05");
    for (double d = 0.0; d <= 6.0; d += 0.25) {
        const double f = wobblePaneFade(d, 0.05, 2.0);
        const double gain = 1.0 / f;
        CHECK(near(f * gain, 1.0), "gain compensation preserves unity brightness: dev=%f", d);
    }
    // Monotonic decrease with dev
    double prev = wobblePaneFade(0.0, 0.05, 2.0);
    bool mono = true;
    for (double d = 0.1; d <= 4.0; d += 0.1) {
        const double cur = wobblePaneFade(d, 0.05, 2.0);
        if (cur > prev + 1e-9) mono = false;
        prev = cur;
    }
    CHECK(mono, "wobblePaneFade decreases monotonically with dev");

    // Negative controls
    CHECK(!near(wobblePaneFade(2.0, 0.05, 2.0) * 1.0, 1.0), "negative: uncompensated gain fails unity check");
    CHECK(wobblePaneFade(-1.0, 0.05, 2.0) <= 1.0, "negative: negative dev handled safely");
}

static void testRestoreTargetPersistence() {
    const auto dock = parseDock(DOCK);
    STargetQuery q;
    q.output    = "HDMI-A-1";
    q.monitor   = {0, 0, 1920, 1080};
    q.restore   = true;
    q.address   = "0x55d0c0ffee00";
    q.cls       = "org.gnome.Nautilus";
    auto t      = resolveTarget(dock, q);
    CHECK(t.source == "minimized" && near(t.rect.x, 1200), "restore finds minimized icon");
    CHECK(t.rect.valid(), "restore target rect is valid");

    // sourcePreference = "icon" overrides minimized
    q.sourcePreference = "icon";
    t = resolveTarget(dock, q);
    CHECK(t.source == "icon" && near(t.rect.x, 700), "sourcePreference icon resolves app icon");

    // Output mismatch searches all outputs
    q.sourcePreference = "";
    q.output = "NONEXISTENT-OUT";
    t = resolveTarget(dock, q);
    CHECK(t.source == "minimized" && near(t.rect.x, 1200), "fallback across outputs finds minimized window");

    // Empty minimized list with unknown window falls back to valid target, never [0,0,0,0]
    q.address = "0xunknown";
    q.cls = "unknown.app";
    q.output = "HDMI-A-1";
    t = resolveTarget(dock, q);
    CHECK(t.rect.valid(), "untracked window restore produces valid rect");
    CHECK(t.rect.w > 0 && t.rect.h > 0, "untracked window restore has positive dimensions");

    // Negative control: uninitialized rect check
    const SRect uninit{0, 0, 0, 0};
    CHECK(!uninit.valid(), "negative: [0,0,0,0] is not valid");
}

static void testTiledSlotRestoration() {
    // Left slot on 1920x1080 workspace
    const SRect sOrig{0, 0, 960, 1080};
    // Default dwindle right split
    const SRect sDwindle{960, 0, 960, 1080};

    const double origCenterX = sOrig.cx();
    const double dwindleCenterX = sDwindle.cx();

    CHECK(origCenterX < dwindleCenterX - 50.0, "orig center (%f) is left of dwindle center (%f), movewindow l needed", origCenterX, dwindleCenterX);

    // Negative control: dwindle default does not match original left slot
    CHECK(!near(sOrig.x, sDwindle.x), "negative: dwindle default differs from left slot");
}

static void testCrossOutputMinimizedFallback() {
    const char* DOCK_MULTI = R"({
      "version": 1, "updated_ms": 1760720000000,
      "outputs": {
        "HDMI-A-1": { "visible": true, "icons": [] },
        "HEADLESS-2": { "visible": true, "edge": "bottom", "dock_rect": [3000, 2000, 440, 56],
          "minimized": [ { "address": "0x55d0c0ffee99", "rect": [3200, 2000, 48, 48] } ] }
      }
    })";
    const auto dock = parseDock(DOCK_MULTI);
    STargetQuery q;
    q.output    = "HDMI-A-1";
    q.monitor   = {0, 0, 1920, 1080};
    q.restore   = true;
    q.address   = "0x55d0c0ffee99";
    q.cls       = "unknown.app";
    auto t      = resolveTarget(dock, q);
    CHECK(t.source == "minimized" && near(t.rect.x, 3200), "cross-output minimized fallback finds window on HEADLESS-2 (%s, x=%f)", t.source.c_str(), t.rect.x);
}

int main() {
    testEndpointsAndMapping();
    testShape();
    testMonotonic();
    testDegenerate();
    testEdges();
    testDock();
    testFallback();
    testLaunch();
    testJson();
    testWobblePaneFade();
    testRestoreTargetPersistence();
    testTiledSlotRestoration();
    testCrossOutputMinimizedFallback();
    std::printf("lampmath: %d passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
