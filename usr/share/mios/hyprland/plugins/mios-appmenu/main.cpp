// AI-hint: mios-appmenu -- the KDE appmenu Wayland protocol (org_kde_kwin_appmenu_manager v2) for Hyprland 0.56 (MiOS
// original source; [desktop.hyprland.mios-appmenu]). Hyprland has no global-menu protocol, so Wayland-native toolkits
// that announce their menubar per surface -- Qt 6 (QDBusMenuBar), Gecko (widget.gtk.global-menu.wayland), Chromium
// and Electron (ozone/wayland DbusAppmenu) -- either never export it or leave miosd guessing which window a
// /MenuBar/<n> or /com/canonical/menu/<n> belongs to. With this global advertised, each client sends
// set_address(service, object_path) for its own wl_surface (a client can only name its own surfaces); the plugin
// keeps the table, binds it to the Hyprland window that owns the surface and publishes it two ways:
//   * IPC event `miosappmenu>>0xADDRESS,SERVICE,PATH` on set_address, on the window mapping, and with empty SERVICE
//     and PATH when the object goes away (miosd appmenu serve re-snapshots on it);
//   * `hyprctl mios-appmenu` (json): {"version":1,"objects":N,"menus":[{address,class,pid,client_pid,service,path}]}.
// Service and path are untrusted client strings: validated as D-Bus bus names / object paths here, and miosd still
// requires that the D-Bus connection owning SERVICE belongs to the window's process (or its Flatpak scope).
// AI-related: usr/share/mios/hyprland/plugins/mios-appmenu/appmenu.xml, usr/share/mios/hyprland/plugins/mios-appmenu/Makefile,
// AI-related: src/mios-rs/miosd/src/appmenu.rs, usr/share/mios/mios.toml [desktop.hyprland.mios-appmenu], tests/test-appmenu-global.py
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/managers/EventManager.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/desktop/state/WindowState.hpp>
#include <hyprland/src/desktop/view/WLSurface.hpp>
#include <hyprland/src/desktop/view/Window.hpp>

extern "C" {
#include "appmenu-server-protocol.h"
}

#include "appmenu.hpp"

#include <cstdint>
#include <format>
#include <string>
#include <unordered_map>
#include <vector>

namespace {
    HANDLE                           PHANDLE = nullptr;
    wl_global*                       g_global = nullptr;
    SP<SHyprCtlCommand>              g_cmd;
    std::vector<CHyprSignalListener> g_listeners;
    bool                             g_exiting = false;

    struct SEntry {
        WP<CWLSurfaceResource> surface;
        wl_client*             client = nullptr;
        pid_t                  clientPid = 0;
        std::string            service;
        std::string            path;
        std::string            announced; // window address last announced on the IPC socket ("" = none)
    };
    // keyed by the org_kde_kwin_appmenu resource
    std::unordered_map<wl_resource*, SEntry> g_entries;
    std::vector<wl_resource*>                g_managers;

    std::string jsonEscape(const std::string& s) {
        std::string out;
        for (const char c : s) {
            if (c == '"' || c == '\\')
                out += '\\';
            if (static_cast<unsigned char>(c) < 0x20)
                continue;
            out += c;
        }
        return out;
    }

    std::string addressOf(const PHLWINDOW& w) {
        return std::format("0x{:x}", reinterpret_cast<std::uintptr_t>(w.get()));
    }

    PHLWINDOW windowFor(const SP<CWLSurfaceResource>& surface) {
        if (!surface || !Desktop::windowState())
            return nullptr;
        for (const auto& w : Desktop::windowState()->windows()) {
            if (!w || w->m_isX11)
                continue;
            const auto WL = w->wlSurface();
            if (WL && WL->resource() == surface)
                return w;
        }
        return nullptr;
    }

    void post(const std::string& address, const std::string& service, const std::string& path) {
        if (g_exiting || !g_pEventManager || address.empty())
            return;
        g_pEventManager->postEvent(SHyprIPCEvent{"miosappmenu", std::format("{},{},{}", address, service, path)});
    }

    // Announce an entry when its surface belongs to a mapped window (again after a remap or a new address).
    void announce(SEntry& e) {
        if (e.service.empty())
            return;
        const auto W = windowFor(e.surface.lock());
        if (!W || !W->m_isMapped)
            return;
        e.announced = addressOf(W);
        post(e.announced, e.service, e.path);
    }

    void retract(SEntry& e) {
        if (!e.announced.empty())
            post(e.announced, "", "");
        e.announced.clear();
    }

    // ---- org_kde_kwin_appmenu -----------------------------------------------------------------------------------------
    void appmenuSetAddress(wl_client*, wl_resource* res, const char* service, const char* path) {
        const auto IT = g_entries.find(res);
        if (IT == g_entries.end())
            return;
        auto& e = IT->second;
        const std::string S = service ? service : "";
        const std::string P = path ? path : "";
        if (!MiosAppmenu::validBusName(S) || !MiosAppmenu::validObjectPath(P)) {
            // Never publish a malformed address; an earlier valid one is withdrawn (the client meant to replace it).
            retract(e);
            e.service.clear();
            e.path.clear();
            return;
        }
        if (S == e.service && P == e.path && !e.announced.empty())
            return;
        e.service = S;
        e.path    = P;
        announce(e);
    }

    void appmenuRelease(wl_client*, wl_resource* res) {
        wl_resource_destroy(res);
    }

    const struct org_kde_kwin_appmenu_interface APPMENU_IMPL = {
        .set_address = appmenuSetAddress,
        .release     = appmenuRelease,
    };

    void appmenuDestroy(wl_resource* res) {
        const auto IT = g_entries.find(res);
        if (IT == g_entries.end())
            return;
        retract(IT->second);
        g_entries.erase(IT);
    }

