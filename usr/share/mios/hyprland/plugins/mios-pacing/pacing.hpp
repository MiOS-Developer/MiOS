// AI-hint: mios-pacing, pure part (no compositor headers): the per-output frame gate (phase-locked deadlines 1/cap
//          apart; a frame due within `slack` passes, a deferred frame keeps the deadline, an idle gap resyncs), the
//          rendered-frame meter, the focused window's committed-frame rate and the output exclusion list. Host-tested by
//          tests/pacing_test.cpp (`make test`).
// AI-related: usr/share/mios/hyprland/plugins/mios-pacing/main.cpp, usr/share/mios/hyprland/plugins/mios-pacing/tests/pacing_test.cpp
#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <string>
#include <string_view>
#include <unordered_map>

namespace MiosPacing {
    using Clock = std::chrono::steady_clock;

    // A 2D controller cursor is a lease, not a new global theme. Physical
    // motion yields its shape immediately; a later controller motion may
    // resume it while the owner still refreshes the lease.
    enum class ECursorEffect { None, Apply, Clear };
    struct SCursorLease {
        bool armed = false, overridden = false;
        std::string shape, device;
        Clock::time_point deadline{};
        struct SUpdate { bool valid; ECursorEffect effect; };

        ECursorEffect release() {
            const bool was = overridden;
            overridden = false;
            return was ? ECursorEffect::Clear : ECursorEffect::None;
        }
        ECursorEffect disable() {
            armed = false;
            return release();
        }
        SUpdate refresh(std::string_view nextShape, std::string_view nextDevice, int leaseMs, Clock::time_point now) {
            const bool validShape = !nextShape.empty() && nextShape.size() <= 64 && nextShape.find_first_not_of(
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") == std::string_view::npos;
            bool validDevice = !nextDevice.empty() && nextDevice.size() <= 128;
            for (const unsigned char c : nextDevice)
                validDevice = validDevice && c >= 32 && c < 127;
            if (!validShape || !validDevice || leaseMs < 1 || leaseMs > 10000)
                return {false, disable()};
            const auto effect = shape != nextShape || device != nextDevice ? release() : ECursorEffect::None;
            shape = nextShape;
            device = nextDevice;
            armed = true;
            deadline = now + std::chrono::milliseconds(leaseMs);
            return {true, effect};
        }
        ECursorEffect expire(Clock::time_point now) {
            return armed && now >= deadline ? disable() : ECursorEffect::None;
        }
        ECursorEffect motion(std::string_view source, Clock::time_point now) {
            if (!armed || now >= deadline)
                return disable();
            if (source != device)
                return release();
            if (overridden)
                return ECursorEffect::None;
            overridden = true;
            return ECursorEffect::Apply;
        }
    };

    struct SGate {
        Clock::time_point next{};
        bool              primed = false;
        void              reset() {
            primed = false;
        }
    };

    inline Clock::duration periodOf(int hz) {
        return std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>(1.0 / (hz > 0 ? hz : 1)));
    }

    // May the frame event at `now` render under a cap of `hz`? An admitted frame moves the deadline one period on
    // (phase-locked: no drift) or, after an idle gap of more than one period, resyncs to now + period. A deferred
    // frame leaves the deadline where it is: the caller reschedules a frame for `g.next`.
    inline bool admit(SGate& g, Clock::time_point now, int hz, Clock::duration slack) {
        if (hz <= 0)
            return true;
        const auto P = periodOf(hz);
        if (!g.primed) {
            g.primed = true;
            g.next   = now + P;
            return true;
        }
        if (now + slack < g.next)
            return false;
        g.next = (now - g.next < P) ? g.next + P : now + P;
        return true;
    }

