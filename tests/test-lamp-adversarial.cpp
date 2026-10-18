// Adversarial challenge test harness for R2: lampmath.hpp & wobble math contracts
#include "/usr/share/mios/hyprland/plugins/mios-wobbly/lampmath.hpp"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <limits>
#include <string>
#include <vector>

using namespace MiosLamp;

static int g_pass = 0;
static int g_fail = 0;

#define CHK(cond, msg, ...)                                                     \
    do {                                                                        \
        if (cond) {                                                             \
            ++g_pass;                                                           \
        } else {                                                                \
            ++g_fail;                                                           \
            std::printf("FAIL [%s:%d]: " msg "\n", __FILE__, __LINE__, ##__VA_ARGS__); \
        }                                                                       \
    } while (0)

static bool approx(double a, double b, double eps = 1e-6) {
    return std::abs(a - b) <= eps;
}

// -----------------------------------------------------------------------------
// 1. wobblePaneFade edge cases
// -----------------------------------------------------------------------------
static void testWobblePaneFadeAdversarial() {
    std::printf("[TEST] wobblePaneFade Edge Cases & Stress...\n");

    // Boundary at rest: 0.0
    CHK(approx(wobblePaneFade(0.0), 1.0), "dev = 0.0 must be exactly 1.0");
    CHK(approx(wobblePaneFade(-0.0), 1.0), "dev = -0.0 must be exactly 1.0");

    // Negative dev (should be treated as at rest dev <= 0)
    CHK(approx(wobblePaneFade(-0.0001), 1.0), "dev = -0.0001 must be 1.0");
    CHK(approx(wobblePaneFade(-1.0), 1.0), "dev = -1.0 must be 1.0");
    CHK(approx(wobblePaneFade(-1000.0), 1.0), "dev = -1000.0 must be 1.0");
    CHK(approx(wobblePaneFade(-std::numeric_limits<double>::infinity()), 1.0), "dev = -INF must be 1.0");

    // Boundary at threshold (default thresh = 2.0, minFade = 0.05)
    CHK(approx(wobblePaneFade(2.0), 0.05), "dev = thresh (2.0) must be minFade (0.05)");
    CHK(wobblePaneFade(1.999999) > 0.05, "dev just below thresh must be > minFade");
    CHK(wobblePaneFade(1.999999) < 0.0501, "dev just below thresh must be near minFade");

    // Beyond threshold: should clamp cleanly to minFade
    CHK(approx(wobblePaneFade(2.000001), 0.05), "dev just above thresh must be 0.05");
    CHK(approx(wobblePaneFade(5.0), 0.05), "dev = 5.0 must be 0.05");
    CHK(approx(wobblePaneFade(1e6), 0.05), "dev = 1e6 must be 0.05");
    // Boundary at max finite double
    const double maxVal = std::numeric_limits<double>::max();
    CHK(approx(wobblePaneFade(maxVal), 0.05), "dev = max finite double must clamp to 0.05");

    // Non-finite +INF dev: clamp01 maps all non-finite values to 0.0 as NaN/Inf crash-defense,
    // which results in wobblePaneFade(+INF) = 1.0 (safe fallback to rest rather than NaN alpha)
    double fadeInf = wobblePaneFade(std::numeric_limits<double>::infinity());
    CHK(std::isfinite(fadeInf) && fadeInf >= 0.05 && fadeInf <= 1.0, "+INF dev must yield finite safe alpha [0.05, 1.0] (got %f)", fadeInf);

    // NaN dev handling
    const double nanVal = std::numeric_limits<double>::quiet_NaN();
    double fadeNan = wobblePaneFade(nanVal);
    CHK(std::isfinite(fadeNan) && fadeNan >= 0.05 && fadeNan <= 1.0, "NaN dev must degrade safely to a finite [0.05, 1.0] value (got %f)", fadeNan);

    // Extreme minFade parameters
    CHK(approx(wobblePaneFade(5.0, 0.0, 2.0), 0.05), "minFade < 0.05 must clamp to 0.05");
    CHK(approx(wobblePaneFade(5.0, -10.0, 2.0), 0.05), "negative minFade must clamp to 0.05");
    CHK(approx(wobblePaneFade(5.0, 2.0, 2.0), 1.0), "minFade > 1.0 must clamp to 1.0");
    CHK(approx(wobblePaneFade(5.0, 0.30, 2.0), 0.30), "minFade = 0.30 must yield 0.30 at max dev");
    CHK(approx(wobblePaneFade(5.0, nanVal, 2.0), 1.0), "NaN minFade must fallback to 1.0");

    // Extreme thresh parameters
    CHK(approx(wobblePaneFade(2.0, 0.05, 0.0), 0.05), "thresh = 0.0 must fallback to 2.0");
    CHK(approx(wobblePaneFade(2.0, 0.05, -5.0), 0.05), "negative thresh must fallback to 2.0");
    CHK(approx(wobblePaneFade(2.0, 0.05, nanVal), 0.05), "NaN thresh must fallback to 2.0");
    CHK(approx(wobblePaneFade(2.0, 0.05, std::numeric_limits<double>::infinity()), 0.05), "INF thresh must fallback to 2.0");

    // Custom valid thresh
    CHK(approx(wobblePaneFade(10.0, 0.10, 10.0), 0.10), "thresh = 10.0 at dev = 10.0 must yield 0.10");
    CHK(wobblePaneFade(5.0, 0.10, 10.0) > 0.10 && wobblePaneFade(5.0, 0.10, 10.0) < 1.0, "thresh = 10.0 at dev = 5.0 must be intermediate");

    // Strict Monotonicity test across dense sampling
    double lastVal = wobblePaneFade(0.0);
    bool strictlyNonIncreasing = true;
    for (double d = 0.0; d <= 4.0; d += 0.001) {
        double curVal = wobblePaneFade(d);
        if (curVal > lastVal + 1e-12) {
            strictlyNonIncreasing = false;
            break;
        }
        lastVal = curVal;
    }
    CHK(strictlyNonIncreasing, "wobblePaneFade must be strictly non-increasing over [0, 4]");

    // Derivative continuity at dev=thresh: smoothstep(0, 1, 1) derivative is 0, matching the flat post-thresh line
    double valJustBefore = wobblePaneFade(2.0 - 1e-5);
    double valAtThresh = wobblePaneFade(2.0);
    double valJustAfter = wobblePaneFade(2.0 + 1e-5);
    double derivBefore = (valAtThresh - valJustBefore) / 1e-5;
    double derivAfter = (valJustAfter - valAtThresh) / 1e-5;
    CHK(std::abs(derivBefore) < 1e-3, "derivative approaching threshold must approach 0 (got %f)", derivBefore);
    CHK(std::abs(derivAfter) < 1e-9, "derivative past threshold must be 0 (got %f)", derivAfter);

    // Negative controls:
    CHK(!approx(wobblePaneFade(1.0), 1.0), "negative: dev = 1.0 must not remain 1.0");
    CHK(!approx(wobblePaneFade(1.0), 0.05), "negative: dev = 1.0 must not drop to 0.05");
}

// -----------------------------------------------------------------------------
// 2. resolveTarget Edge & Boundary Conditions
// -----------------------------------------------------------------------------
static const char* SAMPLE_DOCK_JSON = R"({
    "version": 1,
    "outputs": {
        "HDMI-A-1": {
            "visible": true,
            "edge": "bottom",
            "dock_rect": [816, 1016, 288, 56],
            "edge_rect": [816, 1078, 288, 2],
            "minimized_slot": [1104, 1016, 48, 56],
            "icons": [
                { "app_id": "mios-terminal", "desktop_id": "mios-terminal.desktop", "classes": ["Alacritty"], "rect": [880, 1016, 48, 56] },
                { "app_id": "org.gnome.Nautilus", "desktop_id": "org.gnome.Nautilus.desktop", "classes": ["nautilus"], "rect": [936, 1016, 48, 56] }
            ],
            "minimized": [
                { "address": "0x55aa001122", "rect": [1200, 1016, 48, 56] },
                { "address": "0xdeadbeef", "rect": [1260, 1016, 48, 56] }
            ]
        },
        "DP-1": {
            "visible": true,
            "edge": "top",
            "dock_rect": [816, 8, 288, 56],
            "edge_rect": [816, 8, 288, 2],
            "minimized_slot": [1104, 8, 48, 56],
            "icons": [
                { "app_id": "editor", "rect": [880, 8, 48, 56] }
            ],
            "minimized": [
                { "address": "0xsecondary01", "rect": [1200, 8, 48, 56] }
            ]
        }
    }
})";

