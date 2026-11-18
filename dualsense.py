"""Read a PS5 DualSense controller on Linux through the kernel joystick interface.

Standard library only. The kernel driver hid_playstation exposes the controller as
/dev/input/jsN, which every user may read, so no extra permissions are needed.

    import select
    from dualsense import DualSense

    with DualSense() as pad:
        while True:
            select.select([pad], [], [])        # sleep until there is input
            for event in pad.poll():            # Event(kind, name, value)
                print(event)
            print(pad.buttons["cross"], pad.axes["left_x"])    # current state

Buttons: cross circle triangle square l1 r1 l2 r2 create options ps l3 r3
Axes:    left_x left_y right_x right_y l2_trigger r2_trigger dpad_x dpad_y
         from -1.0 to 1.0; x grows to the right, y grows downwards (a stick pushed
         forward is -1.0), a released trigger is -1.0.

Run as a program, it prints the controller's status:  python3 dualsense.py
padview.py shows the controller live, as a picture. record.py records what it does and plays it back.
"""
from __future__ import annotations

import array
import errno
import fcntl
import os
import struct
import sys
from pathlib import Path
from typing import NamedTuple

SONY_VENDOR_ID = "054c"
PRODUCT_IDS = {"0ce6", "0df2"}                       # DualSense, DualSense Edge
CONNECTIONS = {"0003": "usb", "0005": "bluetooth"}   # sysfs id/bustype

# Input event codes (linux/input-event-codes.h) that hid_playstation reports.
BUTTONS = {
    0x130: "cross", 0x131: "circle", 0x133: "triangle", 0x134: "square",
    0x136: "l1", 0x137: "r1", 0x138: "l2", 0x139: "r2",
    0x13A: "create", 0x13B: "options", 0x13C: "ps", 0x13D: "l3", 0x13E: "r3",
}
AXES = {
    0x00: "left_x", 0x01: "left_y", 0x02: "l2_trigger",
    0x03: "right_x", 0x04: "right_y", 0x05: "r2_trigger",
    0x10: "dpad_x", 0x11: "dpad_y",
}


def _ioc_read(number: int, size: int) -> int:
    """Request code of a joystick ioctl that reads `size` bytes (x86, ARM and RISC-V encoding)."""
    return (2 << 30) | (size << 16) | (ord("j") << 8) | number


# Kernel joystick API (linux/joystick.h).
JS_EVENT = struct.Struct("IhBB")            # time in ms, value, type, number
JS_EVENT_BUTTON, JS_EVENT_AXIS, JS_EVENT_INIT = 0x01, 0x02, 0x80
JS_AXIS_MAX = 32767
JSIOCGAXES = _ioc_read(0x11, 1)             # number of axes
JSIOCGBUTTONS = _ioc_read(0x12, 1)          # number of buttons
JSIOCGAXMAP = _ioc_read(0x32, 0x40)         # event code of each axis, u8
JSIOCGBTNMAP = _ioc_read(0x34, 0x400)       # event code of each button, u16


class DualSenseError(RuntimeError):
    """The controller is missing, ambiguous, disconnected, or not driven by hid_playstation."""


class Event(NamedTuple):
    kind: str       # "button" or "axis"
    name: str       # a key of DualSense.buttons or DualSense.axes
    value: float    # button: 1.0 pressed, 0.0 released; axis: -1.0 to 1.0


def find_controllers() -> list[Path]:
    """The /dev/input/jsN paths of all connected DualSense gamepads."""
    found = []
    for js in sorted(Path("/sys/class/input").glob("js*")):
        device = js / "device"
        if (device / "id" / "vendor").read_text().strip() != SONY_VENDOR_ID:
            continue
        if (device / "id" / "product").read_text().strip() not in PRODUCT_IDS:
            continue
        # The controller's motion sensors are a second joystick device, one without buttons.
        if (device / "capabilities" / "key").read_text().strip() == "0":
            continue
        found.append(Path("/dev/input") / js.name)
    return found


