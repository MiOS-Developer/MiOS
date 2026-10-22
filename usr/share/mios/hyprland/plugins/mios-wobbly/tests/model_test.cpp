// AI-hint: Host test for the mios-wobbly model (no compositor SDK): `make -C usr/share/mios/hyprland/plugins/mios-wobbly test`.
// It simulates a title-bar drag rendered at 60/144/165/240 Hz with render-start jitter and compares
// the deformation each frame shows against a fine-step (1/9600 s) reference of the same model.
// Judder detector ("beat"): per frame, the rms over nodes of the SECOND difference (frame to frame)
// of the error vs the reference; the score is its 75th percentile over the drag. A smoothly
// growing discretisation error scores ~0; a recurring per-frame alternation such as the old
// 1,2,2 substep pattern at 144 Hz scores high. The percentile ignores the few single frames where
// a node first hits / leaves max_stretch (a hard limit by design, one event, not a cadence).
// POSITIVE: exact-dt stepping (MiosWobble::advance) has no beat at 60/144/165/240 Hz in a gentle
// drag (mesh inside max_stretch) and tracks the reference there; in a fast flick (mesh pinned at
// max_stretch, ~40 px per 60 Hz frame, where first-order integration error itself moves frame to
// frame) it beats at most a quarter of the old stepping at each rate, and never more at 60 Hz.
// NEGATIVE control: the previous fixed 1/240 s accumulator re-run on the same input IS flagged by
// the same detector at 144 Hz, so the detector is not blind.
// Also: frame-rate invariance, a dropped frame, release-and-settle, and non-finite/zero dt.
// AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/wobble_model.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/main.cpp
#include "../wobble_model.hpp"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <functional>
#include <vector>

namespace {
    struct Vec {
        double x = 0, y = 0;
        Vec    operator+(const Vec& o) const { return {x + o.x, y + o.y}; }
        Vec    operator-(const Vec& o) const { return {x - o.x, y - o.y}; }
        Vec    operator*(double s) const { return {x * s, y * s}; }
        double size() const { return std::sqrt(x * x + y * y); }
    };
    struct Box {
        double x = 0, y = 0, w = 0, h = 0;
    };
    using MiosWobble::N;
    struct Model {
        Vec P[N][N], V[N][N];
    };

    int failures = 0;
    void check(bool ok, const char* what, double got, double limit) {
        std::printf("%s %-64s got %9.4f limit %9.4f\n", ok ? "PASS" : "FAIL", what, got, limit);
        if (!ok)
            ++failures;
    }

    const MiosWobble::SParams PRM{520.f, 40.f, 11.f, 0.3f}; // [desktop.hyprland.mios-wobbly.config]
    constexpr int PIN_I = N / 2, PIN_J = 0;                    // grabbed at the title bar centre

    // Drag speed scale: 1 = fast flick (1800 px/s, mesh held at max_stretch), 1/3 = gentle drag.
    double g_speed = 1.0;
    // Window position at time t: a drag that accelerates, cruises, reverses, then is released at 0.9 s.
    Box boxAt(double t) {
        double x = 200;
        if (t < 0.2)
            x += 0.5 * 9000 * t * t; // accelerate to 1800 px/s
        else if (t < 0.5)
            x += 180 + 1800 * (t - 0.2);
        else if (t < 0.9)
            x += 720 - 1200 * (t - 0.5); // reverse at 1200 px/s
        else
            x += 240;
        return {200 + (x - 200) * g_speed, 150 + 40 * g_speed * std::sin(std::min(t, 0.9) * 7.0), 900, 600};
    }
    bool dragging(double t) {
        return t < 0.9;
    }

    // Deterministic render-start jitter (+-0.35 ms) around the vblank grid.
    std::vector<double> frameTimes(double hz, double duration, bool jitter, double dropAt = -1) {
        std::vector<double> out;
        unsigned            seed = 12345;
        for (int f = 1; f / hz <= duration; ++f) {
            double t = f / hz;
            if (jitter) { // drawn for every vblank, so a stalled run keeps the same times after the stall
                seed = seed * 1103515245u + 12345u;
                t += (((seed >> 8) & 0xffff) / 65535.0 - 0.5) * 0.0007;
            }
            if (dropAt > 0 && f / hz > dropAt && f / hz < dropAt + 0.045)
                continue; // a 45 ms stall
            out.push_back(t);
        }
        return out;
    }

