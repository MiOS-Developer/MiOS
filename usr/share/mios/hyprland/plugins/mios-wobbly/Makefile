# AI-hint: Build mios-wobbly.so. `mios-gen build-hyprland-plugin --name mios-wobbly` (inside
# podman-MiOS-DEV) passes the compositor SDK include path as INCLUDES and the GL/wayland link
# line as LIBS, matching the hyprbars Makefile contract; a hand build can use pkg-config.
# `make test` builds and runs the host tests (wobble model, lamp mesh/contract; no compositor SDK needed).
# AI-related: usr/share/mios/hyprland/plugins/mios-wobbly/main.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/lamp.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/wobble_model.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/lampmath.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/tests/model_test.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/tests/lamp_test.cpp

ifeq ($(CXX),g++)
    EXTRA_FLAGS = --no-gnu-unique
else
    EXTRA_FLAGS =
endif

CXXFLAGS ?= -O2
CXXFLAGS += -shared -fPIC -std=c++2b -Wall -Wno-c++11-narrowing
INCLUDES ?= `pkg-config --cflags pixman-1 libdrm hyprland libinput libudev wayland-server xkbcommon`
LIBS     ?= `pkg-config --libs egl glesv2`

SRC    = main.cpp lamp.cpp
HDR    = wobble_model.hpp lampmath.hpp lamp.hpp fxstats.hpp
TARGET = mios-wobbly.so
# the test binary goes outside the tree, so a later plugin build never copies it in
TEST  ?= $(or $(TMPDIR),/tmp)/mios-wobbly-model-test
LAMPTEST ?= $(or $(TMPDIR),/tmp)/mios-wobbly-lamp-test

all: $(TARGET)

$(TARGET): $(SRC) $(HDR)
	$(CXX) $(CXXFLAGS) $(LDFLAGS) $(EXTRA_FLAGS) $(INCLUDES) $(SRC) -o $@ $(LIBS)

$(TEST): tests/model_test.cpp wobble_model.hpp
	$(CXX) -O2 -std=c++2b -Wall -Wextra tests/model_test.cpp -o $@

$(LAMPTEST): tests/lamp_test.cpp lampmath.hpp
	$(CXX) -O2 -std=c++2b -Wall -Wextra tests/lamp_test.cpp -o $@

test: $(TEST) $(LAMPTEST)
	$(TEST)
	$(LAMPTEST)

clean:
	rm -f ./$(TARGET) $(TEST) $(LAMPTEST)

.PHONY: all clean test
