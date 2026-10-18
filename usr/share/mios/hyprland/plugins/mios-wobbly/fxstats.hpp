// AI-hint: Frame-pacing counters shared by the wobble and the lamp (header-only, inline globals, so main.cpp and
// lamp.cpp see one table). Per effect and per output: frames rendered, mesh draws, DISTINCT mesh states drawn, and
// for every draw the wall-clock interval since the previous draw on that output next to how far the drawn model
// advanced in that interval. A paced effect advances its model by exactly the frame interval (pacing_error ~0,
// distinct_per_s == the output refresh while it moves); the old fixed-substep wobble advanced 4.2/8.3 ms per 6.9 ms
// frame at 144 Hz. Read with `hyprctl mios-lamp stats` (`stats reset` clears). Never touches window state.
// AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/main.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/lamp.cpp, tests/test-mios-lamp.py
#pragma once

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <map>
#include <string>
#include <vector>

namespace MiosFxStats {
    enum eEffect : int {
        WOBBLE = 0,
        LAMP   = 1,
    };
    inline constexpr const char* EFFECT_NAMES[2] = {"wobble", "lamp"};
    inline constexpr size_t      MAX_SAMPLES     = 8192;

    struct SMon {
        double              refreshHz = 0;
        uint64_t            frames = 0, draws = 0, distinct = 0;
        uint64_t            lastHash = 0;
        double              firstMs = -1, lastMs = 0, prevDrawMs = -1, prevModelMs = -1;
        std::vector<double> frameDt, modelDt, atMs;
    };

    // Every frame any output renders (effect or not): the compositor's own rate, the ceiling for the effects.
    struct SOut {
        double              refreshHz = 0;
        uint64_t            frames    = 0;
        double              firstMs = -1, lastMs = 0;
        std::vector<double> dt;
    };

    inline std::map<std::string, SMon> g_mons[2];
    inline std::map<std::string, SOut> g_outputs;

    inline double nowMs() {
        return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
    }

    inline uint64_t fnv(const void* data, size_t n, uint64_t h = 1469598103934665603ULL) {
        const auto* p = static_cast<const unsigned char*>(data);
        for (size_t i = 0; i < n; ++i) {
            h ^= p[i];
            h *= 1099511628211ULL;
        }
        return h;
    }

    // render.pre of any frame on `mon`.
    inline void output(const std::string& mon, double hz, double atMs) {
        auto& o     = g_outputs[mon];
        o.refreshHz = hz;
        if (o.firstMs >= 0 && o.dt.size() < MAX_SAMPLES)
            o.dt.push_back(atMs - o.lastMs);
        if (o.firstMs < 0)
            o.firstMs = atMs;
        o.lastMs = atMs;
        ++o.frames;
    }

    // One frame started on `mon` while the effect had something attached.
    inline void frame(int fx, const std::string& mon, double hz) {
        auto& m     = g_mons[fx & 1][mon];
        m.refreshHz = hz;
        ++m.frames;
    }

    // One mesh draw on `mon`: hash of the drawn mesh, the draw's wall-clock time and the model time it shows.
    inline void draw(int fx, const std::string& mon, double hz, uint64_t hash, double atMs, double modelMs) {
        auto& m     = g_mons[fx & 1][mon];
        m.refreshHz = hz;
        if (m.firstMs < 0)
            m.firstMs = atMs;
        m.lastMs = atMs;
        ++m.draws;
        if (m.draws == 1 || hash != m.lastHash)
            ++m.distinct;
        m.lastHash = hash;
        if (m.prevDrawMs >= 0 && m.frameDt.size() < MAX_SAMPLES) {
            m.frameDt.push_back(atMs - m.prevDrawMs);
            m.modelDt.push_back(modelMs - m.prevModelMs);
            m.atMs.push_back(atMs);
        }
        m.prevDrawMs  = atMs;
        m.prevModelMs = modelMs;
    }

    inline void reset() {
        g_mons[0].clear();
        g_mons[1].clear();
        g_outputs.clear();
    }