    // Deformation of every node relative to the window box at time t (what transform() draws).
    void deformation(const Model& m, const Box& b, Vec (&d)[N][N]) {
        for (int i = 0; i < N; ++i)
            for (int j = 0; j < N; ++j)
                d[i][j] = m.P[i][j] - MiosWobble::target<Vec>(b, i, j);
    }

    struct Run {
        std::vector<double> t;
        std::vector<Model>  m;
    };

    using Stepper = std::function<void(Model&, const Box& from, const Box& to, int pinI, int pinJ, float dt, double& accum)>;

    Run simulate(const std::vector<double>& frames, const Stepper& step) {
        Run   r;
        Model m;
        MiosWobble::settle(m.P, m.V, boxAt(0));
        double last = 0, accum = 0;
        Box    prev = boxAt(0);
        for (double t : frames) {
            const Box  b   = boxAt(t);
            const bool pin = dragging(t);
            step(m, prev, b, pin ? PIN_I : -1, pin ? PIN_J : -1, (float)std::min(t - last, 0.05), accum);
            prev = b;
            last = t;
            r.t.push_back(t);
            r.m.push_back(m);
        }
        return r;
    }

    // NEW: what main.cpp now does.
    void exactStep(Model& m, const Box& from, const Box& to, int pi, int pj, float dt, double&) {
        MiosWobble::advance(m.P, m.V, from, to, pi, pj, PRM, dt);
    }
    // OLD (negative control): fixed 1/240 s steps from an accumulator, target = this frame's box.
    void accumulatorStep(Model& m, const Box&, const Box& to, int pi, int pj, float dt, double& accum) {
        accum += dt;
        while (accum >= MiosWobble::MAX_STEP) {
            MiosWobble::integrate(m.P, m.V, to, pi, pj, PRM, MiosWobble::MAX_STEP);
            accum -= MiosWobble::MAX_STEP;
        }
    }

    // Fine reference: the same model integrated at 1/9600 s with the box sampled continuously.
    Model referenceAt(double t) {
        static Model  m;
        static double tRef = -1;
        if (tRef < 0 || t < tRef) {
            MiosWobble::settle(m.P, m.V, boxAt(0));
            tRef = 0;
        }
        const double h = 1.0 / 9600;
        while (tRef + h <= t + 1e-12) {
            const double tn  = tRef + h;
            const bool   pin = dragging(tn);
            MiosWobble::integrate(m.P, m.V, boxAt(tn), pin ? PIN_I : -1, pin ? PIN_J : -1, PRM, (float)h);
            tRef = tn;
        }
        Model out = m;
        if (t - tRef > 1e-9) {
            const bool pin = dragging(t);
            MiosWobble::integrate(out.P, out.V, boxAt(t), pin ? PIN_I : -1, pin ? PIN_J : -1, PRM, (float)(t - tRef));
        }
        return out;
    }

    struct Err {
        double max = 0, beat = 0;
    };
    // max: worst node deformation error vs the reference (px) while dragging.
    // beat: 75th percentile over frames of rms_nodes(e[n+1] - 2 e[n] + e[n-1]), e = that error (px).
    Err compare(const Run& r) {
        Err                 e;
        std::vector<Vec>    prev2, prev1;
        std::vector<double> beats;
        for (size_t f = 0; f < r.t.size(); ++f) {
            if (r.t[f] < 0.05 || r.t[f] > 0.88)
                continue;
            const Box        b   = boxAt(r.t[f]);
            Model            ref = referenceAt(r.t[f]);
            Vec              d[N][N], dr[N][N];
            std::vector<Vec> err;
            deformation(r.m[f], b, d);
            deformation(ref, b, dr);
            for (int i = 0; i < N; ++i)
                for (int j = 0; j < N; ++j) {
                    err.push_back(d[i][j] - dr[i][j]);
                    e.max = std::max(e.max, err.back().size());
                }
            if (!prev2.empty()) {
                double sum = 0;
                for (size_t k = 0; k < err.size(); ++k) {
                    const double s = (err[k] - prev1[k] * 2.0 + prev2[k]).size();
                    sum += s * s;
                }
                beats.push_back(std::sqrt(sum / err.size()));
            }
            prev2 = prev1;
            prev1 = err;
        }
        if (!beats.empty()) {
            std::sort(beats.begin(), beats.end());
            e.beat = beats[beats.size() * 3 / 4];
        }
        return e;
    }

    double maxDeformation(const Run& r) {
        double worst = 0;
        for (size_t f = 0; f < r.t.size(); ++f) {
            Vec d[N][N];
            deformation(r.m[f], boxAt(r.t[f]), d);
            for (int i = 0; i < N; ++i)
                for (int j = 0; j < N; ++j)
                    worst = std::max(worst, d[i][j].size());
        }
        return worst;
    }
}

