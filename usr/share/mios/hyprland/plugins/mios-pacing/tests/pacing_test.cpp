// AI-hint: Host tests for mios-pacing's pure frame gate (pacing.hpp) with a simulated clock: a cap holds the admitted
//          rate at 60/90/120 within 1% whatever the request rate, deferred frames rescheduled at the deadline lose
//          nothing, no cap admits everything, idle gaps resync, exclusions and the uncapped reasons (negative controls);
//          the focused window's committed-frame rate (fastest surface of its tree; non-frame commits never count).
// AI-related: usr/share/mios/hyprland/plugins/mios-pacing/pacing.hpp, usr/share/mios/hyprland/plugins/mios-pacing/Makefile
#include "../pacing.hpp"

#include <cmath>
#include <cstdio>
#include <cstdlib>

using namespace MiosPacing;
using std::chrono::microseconds;
using std::chrono::milliseconds;

static int g_fail = 0;
#define CHECK(c, ...)                                                                                                                                                    \
    do {                                                                                                                                                                 \
        if (!(c)) {                                                                                                                                                      \
            std::printf("FAIL %s:%d: ", __FILE__, __LINE__);                                                                                                             \
            std::printf(__VA_ARGS__);                                                                                                                                    \
            std::printf("\n");                                                                                                                                           \
            ++g_fail;                                                                                                                                                    \
        }                                                                                                                                                                \
    } while (0)

// Frame events arrive whenever the compositor asks (damage every `request` us); a deferred one re-arms a timer that
// fires exactly at the deadline (+ `jitter` us) -- the VRR case: the panel shows each admitted commit. Returns the
// admitted frames per second over `seconds`.
static double simulate(int cap, int requestUs, int jitterUs, double seconds) {
    SGate             g;
    Clock::time_point t0{};
    Clock::time_point now     = t0;
    Clock::time_point timerAt = Clock::time_point::max();
    Clock::time_point nextReq = t0;
    int               admitted = 0;
    const auto        END = t0 + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(seconds));
    while (now < END) {
        now = std::min(nextReq, timerAt);
        if (now == timerAt)
            timerAt = Clock::time_point::max();
        if (now == nextReq)
            nextReq = now + microseconds(requestUs);
        if (admit(g, now, cap, microseconds(500)))
            ++admitted;
        else if (timerAt == Clock::time_point::max())
            timerAt = g.next + microseconds(jitterUs);
    }
    return admitted / seconds;
}