    // `stats raw`: per draw [ms since the first draw, frame dt, model dt] for each effect and output.
    inline std::string rawJson() {
        std::string out = "{";
        for (int fx = 0; fx < 2; ++fx) {
            out += std::string(fx ? "," : "") + "\"" + EFFECT_NAMES[fx] + "\":{";
            bool first = true;
            for (const auto& [name, m] : g_mons[fx]) {
                out += std::string(first ? "" : ",") + "\"" + name + "\":[";
                for (size_t i = 0; i < m.frameDt.size(); ++i) {
                    char buf[96];
                    std::snprintf(buf, sizeof(buf), "%s[%.3f,%.3f,%.3f]", i ? "," : "", m.atMs[i] - m.firstMs, m.frameDt[i], m.modelDt[i]);
                    out += buf;
                }
                out += "]";
                first = false;
            }
            out += "}";
        }
        return out + "}";
    }

    inline double pct(std::vector<double> v, double p) {
        if (v.empty())
            return 0;
        std::sort(v.begin(), v.end());
        const size_t i = std::min(v.size() - 1, (size_t)std::llround(p * (v.size() - 1)));
        return v[i];
    }

    inline std::string json() {
        std::string out = "{\"outputs\":{";
        bool        firstOut = true;
        for (const auto& [name, o] : g_outputs) {
            const double secs = (o.lastMs - o.firstMs) / 1000.0;
            char         buf[256];
            std::snprintf(buf, sizeof(buf), "%s\"%s\":{\"refresh_hz\":%.3f,\"frames\":%llu,\"seconds\":%.4f,\"frames_per_s\":%.2f,\"dt_ms_p50\":%.4f,\"dt_ms_p95\":%.4f}",
                          firstOut ? "" : ",", name.c_str(), o.refreshHz, (unsigned long long)o.frames, secs, secs > 0 ? (o.frames - 1) / secs : 0.0, pct(o.dt, 0.5), pct(o.dt, 0.95));
            out += buf;
            firstOut = false;
        }
        out += "},";
        for (int fx = 0; fx < 2; ++fx) {
            out += std::string(fx ? "," : "") + "\"" + EFFECT_NAMES[fx] + "\":{";
            bool first = true;
            for (const auto& [name, m] : g_mons[fx]) {
                const double secs = m.draws > 1 ? (m.lastMs - m.firstMs) / 1000.0 : 0.0;
                double       errSum = 0, mean = 0;
                size_t       uniform = 0, n = 0;
                for (size_t i = 0; i < m.frameDt.size(); ++i) {
                    const double f = m.frameDt[i], d = m.modelDt[i];
                    if (!(f > 0.05))
                        continue; // two draws in one frame (content + blur matte): not a frame step
                    ++n;
                    mean += f;
                    errSum += std::abs(d - f) / f;
                    if (std::abs(d - f) <= 0.1 * f)
                        ++uniform;
                }
                std::vector<double> steps;
                for (size_t i = 0; i < m.frameDt.size(); ++i)
                    if (m.frameDt[i] > 0.05)
                        steps.push_back(m.modelDt[i]);
                char buf[1024];
                std::snprintf(buf, sizeof(buf),
                              "%s\"%s\":{\"refresh_hz\":%.3f,\"frames\":%llu,\"draws\":%llu,\"distinct\":%llu,\"seconds\":%.4f,\"draws_per_s\":%.2f,"
                              "\"distinct_per_s\":%.2f,\"frame_dt_ms\":%.4f,\"model_dt_ms_p05\":%.4f,\"model_dt_ms_p50\":%.4f,\"model_dt_ms_p95\":%.4f,"
                              "\"pacing_error\":%.4f,\"uniform_fraction\":%.4f,\"samples\":%zu}",
                              first ? "" : ",", name.c_str(), m.refreshHz, (unsigned long long)m.frames, (unsigned long long)m.draws, (unsigned long long)m.distinct, secs,
                              secs > 0 ? (m.draws - 1) / secs : 0.0, secs > 0 ? (m.distinct - 1) / secs : 0.0, n ? mean / n : 0.0, pct(steps, 0.05), pct(steps, 0.5),
                              pct(steps, 0.95), n ? errSum / n : 0.0, n ? (double)uniform / n : 0.0, n);
                out += buf;
                first = false;
            }
            out += "}";
        }
        return out + "}";
    }
}