int main() {
    char what[128];
    for (double speed : {1.0, 1.0 / 3}) {
        g_speed           = speed;
        const char* label = speed == 1.0 ? "flick" : "gentle";
        // Sanity: the drag actually deforms the mesh (otherwise every comparison below is vacuous).
        const auto base = simulate(frameTimes(144, 0.9, false), exactStep);
        std::snprintf(what, sizeof what, "%-6s: drag deforms the mesh (px)", label);
        check(maxDeformation(base) > 20, what, maxDeformation(base), 20);

        // POSITIVE: exact-dt stepping has no beat at any common refresh rate (render jitter on).
        for (double hz : {60.0, 144.0, 165.0, 240.0}) {
            const Err e   = compare(simulate(frameTimes(hz, 0.9, true), exactStep));
            const Err acc = compare(simulate(frameTimes(hz, 0.9, true), accumulatorStep));
            if (speed != 1.0) { // inside max_stretch the model is linear: no beat, tracks the reference
                std::snprintf(what, sizeof what, "%-6s exact %3.0f Hz: beat (px)", label, hz);
                check(e.beat < 0.1, what, e.beat, 0.1);
                std::snprintf(what, sizeof what, "%-6s exact %3.0f Hz: worst error vs reference (px)", label, hz);
                check(e.max < 2.5, what, e.max, 2.5);
            } else if (hz == 60.0) { // 4 x 1/240 s either way: never worse than the old stepping
                std::snprintf(what, sizeof what, "%-6s exact %3.0f Hz: beat (px) <= old %.3f", label, hz, acc.beat);
                check(e.beat <= acc.beat * 1.05 + 1e-6, what, e.beat, acc.beat * 1.05);
            } else {
                std::snprintf(what, sizeof what, "%-6s exact %3.0f Hz: beat (px) <= old/4 (old %.3f)", label, hz, acc.beat);
                check(e.beat < 0.5 && e.beat <= acc.beat / 4, what, e.beat, std::min(0.5, acc.beat / 4));
            }
        }

        // NEGATIVE control: the old accumulator on the same 144 Hz input is flagged by the same detector.
        const Err old = compare(simulate(frameTimes(144, 0.9, true), accumulatorStep));
        std::snprintf(what, sizeof what, "%-6s accum 144 Hz: old stepping IS flagged (beat px)", label);
        check(old.beat > 0.3, what, old.beat, 0.3);
    }
    g_speed = 1.0;

    // Frame-rate invariance (gentle drag): 60 Hz and 144 Hz show the same deformation at shared
    // instants (every 1/12 s). Negative control: the old stepping differs more between the rates.
    {
        g_speed      = 1.0 / 3;
        auto between = [](const Stepper& step, int& shared) {
            const auto a     = simulate(frameTimes(60, 0.9, false), step);
            const auto b     = simulate(frameTimes(144, 0.9, false), step);
            double     worst = 0;
            shared           = 0;
            for (size_t i = 0; i < a.t.size(); ++i)
                for (size_t j = 0; j < b.t.size(); ++j)
                    if (std::abs(a.t[i] - b.t[j]) < 1e-9 && a.t[i] > 0.05) {
                        ++shared;
                        Vec da[N][N], db[N][N];
                        deformation(a.m[i], boxAt(a.t[i]), da);
                        deformation(b.m[j], boxAt(b.t[j]), db);
                        for (int x = 0; x < N; ++x)
                            for (int y = 0; y < N; ++y)
                                worst = std::max(worst, (da[x][y] - db[x][y]).size());
                    }
            return worst;
        };
        int          sharedNew = 0, sharedOld = 0;
        const double fresh = between(exactStep, sharedNew), old = between(accumulatorStep, sharedOld);
        check(sharedNew >= 8, "invariance: shared 60/144 Hz instants exist (not vacuous)", sharedNew, 8);
        check(fresh < 1.0, "exact: 60 Hz vs 144 Hz deformation at shared instants (px)", fresh, 1.0);
        check(old > fresh * 2, "accum: old stepping differs more between 60 and 144 Hz (px)", old, fresh * 2);
        g_speed = 1.0;
    }

    // A 45 ms stall mid-drag: the model stays bounded (no blow-up) and recovers to the reference.
    {
        const auto r = simulate(frameTimes(144, 0.9, true, 0.4), exactStep);
        check(maxDeformation(r) <= 0.3 * 600 + 1e-3, "exact: 45 ms stall keeps deformation within max_stretch", maxDeformation(r), 0.3 * 600);
        // Deviation from the no-stall run: right after the stall (0.445-0.47 s) vs 0.35 s later.
        double     early = 0, late = 0;
        int        shared = 0;
        const auto ok     = simulate(frameTimes(144, 0.9, true), exactStep);
        for (size_t i = 0; i < r.t.size(); ++i)
            for (size_t j = 0; j < ok.t.size(); ++j) {
                const double t = r.t[i];
                if (t != ok.t[j] || !((t > 0.445 && t < 0.47) || (t > 0.8 && t < 0.88)))
                    continue;
                ++shared;
                Vec a[N][N], b[N][N];
                deformation(r.m[i], boxAt(t), a);
                deformation(ok.m[j], boxAt(t), b);
                double worst = 0;
                for (int x = 0; x < N; ++x)
                    for (int y = 0; y < N; ++y)
                        worst = std::max(worst, (a[x][y] - b[x][y]).size());
                (t < 0.5 ? early : late) = std::max(t < 0.5 ? early : late, worst);
            }
        check(shared > 8 && early > 1.0, "exact: stall perturbs the run (early deviation, not vacuous)", early, 1.0);
        check(late < 1.0 && late < early / 4, "exact: 45 ms stall deviation decays (px, 0.35 s later)", late, std::min(1.0, early / 4));
    }

    // Release and settle: after the drag ends the mesh comes to rest within 1.5 s (the plugin's
    // detach condition dev < 0.3 px, vel < 4 px/s).
    {
        const auto r      = simulate(frameTimes(144, 2.4, true), exactStep);
        double     settle = -1;
        for (size_t f = 0; f < r.t.size(); ++f) {
            if (r.t[f] < 0.9)
                continue;
            Vec    d[N][N];
            double dev = 0, vel = 0;
            deformation(r.m[f], boxAt(r.t[f]), d);
            for (int i = 0; i < N; ++i)
                for (int j = 0; j < N; ++j) {
                    dev = std::max(dev, d[i][j].size());
                    vel = std::max(vel, r.m[f].V[i][j].size());
                }
            if (dev < 0.3 && vel < 4.0) {
                settle = r.t[f] - 0.9;
                break;
            }
        }
        check(settle > 0 && settle < 1.5, "exact: settles after release (s)", settle, 1.5);
    }

    // Degenerate dt: zero, negative and NaN leave the model untouched and report 0 substeps; a huge
    // dt is capped at MAX_SUBSTEPS (the plugin clamps dt to 50 ms before this anyway).
    {
        Model m;
        MiosWobble::settle(m.P, m.V, boxAt(0));
        m.V[1][1]         = {50, 0};
        const Model saved = m;
        int         n0 = MiosWobble::advance(m.P, m.V, boxAt(0), boxAt(0), -1, -1, PRM, 0.f);
        int         n1 = MiosWobble::advance(m.P, m.V, boxAt(0), boxAt(0), -1, -1, PRM, -1.f);
        int         n2 = MiosWobble::advance(m.P, m.V, boxAt(0), boxAt(0), -1, -1, PRM, std::nanf(""));
        const bool  same = m.P[1][1].x == saved.P[1][1].x && m.V[1][1].x == saved.V[1][1].x;
        check(n0 == 0 && n1 == 0 && n2 == 0 && same, "degenerate dt is a no-op (substeps returned)", n0 + n1 + n2, 0);
        const int nBig = MiosWobble::advance(m.P, m.V, boxAt(0), boxAt(0), -1, -1, PRM, 10.f);
        check(nBig == MiosWobble::MAX_SUBSTEPS, "huge dt capped at MAX_SUBSTEPS", nBig, MiosWobble::MAX_SUBSTEPS);
        const int n144 = MiosWobble::advance(m.P, m.V, boxAt(0), boxAt(0), -1, -1, PRM, 1.f / 144);
        const int n60  = MiosWobble::advance(m.P, m.V, boxAt(0), boxAt(0), -1, -1, PRM, 1.f / 60);
        check(n144 == 2 && n60 == 4, "substeps: 144 Hz -> 2, 60 Hz -> 4", n144 * 10 + n60, 24);
    }

    std::printf("%s (%d failure%s)\n", failures ? "FAILED" : "OK", failures, failures == 1 ? "" : "s");
    return failures ? 1 : 0;
}
