// AI-hint: host test of mios-appmenu's set_address validation (appmenu.hpp): real toolkit addresses pass (Qt
// /MenuBar/<n>, Gecko and Chromium /com/canonical/menu/<n|hex>, unique and well-known names); separator-carrying,
// empty and malformed strings are refused (negative controls). `make test`.
// AI-related: usr/share/mios/hyprland/plugins/mios-appmenu/appmenu.hpp
#include "../appmenu.hpp"
#include <cstdio>
#include <cstdlib>

static int failures = 0;
#define CHECK(x)                                                                                                       \
    do {                                                                                                               \
        if (!(x)) {                                                                                                    \
            std::fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, #x);                                         \
            ++failures;                                                                                                \
        }                                                                                                              \
    } while (0)

int main() {
    using namespace MiosAppmenu;
    CHECK(validBusName(":1.42"));
    CHECK(validBusName(":1.4205"));
    CHECK(validBusName("org.kde.StatusNotifierItem-12-1"));
    CHECK(validBusName("com.obsproject.Studio"));
    CHECK(!validBusName(""));
    CHECK(!validBusName(":1.42,/evil"));
    CHECK(!validBusName("org..x"));
    CHECK(!validBusName("org.1x"));
    CHECK(!validBusName("single"));
    CHECK(!validBusName("org.x\n"));
    CHECK(!validBusName(std::string(300, 'a') + ".b"));
    CHECK(validObjectPath("/MenuBar/1"));
    CHECK(validObjectPath("/com/canonical/menu/1"));
    CHECK(validObjectPath("/com/canonical/menu/4C0000A"));
    CHECK(validObjectPath("/"));
    CHECK(!validObjectPath(""));
    CHECK(!validObjectPath("MenuBar/1"));
    CHECK(!validObjectPath("/MenuBar/"));
    CHECK(!validObjectPath("/Menu//Bar"));
    CHECK(!validObjectPath("/a,b"));
    CHECK(!validObjectPath("/a-b"));
    if (failures)
        return EXIT_FAILURE;
    std::puts("mios-appmenu validation: all checks passed");
    return EXIT_SUCCESS;
}
