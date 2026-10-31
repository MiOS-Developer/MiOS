# AI-hint: Private uinput gamepad for the mios-controls live tests: named mios-input-fixture-* (mios-inputd's live bridge refuses it, so only its owner and the editor read it), with an Xbox or generic vendor id for the family glyphs; press/hold/stick/trigger/hat helpers; destroyed on close.
# AI-related: tests/test-mios-controls-visual-live.py, tools/native/mios-controls/src/pads.rs, tools/native/mios-controls/src/devices.rs (FIXTURE_PREFIX), tools/native/mios-inputd/src/linux.rs
"""A private uinput gamepad (legacy uinput_user_dev setup, no python-evdev dependency)."""
import fcntl
import os
import struct
import time

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 0x10, 0x11
BUTTONS = {
    'BTN_SOUTH': 0x130, 'BTN_EAST': 0x131, 'BTN_NORTH': 0x133, 'BTN_WEST': 0x134, 'BTN_TL': 0x136, 'BTN_TR': 0x137,
    'BTN_SELECT': 0x13a, 'BTN_START': 0x13b, 'BTN_MODE': 0x13c, 'BTN_THUMBL': 0x13d, 'BTN_THUMBR': 0x13e,
}
FAMILIES = {'xbox': (0x045e, 0x0b12), 'playstation': (0x054c, 0x0ce6), 'generic': (0x1209, 0x0001)}
PREFIX = 'mios-input-fixture-'


class FixturePad:
    def __init__(self, family='xbox', tag='controls'):
        vendor, product = FAMILIES[family]
        self.name = f'{PREFIX}{tag}-{family}'
        self.fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            for ev in (EV_KEY, EV_ABS):
                fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
            for code in BUTTONS.values():
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            ranges = {ABS_X: (-32768, 32767), ABS_Y: (-32768, 32767), ABS_RX: (-32768, 32767), ABS_RY: (-32768, 32767),
                      ABS_Z: (0, 1023), ABS_RZ: (0, 1023), ABS_HAT0X: (-1, 1), ABS_HAT0Y: (-1, 1)}
            amax, amin, fuzz, flat = [0] * 64, [0] * 64, [0] * 64, [0] * 64
            for code, (lo, hi) in ranges.items():
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
                amin[code], amax[code] = lo, hi
                flat[code] = 128 if hi > 1023 else 0
            dev = struct.pack('80sHHHHi64i64i64i64i', self.name.encode(), 3, vendor, product, 1, 0, *amax, *amin, *fuzz, *flat)
            os.write(self.fd, dev)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except BaseException:
            os.close(self.fd)
            raise
        self.event = self._wait_node()

    def _wait_node(self, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for block in open('/proc/bus/input/devices').read().split('\n\n'):
                if f'N: Name="{self.name}"' in block:
                    for tok in block.split():
                        if tok.startswith('event') and tok[5:].isdigit():
                            return tok
            time.sleep(0.05)
        return None

    def _emit(self, typ, code, value):
        os.write(self.fd, struct.pack('llHHi', 0, 0, typ, code, value))

    def _syn(self):
        self._emit(EV_SYN, 0, 0)

    def button(self, name, down):
        self._emit(EV_KEY, BUTTONS[name], 1 if down else 0)
        self._syn()

    def press(self, name, hold=0.06, gap=0.12):
        self.button(name, True)
        time.sleep(hold)
        self.button(name, False)
        time.sleep(gap)

    def hat(self, x=0, y=0, hold=0.06, gap=0.12):
        """A d-pad tap through the HAT axes (x/y in -1..1), then centred."""
        self._emit(EV_ABS, ABS_HAT0X, x)
        self._emit(EV_ABS, ABS_HAT0Y, y)
        self._syn()
        time.sleep(hold)
        self._emit(EV_ABS, ABS_HAT0X, 0)
        self._emit(EV_ABS, ABS_HAT0Y, 0)
        self._syn()
        time.sleep(gap)

    def stick(self, axis, value, hold=0.08, gap=0.12):
        code = {'LX': ABS_X, 'LY': ABS_Y, 'RX': ABS_RX, 'RY': ABS_RY}[axis]
        self._emit(EV_ABS, code, value)
        self._syn()
        time.sleep(hold)
        self._emit(EV_ABS, code, 0)
        self._syn()
        time.sleep(gap)

    def trigger(self, side, value=1023, hold=0.08, gap=0.12):
        code = ABS_Z if side == 'L' else ABS_RZ
        self._emit(EV_ABS, code, value)
        self._syn()
        time.sleep(hold)
        self._emit(EV_ABS, code, 0)
        self._syn()
        time.sleep(gap)

    def close(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