    // Frames rendered in the last second.
    struct SMeter {
        std::deque<Clock::time_point> t;
        void                          tick(Clock::time_point now) {
            t.push_back(now);
            trim(now);
        }
        void trim(Clock::time_point now) {
            while (!t.empty() && now - t.front() > std::chrono::seconds(1))
                t.pop_front();
        }
        double fps(Clock::time_point now) {
            trim(now);
            if (t.size() < 2)
                return static_cast<double>(t.size());
            const double SPAN = std::chrono::duration<double>(t.back() - t.front()).count();
            return SPAN > 0 ? (t.size() - 1) / SPAN : 0.0;
        }
    };

    // A client commit is a frame when it attaches a buffer that carries damage (new content). A commit that only
    // moves state (input region, scale, an ack), detaches the buffer (unmap) or re-attaches a buffer with no damage
    // is not one.
    inline bool isFrame(bool bufferUpdated, bool hasBuffer, bool hasDamage) {
        return bufferUpdated && hasBuffer && hasDamage;
    }

    // The focused window's committed frame rate. One meter per surface of its tree (the toplevel and every
    // subsurface: a video player or a game may present on a subsurface while the toplevel stays idle); the window
    // rate is its fastest surface -- the rate at which the window's picture changes. Surfaces idle for more than a
    // second drop out.
    struct SWindowRate {
        std::unordered_map<std::uintptr_t, SMeter> surfaces;
        std::uint64_t                              frames = 0;
        void                                       tick(std::uintptr_t surface, Clock::time_point now) {
            surfaces[surface].tick(now);
            ++frames;
        }
        double fps(Clock::time_point now) {
            double best = 0.0;
            for (auto it = surfaces.begin(); it != surfaces.end();) {
                const double F = it->second.fps(now);
                if (it->second.t.empty()) {
                    it = surfaces.erase(it);
                    continue;
                }
                best = F > best ? F : best;
                ++it;
            }
            return best;
        }
        void clear() {
            surfaces.clear();
            frames = 0;
        }
    };

    // `csv` = "HEADLESS-,VIRT-": an output whose name starts with any entry is excluded.
    inline bool excluded(std::string_view name, std::string_view csv) {
        size_t start = 0;
        while (start <= csv.size()) {
            const size_t END   = csv.find(',', start);
            std::string_view p = csv.substr(start, END == std::string_view::npos ? std::string_view::npos : END - start);
            while (!p.empty() && p.front() == ' ')
                p.remove_prefix(1);
            while (!p.empty() && p.back() == ' ')
                p.remove_suffix(1);
            if (!p.empty() && name.starts_with(p))
                return true;
            if (END == std::string_view::npos)
                break;
            start = END + 1;
        }
        return false;
    }

    // `csv` = "mios-wallpaper,other": true when `name` equals one entry exactly (layer namespaces: a test probe such as
    // "mios-wallpaper-native-probe" is a different surface and is never throttled).
    inline bool listed(std::string_view name, std::string_view csv) {
        size_t start = 0;
        while (start <= csv.size()) {
            const size_t END   = csv.find(',', start);
            std::string_view p = csv.substr(start, END == std::string_view::npos ? std::string_view::npos : END - start);
            while (!p.empty() && p.front() == ' ')
                p.remove_prefix(1);
            while (!p.empty() && p.back() == ' ')
                p.remove_suffix(1);
            if (!p.empty() && name == p)
                return true;
            if (END == std::string_view::npos)
                break;
            start = END + 1;
        }
        return false;
    }

    // Why an output is not capped ("" = capped at `cap`).
    inline std::string_view uncappedReason(bool enabled, int cap, bool mirror, bool excludedName, bool requireVrr, bool vrrActive,
                                           bool newScheduling, double refresh) {
        if (!enabled)
            return "disabled";
        if (cap <= 0)
            return "no cap";
        if (mirror)
            return "mirror";
        if (excludedName)
            return "excluded";
        if (requireVrr && !vrrActive)
            return "no active VRR";
        if (newScheduling)
            return "render:new_render_scheduling";
        if (refresh > 0 && cap >= refresh - 0.5)
            return "cap at or above the refresh";
        return "";
    }
}