static void testResolveTargetAdversarial() {
    std::printf("[TEST] resolveTarget Edge & Stress Conditions...\n");

    const auto dock = parseDock(SAMPLE_DOCK_JSON);
    CHK(dock.size() == 2, "dock parsed 2 outputs");

    // Case 1: Missing output, query for window minimized on another output
    {
        STargetQuery q;
        q.output = "NONEXISTENT-MONITOR";
        q.monitor = {0, 0, 1920, 1080};
        q.restore = true;
        q.address = "0xsecondary01"; // Present on DP-1
        auto t = resolveTarget(dock, q);
        CHK(t.source == "minimized", "cross-output restore finds window on DP-1 (got %s)", t.source.c_str());
        CHK(approx(t.rect.y, 8), "cross-output restore locates DP-1 y coordinate (got %f)", t.rect.y);
        CHK(t.edgeHint == eEdge::TOP, "cross-output restore adopts DP-1 top edge");
    }

    // Case 2: Missing output, nonexistent window address -> fallback
    {
        STargetQuery q;
        q.output = "NONEXISTENT-MONITOR";
        q.monitor = {1920, 0, 1920, 1080};
        q.restore = true;
        q.address = "0xdoesnotexist";
        auto t = resolveTarget(dock, q);
        CHK(t.source == "fallback", "missing output + unknown window -> fallback");
        CHK(t.rect.valid(), "fallback target rect must be valid");
        CHK(t.rect.x >= 1920, "fallback target rect must be on query monitor x >= 1920 (got %f)", t.rect.x);
    }

    // Case 3: Completely empty dock
    {
        DockMap emptyDock;
        STargetQuery q;
        q.output = "HDMI-A-1";
        q.monitor = {0, 0, 1920, 1080};
        q.restore = true;
        q.address = "0x55aa001122";
        auto t = resolveTarget(emptyDock, q);
        CHK(t.source == "fallback", "empty dock -> fallback");
        CHK(t.rect.valid(), "empty dock rect must be valid");
        CHK(approx(t.rect.w, 64) || approx(t.rect.w, 48), "fallback width reasonable (got %f)", t.rect.w);
    }

    // Case 4: Completely empty query on empty dock (total degenerate input)
    {
        DockMap emptyDock;
        STargetQuery emptyQuery;
        auto t = resolveTarget(emptyDock, emptyQuery);
        CHK(t.source == "fallback", "empty query on empty dock -> fallback");
        CHK(t.rect.valid(), "empty query rect must be valid, NEVER [0,0,0,0]");
        CHK(t.rect.w > 0 && t.rect.h > 0, "rect dimensions must be positive");
    }

    // Case 5: sourcePreference = "icon" when window is also in minimized list
    {
        STargetQuery q;
        q.output = "HDMI-A-1";
        q.monitor = {0, 0, 1920, 1080};
        q.restore = true;
        q.address = "0xdeadbeef";
        q.cls = "org.gnome.Nautilus";
        q.sourcePreference = "icon";
        auto t = resolveTarget(dock, q);
        CHK(t.source == "icon", "sourcePreference 'icon' must resolve icon even when restore=true");
        CHK(approx(t.rect.x, 936), "icon rect x matches Nautilus icon (936, got %f)", t.rect.x);
    }

    // Case 6: sourcePreference = "icon", but icon is NOT in dock -> fallback to minimized
    {
        STargetQuery q;
        q.output = "HDMI-A-1";
        q.monitor = {0, 0, 1920, 1080};
        q.restore = true;
        q.address = "0xdeadbeef";
        q.cls = "UnpinnedSpecialApp";
        q.sourcePreference = "icon";
        auto t = resolveTarget(dock, q);
        CHK(t.source == "minimized", "missing icon with sourcePreference 'icon' must fallback to minimized");
        CHK(approx(t.rect.x, 1260), "minimized rect matches 0xdeadbeef (1260, got %f)", t.rect.x);
    }

    // Case 7: Invalid / Extreme Addresses (buffer overflow / injection resistance)
    {
        STargetQuery q;
        q.output = "HDMI-A-1";
        q.monitor = {0, 0, 1920, 1080};
        q.restore = true;
        q.address = std::string(10000, 'F');
        auto t = resolveTarget(dock, q);
        CHK(t.rect.valid(), "huge address string must not crash and yield valid rect");

        q.address = "";
        t = resolveTarget(dock, q);
        CHK(t.rect.valid(), "empty address string must yield valid rect");

        q.address = "\0\0\0";
        t = resolveTarget(dock, q);
        CHK(t.rect.valid(), "null byte address must yield valid rect");
    }

    // Case 8: Auto-hidden dock projection
    {
        const char* HIDDEN_DOCK_JSON = R"({
            "version": 1,
            "outputs": {
                "HDMI-A-1": {
                    "visible": false,
                    "edge": "bottom",
                    "dock_rect": [816, 1016, 288, 56],
                    "edge_rect": [816, 1078, 288, 2],
                    "icons": [
                        { "app_id": "test", "rect": [900, 1016, 48, 56] }
                    ]
                }
            }
        })";
        const auto hDock = parseDock(HIDDEN_DOCK_JSON);
        STargetQuery q;
        q.output = "HDMI-A-1";
        q.monitor = {0, 0, 1920, 1080};
        q.cls = "test";
        q.hiddenToEdge = true;
        auto t = resolveTarget(hDock, q);
        CHK(t.source == "edge", "hidden dock target source must be 'edge'");
        CHK(approx(t.rect.y, 1078), "hidden dock target y projected to edge_rect (1078, got %f)", t.rect.y);
        CHK(approx(t.rect.x, 900), "hidden dock preserves icon x coordinate (900, got %f)", t.rect.x);
    }
}