int main() {
    for (int cap : {60, 90, 120}) {
        for (int req : {1000, 4000, 6944}) {
            const double FPS = simulate(cap, req, 300, 10.0);
            CHECK(std::abs(FPS - cap) <= cap * 0.01, "cap %d request %d us: %.2f fps", cap, req, FPS);
        }
    }
    // Negative controls: no cap admits every request; a request rate below the cap is never held back.
    CHECK(std::abs(simulate(0, 4000, 0, 5.0) - 250.0) < 1.0, "no cap: every frame");
    CHECK(std::abs(simulate(120, 20000, 0, 5.0) - 50.0) < 1.0, "50 fps content under a 120 cap stays 50");

    // Phase lock: deadlines advance by exactly one period while busy.
    SGate g;
    Clock::time_point t{};
    CHECK(admit(g, t, 90, microseconds(500)), "first frame passes");
    const auto P = periodOf(90);
    CHECK(!admit(g, t + P / 2, 90, microseconds(500)), "half a period later: deferred");
    CHECK(admit(g, t + P - microseconds(200), 90, microseconds(500)), "within the slack: passes");
    CHECK(g.next == t + 2 * P, "phase kept (no drift from the early frame)");
    // Idle gap: resync instead of a burst of catch-up frames.
    CHECK(admit(g, t + 10 * P, 90, microseconds(500)), "after idle: passes");
    CHECK(g.next == t + 11 * P, "resynced to now + period");
    CHECK(!admit(g, t + 10 * P + P / 3, 90, microseconds(500)), "and the next one waits again");

    // Exclusions (the RDP virtual output keeps its own pacing).
    CHECK(excluded("HEADLESS-MIOS-RDP", "HEADLESS-"), "headless excluded");
    CHECK(excluded("VIRT-1", "HEADLESS-, VIRT-"), "list with spaces");
    CHECK(!excluded("DP-3", "HEADLESS-"), "DP-3 capped");
    CHECK(!excluded("DP-3", ""), "empty list excludes nothing");

    // Wallpaper frame callbacks (the same gate at W): the compositor offers a callback on every output frame (60 or
    // 144 Hz); the held ones are released by a timer at the deadline -- the wallpaper is asked for W frames a second.
    for (int w : {30, 45, 60}) {
        for (int frameUs : {16667, 6944}) {
            const double FPS = simulate(w, frameUs, 300, 10.0);
            CHECK(std::abs(FPS - w) <= w * 0.01, "wallpaper %d at %d us frames: %.2f callbacks/s", w, frameUs, FPS);
        }
    }
    CHECK(std::abs(simulate(0, 16667, 0, 5.0) - 60.0) < 1.0, "wallpaper_fps 0: every output frame (no throttle)");
    // Namespaces: exact entries only.
    CHECK(listed("mios-wallpaper", "mios-wallpaper"), "the wallpaper namespace");
    CHECK(listed("swww-daemon", "mios-wallpaper, swww-daemon"), "list with spaces");
    CHECK(!listed("mios-wallpaper-native-probe", "mios-wallpaper"), "a probe namespace is not the wallpaper");
    CHECK(!listed("mios-wallpaper", ""), "empty list throttles nothing");
    CHECK(!listed("", "mios-wallpaper"), "an unnamed layer is not the wallpaper");

    // Reasons.
    CHECK(uncappedReason(true, 90, false, false, true, true, false, 144.0).empty(), "VRR output capped");
    CHECK(uncappedReason(true, 90, false, false, true, false, false, 144.0) == "no active VRR", "fixed refresh: not capped");
    CHECK(uncappedReason(true, 90, false, false, false, false, false, 144.0).empty(), "require_vrr off: capped anyway");
    CHECK(uncappedReason(true, 0, false, false, true, true, false, 144.0) == "no cap", "unlocked");
    CHECK(uncappedReason(true, 144, false, false, true, true, false, 143.998) == "cap at or above the refresh", "cap >= refresh");
    CHECK(uncappedReason(true, 90, false, true, true, true, false, 144.0) == "excluded", "excluded");
    CHECK(uncappedReason(false, 90, false, false, true, true, false, 144.0) == "disabled", "disabled");

    // Meter.
    SMeter m;
    for (int i = 0; i <= 90; ++i)
        m.tick(t + i * P);
    CHECK(std::abs(m.fps(t + 90 * P) - 90.0) < 1.0, "meter reads 90 (%.2f)", m.fps(t + 90 * P));

    // Focused-window rate: a 24 FPS video subsurface under an idle toplevel is a 24 FPS window.
    {
        SWindowRate r;
        const auto  V = periodOf(24);
        for (int i = 0; i <= 48; ++i)
            r.tick(0x2000, t + i * V);
        r.tick(0x1000, t); // the toplevel's one configure frame
        CHECK(std::abs(r.fps(t + 48 * V) - 24.0) < 0.5, "video subsurface: 24 (%.2f)", r.fps(t + 48 * V));
        CHECK(r.surfaces.size() == 1, "the idle toplevel dropped out (%zu)", r.surfaces.size());
        CHECK(r.frames == 50, "every frame counted (%llu)", static_cast<unsigned long long>(r.frames));
        // An idle window reads 0 after a second; nothing is tracked any more.
        CHECK(r.fps(t + 48 * V + std::chrono::milliseconds(1500)) == 0.0, "idle: 0");
        CHECK(r.surfaces.empty(), "idle: no surface left");
    }
    // A toplevel repainting at 60 around a 30 FPS video is a 60 FPS window (the fastest surface; never the sum).
    {
        SWindowRate r;
        const auto  A = periodOf(60), B = periodOf(30);
        for (int i = 0; i <= 120; ++i)
            r.tick(0x1000, t + i * A);
        for (int i = 0; i <= 60; ++i)
            r.tick(0x2000, t + i * B);
        const double F = r.fps(t + 120 * A);
        CHECK(std::abs(F - 60.0) < 0.5, "fastest surface: 60 (%.2f)", F);
        r.clear();
        CHECK(r.fps(t) == 0.0 && r.frames == 0, "clear: a new focus starts at nothing");
    }
    // A 92 FPS game toplevel.
    {
        SWindowRate r;
        const auto  G = periodOf(92);
        for (int i = 0; i <= 184; ++i)
            r.tick(0x1000, t + i * G);
        CHECK(std::abs(r.fps(t + 184 * G) - 92.0) < 0.5, "game: 92 (%.2f)", r.fps(t + 184 * G));
    }
    // What a frame is (negative controls: state-only commits, an unmap, a damage-less re-attach).
    CHECK(isFrame(true, true, true), "new buffer with damage: a frame");
    CHECK(!isFrame(false, true, true), "no new buffer: not a frame");
    CHECK(!isFrame(true, false, false), "null buffer (unmap): not a frame");
    CHECK(!isFrame(true, true, false), "buffer without damage: not a frame");

    if (g_fail) {
        std::printf("%d failure(s)\n", g_fail);
        return 1;
    }
    std::printf("mios-pacing gate: all tests passed\n");
    return 0;
}