class DualSense:
    """An open DualSense controller: its current state and a stream of input events."""

    def __init__(self, device: str | os.PathLike | None = None) -> None:
        """Open `device` (a /dev/input/jsN path), or the one connected controller if None."""
        if device is None:
            found = find_controllers()
            if not found:
                raise DualSenseError("no DualSense controller is connected (./setup.sh connects it)")
            if len(found) > 1:
                paths = ", ".join(str(path) for path in found)
                raise DualSenseError(f"several DualSense controllers are connected, choose one: {paths}")
            device = found[0]
        self.path = Path(device).resolve()
        self._sysfs = Path("/sys/class/input") / self.path.name / "device"
        self.name = (self._sysfs / "name").read_text().strip()
        self.connection = CONNECTIONS[(self._sysfs / "id" / "bustype").read_text().strip()]
        self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)

        # The driver numbers its buttons and axes; ask which event code each number stands for.
        count = array.array("B", [0])
        codes = array.array("H", [0] * 0x200)
        fcntl.ioctl(self._fd, JSIOCGBUTTONS, count)
        fcntl.ioctl(self._fd, JSIOCGBTNMAP, codes)
        self._button_names = [BUTTONS.get(code) for code in codes[:count[0]]]
        codes = array.array("B", [0] * 0x40)
        fcntl.ioctl(self._fd, JSIOCGAXES, count)
        fcntl.ioctl(self._fd, JSIOCGAXMAP, codes)
        self._axis_names = [AXES.get(code) for code in codes[:count[0]]]
        missing = (set(BUTTONS.values()) - set(self._button_names)) | (set(AXES.values()) - set(self._axis_names))
        if missing:
            os.close(self._fd)
            raise DualSenseError(f"{self.path} does not report {sorted(missing)}; is hid_playstation driving it?")

        self.buttons = {name: False for name in BUTTONS.values()}
        self.axes = {name: 0.0 for name in AXES.values()}
        self.poll()     # on open the kernel queues the current state of every button and axis

    def poll(self) -> list[Event]:
        """Read all pending input without blocking, update `buttons` and `axes`, return the changes."""
        events = []
        while True:
            try:
                data = os.read(self._fd, JS_EVENT.size * 64)
            except BlockingIOError:
                return events
            except OSError as error:
                if error.errno == errno.ENODEV:
                    raise DualSenseError("the controller was disconnected") from error
                raise
            for _, raw, kind, number in JS_EVENT.iter_unpack(data):
                if kind & JS_EVENT_BUTTON:
                    name = self._button_names[number]
                    if name is None:
                        continue
                    self.buttons[name] = bool(raw)
                    event = Event("button", name, float(raw))
                else:
                    name = self._axis_names[number]
                    if name is None:
                        continue
                    self.axes[name] = raw / JS_AXIS_MAX
                    event = Event("axis", name, self.axes[name])
                if not kind & JS_EVENT_INIT:    # the initial state is not a change
                    events.append(event)

    def battery(self) -> tuple[int, str]:
        """Charge in percent (the controller reports steps of 10) and status, e.g. "Discharging"."""
        try:
            (supply,) = (self._sysfs / "device" / "power_supply").iterdir()
            return int((supply / "capacity").read_text()), (supply / "status").read_text().strip()
        except FileNotFoundError as error:
            raise DualSenseError("the controller was disconnected") from error

    def fileno(self) -> int:
        """File descriptor, so that select() can wait for input on the controller."""
        return self._fd

    def close(self) -> None:
        os.close(self._fd)

    def __enter__(self) -> DualSense:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def main() -> None:
    """Print the status of the connected controller."""
    try:
        with DualSense() as pad:
            level, status = pad.battery()
            print(f"controller : {pad.name}")
            print(f"device     : {pad.path} ({pad.connection})")
            print(f"battery    : {level}% ({status})")
            print(f"inputs     : {len(pad.buttons)} buttons, {len(pad.axes)} axes")
    except DualSenseError as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()