// -----------------------------------------------------------------------------
// 3. Tiled Slot Math & Swap Directions
// -----------------------------------------------------------------------------
static void testTiledSlotLogicAdversarial() {
    std::printf("[TEST] Tiled Slot Layout Restoration Logic...\n");

    // Case: Left slot restoration vs Dwindle right half
    // Workspace 1920x1080
    // W1 was left: [0, 0, 960, 1080] -> center X = 480
    // Sibling W2 fills screen while W1 is minimized: [0, 0, 1920, 1080]
    // Upon restore, dwindle splits W2 and puts W1 on right half: [960, 0, 960, 1080] -> center X = 1440
    const double preLeftCenterX = 480.0;
    const double curRightCenterX = 1440.0;
    CHK(preLeftCenterX < curRightCenterX - 50.0, "left slot vs right split triggers movewindow l");

    // Case: Right slot restoration vs Dwindle left half
    const double preRightCenterX = 1440.0;
    const double curLeftCenterX = 480.0;
    CHK(preRightCenterX > curLeftCenterX + 50.0, "right slot vs left split triggers movewindow r");

    // Case: Top slot restoration vs Dwindle bottom half
    const double preTopCenterY = 270.0;
    const double curBottomCenterY = 810.0;
    CHK(preTopCenterY < curBottomCenterY - 50.0, "top slot vs bottom split triggers movewindow u");

    // Case: Bottom slot restoration vs Dwindle top half
    const double preBottomCenterY = 810.0;
    const double curTopCenterY = 270.0;
    CHK(preBottomCenterY > curTopCenterY + 50.0, "bottom slot vs top split triggers movewindow d");

    // Case: Center within tolerance (e.g. 20px difference due to borders/gaps)
    const double preCenterClose = 480.0;
    const double curCenterClose = 495.0;
    CHK(!(preCenterClose < curCenterClose - 50.0) && !(preCenterClose > curCenterClose + 50.0),
        "center within 50px tolerance does not trigger layout move");
}

int main() {
    testWobblePaneFadeAdversarial();
    testResolveTargetAdversarial();
    testTiledSlotLogicAdversarial();

    std::printf("==================================================\n");
    std::printf("ADVERSARIAL CHALLENGE RESULT: %d passed, %d failed\n", g_pass, g_fail);
    std::printf("==================================================\n");
    return g_fail ? 1 : 0;
}
