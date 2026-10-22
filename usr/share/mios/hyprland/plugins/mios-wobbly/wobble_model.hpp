// AI-hint: mios-wobbly spring-mass model, kept free of Hyprland types so tests/model_test.cpp can
// build it on the host. main.cpp instantiates it with Vector2D/CBox; the test uses plain structs.
// Frame pacing: advance() covers each frame's real dt exactly (equal substeps of at most MAX_STEP)
// and moves the target box linearly across those substeps. The earlier fixed 1/240 s accumulator
// stepped 1,2,2,1,2,2... substeps per frame on a 144 Hz output (240/144 is not an integer), so the
// mesh advanced 4.2, 8.3, 8.3 ms per 6.9 ms frame while the window itself moved in real time: a
// 48 Hz beat in the deformation that read as a low frame rate.
// AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/main.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/tests/model_test.cpp
#pragma once

#include <algorithm>
#include <cmath>

namespace MiosWobble {
    constexpr int   N        = 5;            // control points per axis (degree-4 Bezier patch)
    constexpr float MAX_STEP = 1.0f / 240.f; // longest physics substep (stability bound, not a rate)
    constexpr int   MAX_SUBSTEPS = 64;
    // Velocity decay rate (1/s) while a node is held at max_stretch. ln(2) * 240: exactly the old
    // "halve the velocity every clamped 1/240 s substep", expressed per second so the clamp no
    // longer depends on how many substeps a frame happens to get.
    constexpr float CLAMP_DAMPING = 166.3553f;

    struct SParams {
        float springK = 520.f, anchorK = 40.f, friction = 11.f, maxStretch = 0.3f;
    };

    inline float bernstein(int i, float t) {
        static constexpr float C[N] = {1, 4, 6, 4, 1};
        return C[i] * std::pow(t, (float)i) * std::pow(1.f - t, (float)(N - 1 - i));
    }

    template <class Vec, class Box>
    Vec target(const Box& b, int i, int j) {
        return Vec{b.x + b.w * i / (N - 1.0), b.y + b.h * j / (N - 1.0)};
    }

    template <class Box>
    Box lerpBox(const Box& a, const Box& b, double t) {
        return Box{a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.w + (b.w - a.w) * t, a.h + (b.h - a.h) * t};
    }

    template <class Vec>
    Vec patch(const Vec (&P)[N][N], float u, float v) {
        float bu[N], bv[N];
        for (int i = 0; i < N; ++i) {
            bu[i] = bernstein(i, u);
            bv[i] = bernstein(i, v);
        }
        Vec out{0.0, 0.0};
        for (int i = 0; i < N; ++i)
            for (int j = 0; j < N; ++j)
                out = out + P[i][j] * (double)(bu[i] * bv[j]);
        return out;
    }

    template <class Vec, class Box>
    void settle(Vec (&P)[N][N], Vec (&V)[N][N], const Box& box) {
        for (int i = 0; i < N; ++i)
            for (int j = 0; j < N; ++j) {
                P[i][j] = target<Vec>(box, i, j);
                V[i][j] = Vec{0.0, 0.0};
            }
    }

    // One semi-implicit Euler substep of length h toward `box` (the pinned node sits on its target).
    template <class Vec, class Box>
    void integrate(Vec (&P)[N][N], Vec (&V)[N][N], const Box& box, int pinI, int pinJ, const SParams& prm, float h) {
        const float k = std::clamp(prm.springK, 10.f, 3000.f), ka = std::clamp(prm.anchorK, 1.f, 1000.f), fr = std::clamp(prm.friction, 0.5f, 100.f);
        Vec T[N][N], nv[N][N];
        for (int i = 0; i < N; ++i)
            for (int j = 0; j < N; ++j)
                T[i][j] = target<Vec>(box, i, j);
        static constexpr int   NB[8][2] = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}, {1, 1}, {1, -1}, {-1, 1}, {-1, -1}};
        static constexpr float NW[8]    = {1, 1, 1, 1, 0.5f, 0.5f, 0.5f, 0.5f};
        for (int i = 0; i < N; ++i)
            for (int j = 0; j < N; ++j) {
                if (i == pinI && j == pinJ) {
                    nv[i][j] = Vec{0.0, 0.0};
                    continue;
                }
                Vec F = (T[i][j] - P[i][j]) * (double)ka;
                for (int n = 0; n < 8; ++n) {
                    const int a = i + NB[n][0], b = j + NB[n][1];
                    if (a < 0 || b < 0 || a >= N || b >= N)
                        continue;
                    F = F + ((P[a][b] - P[i][j]) - (T[a][b] - T[i][j])) * (double)(k * NW[n]);
                }
                F        = F - V[i][j] * (double)fr;
                nv[i][j] = V[i][j] + F * (double)h;
            }
        const double lim = std::clamp(prm.maxStretch, 0.05f, 1.f) * std::min(box.w, box.h);
        for (int i = 0; i < N; ++i)
            for (int j = 0; j < N; ++j) {
                if (i == pinI && j == pinJ) {
                    P[i][j] = T[i][j];
                    V[i][j] = Vec{0.0, 0.0};
                    continue;
                }
                V[i][j] = nv[i][j];
                P[i][j] = P[i][j] + nv[i][j] * (double)h;
                Vec dv  = P[i][j] - T[i][j];
                if (dv.size() > lim) {
                    P[i][j] = T[i][j] + dv * (lim / dv.size());
                    V[i][j] = V[i][j] * (double)std::exp(-CLAMP_DAMPING * h);
                }
            }
    }

    // Advance the model by exactly dt seconds while the window box moved from `from` to `to`:
    // ceil(dt / MAX_STEP) equal substeps, the target box interpolated across them. Returns the
    // substep count (0 for a non-positive or non-finite dt, which leaves the model untouched).
    template <class Vec, class Box>
    int advance(Vec (&P)[N][N], Vec (&V)[N][N], const Box& from, const Box& to, int pinI, int pinJ, const SParams& prm, float dt) {
        if (!(dt > 0.f) || !std::isfinite(dt))
            return 0;
        const int   n = std::clamp((int)std::ceil(dt / MAX_STEP - 1e-3f), 1, MAX_SUBSTEPS);
        const float h = dt / n;
        for (int k = 1; k <= n; ++k)
            integrate(P, V, lerpBox(from, to, (double)k / n), pinI, pinJ, prm, h);
        return n;
    }
}
