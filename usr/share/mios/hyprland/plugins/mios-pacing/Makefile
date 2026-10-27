# AI-hint: Build mios-pacing.so. `mios-gen build-hyprland-plugin --name mios-pacing` (inside podman-MiOS-DEV) passes
# the compositor SDK include path as INCLUDES and the GL/wayland link line as LIBS, matching the mios-wobbly and
# hyprbars Makefile contract; a hand build can use pkg-config. `make test` builds and runs the host tests of the
# frame gate (pacing.hpp; no compositor SDK needed).
# AI-related: usr/share/mios/hyprland/plugins/mios-pacing/main.cpp, usr/share/mios/hyprland/plugins/mios-pacing/pacing.hpp, usr/share/mios/hyprland/plugins/mios-pacing/tests/pacing_test.cpp, usr/share/mios/mios.toml

ifeq ($(CXX),g++)
    EXTRA_FLAGS = --no-gnu-unique
else
    EXTRA_FLAGS =
endif

CXXFLAGS ?= -O2
CXXFLAGS += -shared -fPIC -std=c++2b -Wall -Wno-c++11-narrowing
INCLUDES ?= `pkg-config --cflags pixman-1 libdrm hyprland libinput libudev wayland-server xkbcommon`
LIBS     ?= `pkg-config --libs egl glesv2`

SRC    = main.cpp
HDR    = pacing.hpp
TARGET = mios-pacing.so
# the test binary goes outside the tree, so a later plugin build never copies it in
TEST  ?= $(or $(TMPDIR),/tmp)/mios-pacing-test

all: $(TARGET)

$(TARGET): $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) $(LDFLAGS) $(EXTRA_FLAGS) $(INCLUDES) $(SRC) -o $@ $(LIBS)

$(TEST): tests/pacing_test.cpp pacing.hpp
	$(CXX) -O2 -std=c++2b -Wall -Wextra tests/pacing_test.cpp -o $@

test: $(TEST)
	$(TEST)

clean:
	rm -f ./$(TARGET) $(TEST)

.PHONY: all clean test
