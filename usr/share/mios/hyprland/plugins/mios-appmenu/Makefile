# AI-hint: Build mios-appmenu.so. `mios-gen build-hyprland-plugin --name mios-appmenu` (inside podman-MiOS-DEV) passes
# the compositor SDK include path as INCLUDES and the GL/wayland link line as LIBS (the mios-pacing Makefile contract);
# wayland-scanner generates the org_kde_kwin_appmenu server glue from appmenu.xml into the build tree. `make test`
# builds and runs the host test of the address validation (appmenu.hpp; no compositor SDK needed).
# AI-related: usr/share/mios/hyprland/plugins/mios-appmenu/main.cpp, usr/share/mios/hyprland/plugins/mios-appmenu/appmenu.xml, usr/share/mios/mios.toml

ifeq ($(CXX),g++)
    EXTRA_FLAGS = --no-gnu-unique
else
    EXTRA_FLAGS =
endif

CXXFLAGS ?= -O2
CXXFLAGS += -shared -fPIC -std=c++2b -Wall -Wno-c++11-narrowing
CFLAGS   ?= -O2
INCLUDES ?= `pkg-config --cflags pixman-1 libdrm hyprland libinput libudev wayland-server xkbcommon`
LIBS     ?= `pkg-config --libs egl glesv2 wayland-server`
SCANNER  ?= wayland-scanner

SRC    = main.cpp
HDR    = appmenu.hpp appmenu-server-protocol.h
TARGET = mios-appmenu.so
TEST  ?= $(or $(TMPDIR),/tmp)/mios-appmenu-test

all: $(TARGET)

appmenu-server-protocol.h: appmenu.xml
	$(SCANNER) server-header $< $@

appmenu-protocol.o: appmenu.xml
	$(SCANNER) private-code $< appmenu-protocol.c
	$(CC) $(CFLAGS) -fPIC -c appmenu-protocol.c -o $@

$(TARGET): $(SRC) $(HDR) appmenu-protocol.o
	$(CXX) $(CXXFLAGS) $(LDFLAGS) $(EXTRA_FLAGS) -I. $(INCLUDES) $(SRC) appmenu-protocol.o -o $@ $(LIBS)

$(TEST): tests/appmenu_test.cpp appmenu.hpp
	$(CXX) -O2 -std=c++2b -Wall -Wextra tests/appmenu_test.cpp -o $@

test: $(TEST)
	$(TEST)

clean:
	rm -f ./$(TARGET) appmenu-protocol.o appmenu-protocol.c appmenu-server-protocol.h $(TEST)

.PHONY: all clean test