    // ---- org_kde_kwin_appmenu_manager ---------------------------------------------------------------------------------
    void managerCreate(wl_client* client, wl_resource* manager, uint32_t id, wl_resource* surfaceRes) {
        const auto SURFACE = CWLSurfaceResource::fromResource(surfaceRes);
        size_t     mine    = 0;
        for (const auto& [r, e] : g_entries)
            mine += e.client == client;
        wl_resource* res = wl_resource_create(client, &org_kde_kwin_appmenu_interface, wl_resource_get_version(manager), id);
        if (!res) {
            wl_client_post_no_memory(client);
            return;
        }
        wl_resource_set_implementation(res, &APPMENU_IMPL, nullptr, appmenuDestroy);
        // Bounded table: a client gets one object per surface in practice; past the cap the object stays inert.
        if (!SURFACE || mine >= MiosAppmenu::MAX_OBJECTS_PER_CLIENT || g_entries.size() >= MiosAppmenu::MAX_OBJECTS)
            return;
        pid_t pid = 0;
        wl_client_get_credentials(client, &pid, nullptr, nullptr);
        g_entries[res] = SEntry{.surface = SURFACE, .client = client, .clientPid = pid};
    }

    void managerRelease(wl_client*, wl_resource* res) {
        wl_resource_destroy(res);
    }

    const struct org_kde_kwin_appmenu_manager_interface MANAGER_IMPL = {
        .create  = managerCreate,
        .release = managerRelease,
    };

    void managerBind(wl_client* client, void*, uint32_t version, uint32_t id) {
        wl_resource* res = wl_resource_create(client, &org_kde_kwin_appmenu_manager_interface, std::min<uint32_t>(version, 2), id);
        if (!res) {
            wl_client_post_no_memory(client);
            return;
        }
        wl_resource_set_implementation(res, &MANAGER_IMPL, nullptr, [](wl_resource* r) { std::erase(g_managers, r); });
        g_managers.push_back(res);
    }

    std::string tableJson() {
        std::string out   = std::format("{{\"version\":1,\"objects\":{},\"menus\":[", g_entries.size());
        bool        first = true;
        for (auto& [res, e] : g_entries) {
            if (e.service.empty())
                continue;
            const auto W = windowFor(e.surface.lock());
            if (!W || !W->m_isMapped)
                continue;
            out += std::format("{}{{\"address\":\"{}\",\"class\":\"{}\",\"pid\":{},\"client_pid\":{},\"service\":\"{}\",\"path\":\"{}\"}}", first ? "" : ",",
                               addressOf(W), jsonEscape(W->m_class), W->getPID(), e.clientPid, jsonEscape(e.service), jsonEscape(e.path));
            first = false;
        }
        return out + "]}";
    }

    std::string hyprctl(eHyprCtlOutputFormat, std::string) {
        return tableJson();
    }
}

APICALL EXPORT std::string PLUGIN_API_VERSION() {
    return HYPRLAND_API_VERSION;
}

APICALL EXPORT PLUGIN_DESCRIPTION_INFO PLUGIN_INIT(HANDLE handle) {
    PHANDLE = handle;
    if (std::string(__hyprland_api_get_hash()) != __hyprland_api_get_client_hash()) {
        HyprlandAPI::addNotification(PHANDLE, "[mios-appmenu] headers do not match the running Hyprland; not loading", CHyprColor{1.0, 0.2, 0.2, 1.0}, 8000);
        throw std::runtime_error("[mios-appmenu] version mismatch");
    }
    if (!g_pCompositor || !g_pCompositor->m_wlDisplay)
        throw std::runtime_error("[mios-appmenu] no wayland display");
    g_global = wl_global_create(g_pCompositor->m_wlDisplay, &org_kde_kwin_appmenu_manager_interface, 2, nullptr, managerBind);
    if (!g_global)
        throw std::runtime_error("[mios-appmenu] cannot create org_kde_kwin_appmenu_manager");

    // A window maps after its toolkit already announced the surface's menu (Qt sets it on QWindow creation).
    g_listeners.push_back(Event::bus()->m_events.window.open.listen([](PHLWINDOW w) {
        if (g_exiting || !w)
            return;
        for (auto& [res, e] : g_entries)
            if (windowFor(e.surface.lock()) == w)
                announce(e);
    }));
    g_listeners.push_back(Event::bus()->m_events.window.close.listen([](PHLWINDOW w) {
        if (g_exiting || !w)
            return;
        const auto ADDR = addressOf(w);
        for (auto& [res, e] : g_entries)
            if (e.announced == ADDR)
                e.announced.clear(); // closewindow>> already tells listeners; a remap announces again
    }));
    g_cmd = HyprlandAPI::registerHyprCtlCommand(PHANDLE, SHyprCtlCommand{.name = "mios-appmenu", .exact = true, .fn = hyprctl});
    return {"mios-appmenu", "KDE appmenu protocol: per-window D-Bus menubar addresses for the MiOS top-bar menus", "MiOS", "1.0"};
}

APICALL EXPORT void PLUGIN_EXIT() {
    g_exiting = true;
    g_listeners.clear();
    if (g_cmd)
        HyprlandAPI::unregisterHyprCtlCommand(PHANDLE, g_cmd);
    // The resources' implementations live in this object: destroy them (the clients' objects become inert -- the
    // compositor only unloads plugins on exit) before the global goes away.
    std::vector<wl_resource*> live = g_managers;
    for (auto& [res, e] : g_entries)
        live.push_back(res);
    g_managers.clear();
    for (auto* res : live) {
        wl_resource_set_destructor(res, nullptr);
        wl_resource_destroy(res);
    }
    g_entries.clear();
    if (g_global)
        wl_global_destroy(g_global);
    g_global = nullptr;
}
