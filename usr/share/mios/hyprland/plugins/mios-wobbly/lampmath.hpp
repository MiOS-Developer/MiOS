// AI-hint: Pure math + data model of the MiOS magic lamp (no Hyprland or GL dependency, unit-tested by
// tests/test-mios-lamp.py and usr/share/mios/hyprland/plugins/mios-wobbly/tests/lamp_test.cpp). Everything is in Hyprland layout
// coordinates (global logical px). The lamp funnels a window rect into a target rect (a dock icon): the window
// first bends into a tube whose sides follow a smoothstep from the window's width at its far end to the icon's
// width at the icon, then its content slides down the tube into the icon, near end first. Progress t = 0 is the
// window, t = 1 the icon; restore/open run the same mesh backwards. Also: the cubic-bezier easing used for the SSOT
// curve, the glass-pane fade profile, a small JSON reader and the dock contract
// (/home/mios/mios-desktop-work-1/lamp-contract.md: $XDG_RUNTIME_DIR/mios-dock-icons.json and mios-lamp-launch.json).
// AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/main.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/lamp.cpp
#pragma once

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace MiosLamp {

    struct SRect {
        double x = 0, y = 0, w = 0, h = 0;
        bool   valid() const {
            return std::isfinite(x) && std::isfinite(y) && std::isfinite(w) && std::isfinite(h) && w >= 1.0 && h >= 1.0 && std::abs(x) < 1e6 && std::abs(y) < 1e6 &&
                w < 1e6 && h < 1e6;
        }
        double cx() const {
            return x + w / 2.0;
        }
        double cy() const {
            return y + h / 2.0;
        }
    };

    enum class eEdge : uint8_t {
        BOTTOM = 0,
        TOP,
        LEFT,
        RIGHT,
    };

    inline std::optional<eEdge> edgeFromString(std::string_view s) {
        if (s == "bottom")
            return eEdge::BOTTOM;
        if (s == "top")
            return eEdge::TOP;
        if (s == "left")
            return eEdge::LEFT;
        if (s == "right")
            return eEdge::RIGHT;
        return std::nullopt;
    }

    inline const char* edgeName(eEdge e) {
        switch (e) {
            case eEdge::TOP: return "top";
            case eEdge::LEFT: return "left";
            case eEdge::RIGHT: return "right";
            default: return "bottom";
        }
    }

    inline double clamp01(double v) {
        return std::isfinite(v) ? std::clamp(v, 0.0, 1.0) : 0.0;
    }

    inline double smoothstep(double e0, double e1, double v) {
        if (e1 <= e0)
            return v >= e1 ? 1.0 : 0.0;
        const double t = clamp01((v - e0) / (e1 - e0));
        return t * t * (3.0 - 2.0 * t);
    }

    // CSS cubic-bezier(x1, y1, x2, y2) easing: maps elapsed fraction x in [0,1] to progress. x1/x2 are clamped
    // to [0,1] so the curve is a function of x; y may overshoot but the lamp clamps progress to [0,1].
    struct SBezier {
        double x1 = 0.42, y1 = 0.0, x2 = 0.58, y2 = 1.0;

        static SBezier make(double ax1, double ay1, double ax2, double ay2) {
            SBezier b;
            b.x1 = std::isfinite(ax1) ? std::clamp(ax1, 0.0, 1.0) : 0.42;
            b.x2 = std::isfinite(ax2) ? std::clamp(ax2, 0.0, 1.0) : 0.58;
            b.y1 = std::isfinite(ay1) ? std::clamp(ay1, -2.0, 3.0) : 0.0;
            b.y2 = std::isfinite(ay2) ? std::clamp(ay2, -2.0, 3.0) : 1.0;
            return b;
        }
        static double coord(double p, double a, double b) {
            const double q = 1.0 - p;
            return 3.0 * q * q * p * a + 3.0 * q * p * p * b + p * p * p;
        }
        static double slope(double p, double a, double b) {
            const double q = 1.0 - p;
            return 3.0 * q * q * a + 6.0 * q * p * (b - a) + 3.0 * p * p * (1.0 - b);
        }
        double operator()(double x) const {
            x = clamp01(x);
            if (x <= 0.0)
                return 0.0;
            if (x >= 1.0)
                return 1.0;
            double p = x;
            for (int i = 0; i < 8; ++i) { // Newton
                const double err = coord(p, x1, x2) - x;
                if (std::abs(err) < 1e-7)
                    return coord(p, y1, y2);
                const double d = slope(p, x1, x2);
                if (std::abs(d) < 1e-6)
                    break;
                p = std::clamp(p - err / d, 0.0, 1.0);
            }
            double lo = 0.0, hi = 1.0; // bisection fallback (flat spots)
            p = x;
            for (int i = 0; i < 40; ++i) {
                const double v = coord(p, x1, x2);
                if (std::abs(v - x) < 1e-7)
                    break;
                (v < x ? lo : hi) = p;
                p = (lo + hi) / 2.0;
            }
            return coord(p, y1, y2);
        }
    };

    // Alpha the window's glass pane is driven to while the lamp runs (hyprglass draws the pane rigidly at the real
    // window box, outside the deformed pass; its alpha is this ^ fade_gamma). The deformed content is multiplied
    // back by 1/paneFade so only the rigid pane disappears. 1 at rest, aMin once the shape starts to move.
    inline double paneFade(double t, double aMin) {
        aMin = std::isfinite(aMin) ? std::clamp(aMin, 0.05, 1.0) : 1.0;
        return 1.0 - (1.0 - aMin) * smoothstep(0.0, 0.18, clamp01(t));
    }

    // Fade the rigid hyprglass pane during wobble based on maximum mesh deviation (dev in px).
    // At rest (dev <= 0): 1.0. At/above threshold: minFade (0.4). Unity brightness preserved via uGain = 1.0 / paneFade.
    inline double wobblePaneFade(double dev, double minFade = 0.4, double thresh = 2.0) {
        minFade = std::isfinite(minFade) ? std::clamp(minFade, 0.05, 1.0) : 1.0;
        if (!std::isfinite(thresh) || thresh <= 0.0)
            thresh = 2.0;
        if (dev <= 0.0)
            return 1.0;
        const double t = clamp01(dev / thresh);
        return 1.0 - (1.0 - minFade) * smoothstep(0.0, 1.0, t);
    }

    // Opacity of the deformed window: it melts into the icon over the last stretch (and emerges from it).
    inline double contentAlpha(double t) {
        return 1.0 - smoothstep(0.86, 1.0, clamp01(t));
    }

    // Which way the funnel points: the dock edge when it agrees with the geometry, otherwise the side of the window
    // the target lies on (largest gap; overlapping rects use the dominant axis of the centre offset).
    inline eEdge chooseEdge(const SRect& win, const SRect& target, std::optional<eEdge> hint) {
        const double below = target.y - (win.y + win.h), above = win.y - (target.y + target.h);
        const double right = target.x - (win.x + win.w), left = win.x - (target.x + target.w);
        if (hint) {
            const double gap = *hint == eEdge::BOTTOM ? target.cy() - win.cy() :
                *hint == eEdge::TOP                   ? win.cy() - target.cy() :
                *hint == eEdge::RIGHT                 ? target.cx() - win.cx() :
                                                        win.cx() - target.cx();
            if (gap > 0)
                return *hint;
        }
        const double best = std::max({below, above, right, left});
        if (best > 0) {
            if (best == below)
                return eEdge::BOTTOM;
            if (best == above)
                return eEdge::TOP;
            if (best == right)
                return eEdge::RIGHT;
            return eEdge::LEFT;
        }
        const double dx = target.cx() - win.cx(), dy = target.cy() - win.cy();
        if (std::abs(dy) >= std::abs(dx))
            return dy >= 0 ? eEdge::BOTTOM : eEdge::TOP;
        return dx >= 0 ? eEdge::RIGHT : eEdge::LEFT;
    }

    // Canonical frame: the funnel always points to +y. toCanon/fromCanon are exact inverses.
    struct SPt {
        double x = 0, y = 0;
    };
    inline SPt toCanon(eEdge e, SPt p) {
        switch (e) {
            case eEdge::TOP: return {p.x, -p.y};
            case eEdge::RIGHT: return {p.y, p.x};
            case eEdge::LEFT: return {p.y, -p.x};
            default: return p;
        }
    }
    inline SPt fromCanon(eEdge e, SPt c) {
        switch (e) {
            case eEdge::TOP: return {c.x, -c.y};
            case eEdge::RIGHT: return {c.y, c.x};
            case eEdge::LEFT: return {-c.y, c.x};
            default: return c;
        }
    }
    inline SRect rectToCanon(eEdge e, const SRect& r) {
        const SPt    a = toCanon(e, {r.x, r.y}), b = toCanon(e, {r.x + r.w, r.y + r.h});
        const double x0 = std::min(a.x, b.x), x1 = std::max(a.x, b.x), y0 = std::min(a.y, b.y), y1 = std::max(a.y, b.y);
        return {x0, y0, x1 - x0, y1 - y0};
    }

    struct SVertex {
        double x = 0, y = 0; // destination, layout coordinates
        double u = 0, v = 0; // source point in the window rect, [0,1] (0,0 = top-left)
    };

    struct SMeshParams {
        int    cols = 8, rows = 32;
        double shapeEnd   = 0.4;  // t at which the sides finished bending into the tube
        double slideStart = 0.12; // t at which the content starts sliding down the tube
        double stretch    = 1.0;  // > 0: the near end reaches the icon first (1 = at half the slide)
    };

    // Mesh of (cols + 1) x (rows + 1) vertices, row-major from the window's far end; rows run along the funnel.
    // Returns false (and an empty mesh) for unusable input; the caller then draws the window untransformed.
    inline bool buildMesh(const SRect& win, const SRect& icon, eEdge edge, double t, const SMeshParams& prm, std::vector<SVertex>& out) {
        out.clear();
        if (!win.valid() || !icon.valid() || !std::isfinite(t))
            return false;
        const int cols = std::clamp(prm.cols, 1, 128), rows = std::clamp(prm.rows, 2, 256);
        t              = clamp01(t);
        const SRect  W = rectToCanon(edge, win), I = rectToCanon(edge, icon);
        const double ax0 = W.x, ax1 = W.x + W.w, ay0 = W.y, ay1 = W.y + W.h; // window: far end ay0, near end ay1
        const double bx0 = I.x, bx1 = I.x + I.w, by0 = I.y, by1 = I.y + I.h; // icon: entry by0
        const double shapeEnd = std::clamp(prm.shapeEnd, 0.05, 1.0), slideStart = std::clamp(prm.slideStart, 0.0, 0.95);
        const double c = smoothstep(0.0, shapeEnd, t);
        const double s = clamp01((t - slideStart) / (1.0 - slideStart));
        const double k = std::clamp(prm.stretch, 0.0, 4.0);
        const double tube = std::max(by0 - ay0, 1.0); // far end of the window to the icon entry

        std::vector<double> ys(rows + 1);
        for (int j = 0; j <= rows; ++j) {
            const double cv  = (double)j / rows;
            const double sv  = clamp01(s * (1.0 + k) - k * (1.0 - cv));
            const double yR  = ay0 + cv * (ay1 - ay0);
            const double yI  = by0 + cv * (by1 - by0);
            double       y   = yR + (yI - yR) * sv;
            ys[j]            = j > 0 ? std::max(y, ys[j - 1]) : y; // never fold (overlapping rects)
        }
        out.reserve((size_t)(cols + 1) * (rows + 1));
        for (int j = 0; j <= rows; ++j) {
            const double cv = (double)j / rows;
            const double y  = ys[j];
            const double g  = smoothstep(0.0, 1.0, clamp01((by0 - y) / tube));
            const double tl = bx0 + (ax0 - bx0) * g, tr = bx1 + (ax1 - bx1) * g;
            for (int i = 0; i <= cols; ++i) {
                const double cu = (double)i / cols;
                const double xR = ax0 + cu * (ax1 - ax0);
                const double xT = tl + cu * (tr - tl);
                const SPt    d  = fromCanon(edge, {xR + (xT - xR) * c, y});
                const SPt    sp = fromCanon(edge, {ax0 + cu * (ax1 - ax0), ay0 + cv * (ay1 - ay0)});
                SVertex      vx;
                vx.x = d.x;
                vx.y = d.y;
                vx.u = clamp01((sp.x - win.x) / win.w);
                vx.v = clamp01((sp.y - win.y) / win.h);
                out.push_back(vx);
            }
        }
        return true;
    }

    inline SRect meshBounds(const std::vector<SVertex>& m) {
        if (m.empty())
            return {};
        double x0 = m[0].x, y0 = m[0].y, x1 = x0, y1 = y0;
        for (const auto& v : m) {
            x0 = std::min(x0, v.x);
            y0 = std::min(y0, v.y);
            x1 = std::max(x1, v.x);
            y1 = std::max(y1, v.y);
        }
        return {x0, y0, x1 - x0, y1 - y0};
    }

    inline SRect unite(const SRect& a, const SRect& b) {
        if (!(a.w > 0 && a.h > 0))
            return b;
        if (!(b.w > 0 && b.h > 0))
            return a;
        const double x0 = std::min(a.x, b.x), y0 = std::min(a.y, b.y);
        const double x1 = std::max(a.x + a.w, b.x + b.w), y1 = std::max(a.y + a.h, b.y + b.h);
        return {x0, y0, x1 - x0, y1 - y0};
    }

    // ---- minimal JSON reader (contract files only; bounded depth and size) -------------------------------------

    struct SJson {
        enum class eType : uint8_t {
            NUL,
            BOOL,
            NUM,
            STR,
            ARR,
            OBJ
        } type = eType::NUL;
        bool                                       b   = false;
        double                                     num = 0;
        std::string                                str;
        std::vector<SJson>                         arr;
        std::vector<std::pair<std::string, SJson>> obj;

        const SJson* get(std::string_view k) const {
            if (type != eType::OBJ)
                return nullptr;
            for (const auto& [key, v] : obj)
                if (key == k)
                    return &v;
            return nullptr;
        }
        std::string string(std::string_view k) const {
            const auto* v = get(k);
            return v && v->type == eType::STR ? v->str : std::string{};
        }
        std::optional<double> number(std::string_view k) const {
            const auto* v = get(k);
            if (v && v->type == eType::NUM && std::isfinite(v->num))
                return v->num;
            return std::nullopt;
        }
        std::optional<bool> boolean(std::string_view k) const {
            const auto* v = get(k);
            if (v && v->type == eType::BOOL)
                return v->b;
            return std::nullopt;
        }
    };

    class CJsonReader {
      public:
        explicit CJsonReader(std::string_view s) : m_s(s) {}
        std::optional<SJson> parse() {
            if (m_s.size() > (1u << 20))
                return std::nullopt;
            SJson v;
            if (!value(v, 0))
                return std::nullopt;
            ws();
            if (m_i != m_s.size())
                return std::nullopt;
            return v;
        }

      private:
        std::string_view m_s;
        size_t           m_i = 0;

        void             ws() {
            while (m_i < m_s.size() && (m_s[m_i] == ' ' || m_s[m_i] == '\n' || m_s[m_i] == '\r' || m_s[m_i] == '\t'))
                ++m_i;
        }
        bool lit(std::string_view w) {
            if (m_s.substr(m_i, w.size()) != w)
                return false;
            m_i += w.size();
            return true;
        }
        static void utf8(std::string& o, uint32_t cp) {
            if (cp < 0x80)
                o += (char)cp;
            else if (cp < 0x800) {
                o += (char)(0xC0 | (cp >> 6));
                o += (char)(0x80 | (cp & 0x3F));
            } else if (cp < 0x10000) {
                o += (char)(0xE0 | (cp >> 12));
                o += (char)(0x80 | ((cp >> 6) & 0x3F));
                o += (char)(0x80 | (cp & 0x3F));
            } else {
                o += (char)(0xF0 | (cp >> 18));
                o += (char)(0x80 | ((cp >> 12) & 0x3F));
                o += (char)(0x80 | ((cp >> 6) & 0x3F));
                o += (char)(0x80 | (cp & 0x3F));
            }
        }
        bool hex4(uint32_t& out) {
            if (m_i + 4 > m_s.size())
                return false;
            out = 0;
            for (int k = 0; k < 4; ++k) {
                const char c = m_s[m_i++];
                out <<= 4;
                if (c >= '0' && c <= '9')
                    out |= (uint32_t)(c - '0');
                else if (c >= 'a' && c <= 'f')
                    out |= (uint32_t)(c - 'a' + 10);
                else if (c >= 'A' && c <= 'F')
                    out |= (uint32_t)(c - 'A' + 10);
                else
                    return false;
            }
            return true;
        }
        bool string(std::string& o) {
            if (m_i >= m_s.size() || m_s[m_i] != '"')
                return false;
            ++m_i;
            while (m_i < m_s.size()) {
                const char c = m_s[m_i++];
                if (c == '"')
                    return true;
                if ((unsigned char)c < 0x20)
                    return false;
                if (c != '\\') {
                    o += c;
                    continue;
                }
                if (m_i >= m_s.size())
                    return false;
                const char e = m_s[m_i++];
                switch (e) {
                    case '"': o += '"'; break;
                    case '\\': o += '\\'; break;
                    case '/': o += '/'; break;
                    case 'b': o += '\b'; break;
                    case 'f': o += '\f'; break;
                    case 'n': o += '\n'; break;
                    case 'r': o += '\r'; break;
                    case 't': o += '\t'; break;
                    case 'u': {
                        uint32_t cp = 0;
                        if (!hex4(cp))
                            return false;
                        if (cp >= 0xD800 && cp <= 0xDBFF && m_s.substr(m_i, 2) == "\\u") {
                            m_i += 2;
                            uint32_t lo = 0;
                            if (!hex4(lo))
                                return false;
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        }
                        utf8(o, cp);
                        break;
                    }
                    default: return false;
                }
            }
            return false;
        }
        bool value(SJson& v, int depth) {
            if (depth > 24)
                return false;
            ws();
            if (m_i >= m_s.size())
                return false;
            const char c = m_s[m_i];
            if (c == '{') {
                ++m_i;
                v.type = SJson::eType::OBJ;
                ws();
                if (m_i < m_s.size() && m_s[m_i] == '}') {
                    ++m_i;
                    return true;
                }
                while (true) {
                    ws();
                    std::string k;
                    if (!string(k))
                        return false;
                    ws();
                    if (m_i >= m_s.size() || m_s[m_i] != ':')
                        return false;
                    ++m_i;
                    SJson child;
                    if (!value(child, depth + 1))
                        return false;
                    v.obj.emplace_back(std::move(k), std::move(child));
                    ws();
                    if (m_i < m_s.size() && m_s[m_i] == ',') {
                        ++m_i;
                        continue;
                    }
                    if (m_i < m_s.size() && m_s[m_i] == '}') {
                        ++m_i;
                        return true;
                    }
                    return false;
                }
            }
            if (c == '[') {
                ++m_i;
                v.type = SJson::eType::ARR;
                ws();
                if (m_i < m_s.size() && m_s[m_i] == ']') {
                    ++m_i;
                    return true;
                }
                while (true) {
                    SJson child;
                    if (!value(child, depth + 1))
                        return false;
                    v.arr.push_back(std::move(child));
                    ws();
                    if (m_i < m_s.size() && m_s[m_i] == ',') {
                        ++m_i;
                        continue;
                    }
                    if (m_i < m_s.size() && m_s[m_i] == ']') {
                        ++m_i;
                        return true;
                    }
                    return false;
                }
            }
            if (c == '"') {
                v.type = SJson::eType::STR;
                return string(v.str);
            }
            if (lit("true")) {
                v.type = SJson::eType::BOOL;
                v.b    = true;
                return true;
            }
            if (lit("false")) {
                v.type = SJson::eType::BOOL;
                return true;
            }
            if (lit("null"))
                return true;
            const size_t start = m_i;
            while (m_i < m_s.size() && (std::isdigit((unsigned char)m_s[m_i]) || m_s[m_i] == '-' || m_s[m_i] == '+' || m_s[m_i] == '.' || m_s[m_i] == 'e' || m_s[m_i] == 'E'))
                ++m_i;
            if (m_i == start || m_i - start > 64)
                return false;
            const std::string num(m_s.substr(start, m_i - start));
            char*             end = nullptr;
            v.num                 = std::strtod(num.c_str(), &end);
            if (!end || *end != '\0')
                return false;
            v.type = SJson::eType::NUM;
            return true;
        }
    };

    inline std::optional<SRect> rectOf(const SJson* v) {
        if (!v || v->type != SJson::eType::ARR || v->arr.size() != 4)
            return std::nullopt;
        double n[4];
        for (int i = 0; i < 4; ++i) {
            if (v->arr[i].type != SJson::eType::NUM)
                return std::nullopt;
            n[i] = v->arr[i].num;
        }
        SRect r{n[0], n[1], n[2], n[3]};
        if (!r.valid())
            return std::nullopt;
        return r;
    }

    inline std::string lower(std::string s) {
        for (auto& c : s)
            c = (char)std::tolower((unsigned char)c);
        return s;
    }

    // ---- dock contract ($XDG_RUNTIME_DIR/mios-dock-icons.json) -------------------------------------------------

    struct SDockIcon {
        std::vector<std::string> keys; // lowercased app_id, desktop_id without .desktop, classes[]
        SRect                    rect;
    };
    struct SDockOutput {
        bool                                       visible = true;
        std::optional<eEdge>                       edge;
        std::optional<SRect>                       dockRect, edgeRect, slot;
        std::vector<SDockIcon>                     icons;
        std::vector<std::pair<std::string, SRect>> minimized; // lowercased address
    };
    using DockMap = std::map<std::string, SDockOutput>;

    inline void addKey(std::vector<std::string>& keys, std::string k) {
        k = lower(std::move(k));
        if (k.size() > 8 && k.ends_with(".desktop"))
            k.resize(k.size() - 8);
        if (!k.empty() && std::find(keys.begin(), keys.end(), k) == keys.end())
            keys.push_back(std::move(k));
    }

    inline std::vector<std::string> keysOf(const SJson& e) {
        std::vector<std::string> keys;
        addKey(keys, e.string("app_id"));
        addKey(keys, e.string("desktop_id"));
        if (const auto* cl = e.get("classes"); cl && cl->type == SJson::eType::ARR)
            for (const auto& c : cl->arr)
                if (c.type == SJson::eType::STR)
                    addKey(keys, c.str);
        return keys;
    }

    // Never throws; malformed parts are skipped (a bad icon never loses the others).
    inline DockMap parseDock(std::string_view text) {
        DockMap    out;
        const auto root = CJsonReader(text).parse();
        if (!root || root->type != SJson::eType::OBJ)
            return out;
        const auto* outputs = root->get("outputs");
        if (!outputs || outputs->type != SJson::eType::OBJ)
            return out;
        for (const auto& [name, o] : outputs->obj) {
            if (o.type != SJson::eType::OBJ || name.empty())
                continue;
            SDockOutput d;
            d.visible  = o.boolean("visible").value_or(true);
            d.edge     = edgeFromString(o.string("edge"));
            d.dockRect = rectOf(o.get("dock_rect"));
            d.edgeRect = rectOf(o.get("edge_rect"));
            d.slot     = rectOf(o.get("minimized_slot"));
            if (const auto* icons = o.get("icons"); icons && icons->type == SJson::eType::ARR)
                for (const auto& ic : icons->arr) {
                    const auto r = rectOf(ic.get("rect"));
                    auto       k = keysOf(ic);
                    if (r && !k.empty())
                        d.icons.push_back({std::move(k), *r});
                }
            if (const auto* mins = o.get("minimized"); mins && mins->type == SJson::eType::ARR)
                for (const auto& m : mins->arr) {
                    const auto r = rectOf(m.get("rect"));
                    auto       a = lower(m.string("address"));
                    if (r && !a.empty())
                        d.minimized.emplace_back(std::move(a), *r);
                }
            out[name] = std::move(d);
        }
        return out;
    }

    inline bool matchesKeys(const std::vector<std::string>& keys, const std::string& cls, const std::string& initialCls) {
        const auto a = lower(cls), b = lower(initialCls);
        for (const auto& k : keys)
            if ((!a.empty() && k == a) || (!b.empty() && k == b))
                return true;
        return false;
    }

    struct STargetQuery {
        std::string output, cls, initialCls, address; // address lowercased "0x..."
        SRect       monitor;                          // the window's output, layout coords
        bool        restore = false;                  // prefer the window's own minimized entry
        bool        hiddenToEdge = true;              // [lamp] hidden_dock = "edge"
        eEdge       fallbackEdge = eEdge::BOTTOM;     // [lamp] fallback_edge
        std::string sourcePreference;                 // optional "minimized" | "icon"
    };
    struct STarget {
        SRect                rect;
        std::optional<eEdge> edgeHint;
        std::string          source; // "minimized" | "icon" | "slot" | "edge" | "fallback"
    };

    // The monitor edge strip the funnel ends in (no dock data, or a hidden dock without an edge rect).
    inline SRect edgeStrip(const SRect& mon, eEdge e, std::optional<SRect> around = std::nullopt) {
        constexpr double THICK = 4.0, LEN = 64.0;
        switch (e) {
            case eEdge::TOP: {
                const double cx = around ? around->cx() : mon.cx(), w = around ? std::max(around->w, 8.0) : LEN;
                return {cx - w / 2, mon.y, w, THICK};
            }
            case eEdge::LEFT: {
                const double cy = around ? around->cy() : mon.cy(), h = around ? std::max(around->h, 8.0) : LEN;
                return {mon.x, cy - h / 2, THICK, h};
            }
            case eEdge::RIGHT: {
                const double cy = around ? around->cy() : mon.cy(), h = around ? std::max(around->h, 8.0) : LEN;
                return {mon.x + mon.w - THICK, cy - h / 2, THICK, h};
            }
            default: {
                const double cx = around ? around->cx() : mon.cx(), w = around ? std::max(around->w, 8.0) : LEN;
                return {cx - w / 2, mon.y + mon.h - THICK, w, THICK};
            }
        }
    }

    // Project a revealed-position rect onto the hidden dock's edge strip (keeps the icon's position along the edge).
    inline SRect projectToStrip(const SRect& r, const SRect& strip, eEdge e) {
        if (e == eEdge::LEFT || e == eEdge::RIGHT)
            return {strip.x, r.y, strip.w, r.h};
        return {r.x, strip.y, r.w, strip.h};
    }

    inline STarget resolveTarget(const DockMap& dock, const STargetQuery& q) {
        auto it = dock.find(q.output);
        if (it == dock.end() && !dock.empty()) {
            if (q.restore && !q.address.empty()) {
                for (auto o = dock.begin(); o != dock.end(); ++o) {
                    for (const auto& [addr, rect] : o->second.minimized) {
                        if (addr == q.address) {
                            it = o;
                            break;
                        }
                    }
                    if (it != dock.end())
                        break;
                }
            }
            if (it == dock.end() && dock.size() == 1)
                it = dock.begin();
        }
        if (it != dock.end()) {
            const auto&          d = it->second;
            std::optional<SRect> r;
            std::string          src;
            if (q.sourcePreference == "icon") {
                for (const auto& ic : d.icons)
                    if (matchesKeys(ic.keys, q.cls, q.initialCls)) {
                        r   = ic.rect;
                        src = "icon";
                        break;
                    }
            }
            if (!r && q.restore && !q.address.empty())
                for (const auto& [addr, rect] : d.minimized)
                    if (addr == q.address) {
                        r   = rect;
                        src = "minimized";
                        break;
                    }
            if (!r)
                for (const auto& ic : d.icons)
                    if (matchesKeys(ic.keys, q.cls, q.initialCls)) {
                        r   = ic.rect;
                        src = "icon";
                        break;
                    }
            if (!r && d.slot) {
                r   = d.slot;
                src = "slot";
            }
            if (r) {
                const eEdge e = d.edge.value_or(q.fallbackEdge);
                if (!d.visible && q.hiddenToEdge) {
                    const SRect strip = d.edgeRect ? *d.edgeRect : (q.monitor.valid() ? edgeStrip(q.monitor, e) : *r);
                    return {projectToStrip(*r, strip, e), e, "edge"};
                }
                return {*r, d.edge, src};
            }
            if (d.edgeRect || d.dockRect) { // dock data but nothing to aim at: its edge strip / bar centre
                const SRect base = d.edgeRect ? *d.edgeRect : *d.dockRect;
                const eEdge e    = d.edge.value_or(q.fallbackEdge);
                return {d.visible ? SRect{base.cx() - 24, base.cy() - 24, 48, 48} : edgeStrip(base, e, base), e, "edge"};
            }
        }
        if (!dock.empty() && q.restore && !q.address.empty()) {
            for (const auto& [outName, d] : dock) {
                for (const auto& [addr, rect] : d.minimized) {
                    if (addr == q.address) {
                        return {rect, d.edge, "minimized"};
                    }
                }
            }
        }
        if (q.monitor.valid())
            return {edgeStrip(q.monitor, q.fallbackEdge), q.fallbackEdge, "fallback"};
        return {SRect{0, 0, 48, 4}, q.fallbackEdge, "fallback"};
    }

    // ---- launch contract ($XDG_RUNTIME_DIR/mios-lamp-launch.json) ----------------------------------------------

    struct SPendingLaunch {
        std::string              id, output;
        double                   timeMs = 0;
        std::vector<std::string> keys;
        SRect                    rect;
    };

    inline std::vector<SPendingLaunch> parseLaunches(std::string_view text) {
        std::vector<SPendingLaunch> out;
        const auto                  root = CJsonReader(text).parse();
        if (!root || root->type != SJson::eType::OBJ)
            return out;
        const auto* pend = root->get("pending");
        if (!pend || pend->type != SJson::eType::ARR)
            return out;
        for (const auto& e : pend->arr) {
            if (e.type != SJson::eType::OBJ)
                continue;
            SPendingLaunch p;
            p.timeMs    = e.number("time_ms").value_or(-1);
            const auto r = rectOf(e.get("rect"));
            p.keys      = keysOf(e);
            p.output    = e.string("output");
            p.id        = e.string("id");
            if (p.id.empty())
                p.id = std::to_string((long long)p.timeMs) + "-" + (p.keys.empty() ? std::string{} : p.keys[0]);
            if (!r || p.keys.empty() || p.timeMs <= 0)
                continue;
            p.rect = *r;
            out.push_back(std::move(p));
        }
        std::sort(out.begin(), out.end(), [](const auto& a, const auto& b) { return a.timeMs < b.timeMs; });
        return out;
    }

    // The oldest unconsumed launch younger than timeoutMs whose classes match the new window (and, when the entry
    // names an output, that is the window's output). nowMs is wall-clock epoch ms, as the dock writes it.
    template <typename IsConsumed>
    inline std::optional<SPendingLaunch> matchLaunch(const std::vector<SPendingLaunch>& pending, double nowMs, double timeoutMs, const std::string& cls,
                                                     const std::string& initialCls, const std::string& output, IsConsumed&& consumed) {
        for (const auto& p : pending) {
            const double age = nowMs - p.timeMs;
            if (age < -2000 || age > timeoutMs)
                continue;
            if (!p.output.empty() && !output.empty() && p.output != output)
                continue;
            if (consumed(p.id) || !matchesKeys(p.keys, cls, initialCls))
                continue;
            return p;
        }
        return std::nullopt;
    }
}
