// AI-hint: Host tests for the toplevel-capture guard's decision (capture_guard.hpp): a mapped window with a live output
//          is captured; a closed, unmapped or output-less window is refused (the SEGV and client-kill paths); with the
//          guard off every capture passes through (the negative control: stock Hyprland behaviour).
// AI-related: usr/share/mios/hyprland/plugins/mios-pacing/capture_guard.hpp, usr/share/mios/hyprland/plugins/mios-pacing/Makefile
#include "../capture_guard.hpp"

#include <cstdio>
#include <initializer_list>

using namespace MiosCaptureGuard;

static int g_fail = 0;
#define CHECK(c, msg)                                                   \
    do {                                                                \
        if (!(c)) {                                                     \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, msg);   \
            ++g_fail;                                                   \
        }                                                               \
    } while (0)

int main() {
    const SWindowFacts live{.exists = true, .mapped = true, .hasMonitor = true};
    const SWindowFacts closed{};
    const SWindowFacts unmapped{.exists = true, .mapped = false, .hasMonitor = true};
    const SWindowFacts orphan{.exists = true, .mapped = true, .hasMonitor = false}; // pinned across a removed output
    CHECK(verdict(live, true) == eVerdict::PASS, "a live window is captured");
    CHECK(verdict(closed, true) == eVerdict::FAIL_NONE, "a closed window is refused");
    CHECK(verdict(unmapped, true) == eVerdict::FAIL_UNMAPPED, "an unmapped window is refused");
    CHECK(verdict(orphan, true) == eVerdict::FAIL_NO_OUTPUT, "an output-less window is refused (the SEGV path)");
    // negative control: guard off = stock behaviour, every capture reaches Hyprland
    for (const auto& w : {live, closed, unmapped, orphan})
        CHECK(verdict(w, false) == eVerdict::PASS, "guard off passes everything");
    static_assert(verdict(SWindowFacts{.exists = true, .mapped = true, .hasMonitor = false}, true) == eVerdict::FAIL_NO_OUTPUT);
    std::printf("%s capture_guard_test (%d failures)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}
