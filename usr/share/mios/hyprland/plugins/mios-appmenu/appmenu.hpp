// AI-hint: mios-appmenu address validation (no compositor SDK; host-testable): a set_address string is published only
// when it is a D-Bus bus name / object path per the D-Bus specification, so an IPC line "ADDR,SERVICE,PATH" can never
// carry a comma, newline or other separator a listener would mis-split.
// AI-related: usr/share/mios/hyprland/plugins/mios-appmenu/main.cpp, usr/share/mios/hyprland/plugins/mios-appmenu/tests/appmenu_test.cpp
#pragma once
#include <cstddef>
#include <string>

namespace MiosAppmenu {
    inline constexpr size_t MAX_OBJECTS            = 4096;
    inline constexpr size_t MAX_OBJECTS_PER_CLIENT = 256;

    // Unique (":1.42") or well-known ("org.example.App") bus name: <= 255 bytes, >= 2 elements separated by '.',
    // elements of [A-Za-z0-9_-] (well-known: no element starts with a digit), no empty element.
    inline bool validBusName(const std::string& s) {
        if (s.empty() || s.size() > 255)
            return false;
        const bool   unique = s[0] == ':';
        size_t       i = unique ? 1 : 0, elements = 0, len = 0;
        for (; i <= s.size(); ++i) {
            const char c = i < s.size() ? s[i] : '.';
            if (c == '.') {
                if (len == 0)
                    return false;
                ++elements;
                len = 0;
                continue;
            }
            const bool alpha = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_' || c == '-';
            const bool digit = c >= '0' && c <= '9';
            if (!alpha && !digit)
                return false;
            if (!unique && len == 0 && digit)
                return false;
            ++len;
        }
        return elements >= 2;
    }

    // "/" or "/a/b_c/1": elements of [A-Za-z0-9_], no empty element, no trailing '/'.
    inline bool validObjectPath(const std::string& s) {
        if (s.empty() || s[0] != '/' || s.size() > 1024)
            return false;
        if (s.size() == 1)
            return true;
        if (s.back() == '/')
            return false;
        char prev = '/';
        for (size_t i = 1; i < s.size(); ++i) {
            const char c = s[i];
            if (c == '/') {
                if (prev == '/')
                    return false;
            } else if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'))
                return false;
            prev = c;
        }
        return true;
    }
}
