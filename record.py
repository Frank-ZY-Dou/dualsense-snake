#!/usr/bin/env python3
"""Record what a PS5 DualSense controller does, and play it back later.

    ./record.py                 press r to start recording and r again to stop; Ctrl+C quits
    ./record.py --key space     use another key: a character, or space, enter or tab
    ./record.py --dir DIR       save the recordings in DIR instead of the current directory

The key is read from the terminal that this runs in, so press it while that terminal has the
keyboard. Meanwhile the controller can drive any other program: both receive its input.

Every recording is a CSV file, dualsense-<date>-<time>.csv, with one row per change:

    time,kind,name,value
    0.000,button,cross,0            first the state at the start: one row for each button
    0.000,axis,left_x,0.00000       and each axis, at time 0
    1.204,button,cross,1            then every change, with the seconds since the start
    2.016,axis,left_x,-0.42189
    7.250,end,,                     and the moment the recording was stopped

Names and values are those of dualsense.py: a button is 1 while it is pressed, an axis runs
from -1 to 1.

./padview.py FILE plays a recording on the picture of the controller. A program reads one
through Replay(FILE), which stands in for DualSense(), and writes one with Recorder.

Standard library only. Needs dualsense.py.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import math
import os
import select
import signal
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Iterator

from dualsense import AXES, BUTTONS, JS_AXIS_MAX, DualSense, DualSenseError, Event

HEADER = "time,kind,name,value"
KEY_NAMES = {"space": " ", "enter": "\n", "tab": "\t"}
REPEAT_SECONDS = 0.8    # a key that is held down types itself again; presses closer together than this count once
PROGRESS_SECONDS = 0.1  # how often the terminal shows the length of the running recording

# The Linux timer that select() can wait for (timerfd). Python has it built in only from 3.13 on.
_libc = ctypes.CDLL(None, use_errno=True)
TFD_TIMER_ABSTIME = 1


class _Time(ctypes.Structure):      # struct timespec
    _fields_ = [("seconds", ctypes.c_long), ("nanoseconds", ctypes.c_long)]


class _Timer(ctypes.Structure):     # struct itimerspec
    _fields_ = [("interval", _Time), ("value", _Time)]


class EndOfRecording(DualSenseError):
    """A recording was played to its end. A program written for the controller sees it leave."""


def _row(seconds: float, event: Event) -> str:
    """The line of a recording that says `event` happened `seconds` after the start."""
    value = f"{event.value:.0f}" if event.kind == "button" else f"{round(event.value, 5) + 0.0:.5f}"
    return f"{seconds:.3f},{event.kind},{event.name},{value}\n"


class Recorder:
    """A recording that is being written: the state of a controller at the start, then its changes.

        with Recorder("jump.csv", pad) as recorder:
            ...
            recorder.write(pad.poll())      # wherever the program polls the controller
    """

    def __init__(self, path: str | os.PathLike, pad: DualSense) -> None:
        """Start a recording in the new file `path`, with the present state of `pad`."""
        self.path = Path(path)
        self.events = 0                         # number of changes written so far
        self._file = open(self.path, "x")       # "x": never write over an existing recording
        self._start = time.monotonic()
        state = [Event("button", name, float(down)) for name, down in pad.buttons.items()]
        state += [Event("axis", name, value) for name, value in pad.axes.items()]
        self._file.write(HEADER + "\n" + "".join(_row(0, event) for event in state))
        self._file.flush()

    def seconds(self) -> float:
        """How long ago the recording started."""
        return time.monotonic() - self._start

    def write(self, events: list[Event]) -> None:
        """Add `events`, which poll() has just returned, at the present time."""
        now = self.seconds()
        self._file.write("".join(_row(now, event) for event in events))
        self._file.flush()      # if the program dies, the file has everything up to this moment
        self.events += len(events)

    def close(self) -> None:
        """End the recording."""
        self._file.write(f"{self.seconds():.3f},end,,\n")
        self._file.close()

    def __enter__(self) -> Recorder:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _load(path: Path) -> tuple[dict[str, float], list[tuple[float, Event]], float]:
    """Read a recording: the state at its start, its events with their times, and its length in seconds."""
    names = {"button": set(BUTTONS.values()), "axis": set(AXES.values())}
    inputs = len(BUTTONS) + len(AXES)
    # A byte that is not ASCII turns into a character that fails the checks below.
    lines = path.read_text(encoding="ascii", errors="replace").splitlines()
    if not lines or lines[0] != HEADER:
        raise ValueError(f"{path} is not a recording: its first line is not {HEADER}")
    state: dict[str, float] = {}
    events: list[tuple[float, Event]] = []
    clock = 0.0
    duration = None
    for number, line in enumerate(lines[1:], start=2):
        try:
            if duration is not None:
                raise ValueError                # nothing may follow the end
            seconds, kind, name, value = line.split(",")
            seconds = float(seconds)
            if not seconds >= clock:
                raise ValueError
            clock = seconds
            if (kind, name, value) == ("end", "", ""):
                duration = seconds
                continue
            value = float(value)
            if name not in names[kind] or not (value in (0, 1) if kind == "button" else -1 <= value <= 1):
                raise ValueError
            if kind == "axis":
                value = round(value * JS_AXIS_MAX) / JS_AXIS_MAX    # exactly what the controller reported
            if len(state) < inputs:             # the state at the start comes first
                if seconds != 0 or name in state:
                    raise ValueError
                state[name] = value
            else:
                events.append((seconds, Event(kind, name, value)))
        except (KeyError, ValueError):
            raise ValueError(f"{path}, line {number} is not a valid row of a recording") from None
    if duration is None:
        raise ValueError(f"{path} has no end row: the recording was cut short")
    if len(state) < inputs:
        raise ValueError(f"{path} does not start with the state of all {inputs} buttons and axes")
    return state, events, duration


class Replay:
    """A recording played back in real time, in place of the controller.

    It has the buttons, axes, poll(), fileno() and close() of DualSense, so a program reads it the
    same way and receives the recorded events at the recorded times. The clock of the recording
    starts when the program first calls poll() or fileno(). `duration` is its length in seconds.
    Once it is over, poll() raises EndOfRecording.
    """

    def __init__(self, path: str | os.PathLike) -> None:
        """Open the recording in the file `path`. It is at its start."""
        self.path = Path(path)
        state, self._events, self.duration = _load(self.path)
        self.buttons = {name: bool(state[name]) for name in BUTTONS.values()}
        self.axes = {name: state[name] for name in AXES.values()}
        self._next = 0                          # index of the first event that poll() has not returned yet
        self._start: float | None = None        # time.monotonic() at the start of the recording
        self._timer = _libc.timerfd_create(time.CLOCK_MONOTONIC, os.O_CLOEXEC)
        if self._timer < 0:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))

    def _set_timer(self) -> None:
        """Make fileno() readable at the time of the next event, or at the end of the recording."""
        due = self._events[self._next][0] if self._next < len(self._events) else self.duration
        seconds, nanoseconds = divmod(math.ceil((self._start + due) * 1e9), 10**9)
        timer = _Timer(value=_Time(seconds, nanoseconds))
        if _libc.timerfd_settime(self._timer, TFD_TIMER_ABSTIME, ctypes.byref(timer), None):
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))

    def poll(self) -> list[Event]:
        """Return the events that have become due, like DualSense.poll(), and update `buttons` and `axes`."""
        now = time.monotonic()
        if self._start is None:
            self._start = now
        elapsed = now - self._start
        events = []
        while self._next < len(self._events) and self._events[self._next][0] <= elapsed:
            event = self._events[self._next][1]
            if event.kind == "button":
                self.buttons[event.name] = bool(event.value)
            else:
                self.axes[event.name] = event.value
            events.append(event)
            self._next += 1
        if not events and self._next == len(self._events) and elapsed >= self.duration:
            raise EndOfRecording("the recording is over")
        self._set_timer()
        return events

    def fileno(self) -> int:
        """File descriptor that select() finds readable when an event is due or the recording is over."""
        if self._start is None:
            self._start = time.monotonic()
            self._set_timer()
        return self._timer

    def close(self) -> None:
        os.close(self._timer)

    def __enter__(self) -> Replay:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


@contextlib.contextmanager
def _single_keys(terminal: int) -> Iterator[None]:
    """Let the terminal hand over every key at once, without Enter and without showing it."""
    settings = termios.tcgetattr(terminal)
    tty.setcbreak(terminal)
    try:
        yield
    finally:
        termios.tcsetattr(terminal, termios.TCSAFLUSH, settings)


def _record(pad: DualSense, key: bytes, directory: Path) -> None:
    """Start a recording of `pad` in `directory` whenever `key` is pressed in the terminal, and stop it
    when the key is pressed again. Returns when the terminal is closed."""
    keyboard = sys.stdin.fileno()
    recorder: Recorder | None = None
    pressed = shown = -math.inf     # when the key was last pressed, and when the progress was last shown
    try:
        while True:
            wait = None if recorder is None else max(0.0, shown + PROGRESS_SECONDS - time.monotonic())
            ready = select.select([pad, keyboard], [], [], wait)[0]
            now = time.monotonic()
            if pad in ready:
                events = pad.poll()
                if recorder is not None:
                    recorder.write(events)
            if keyboard in ready:
                typed = os.read(keyboard, 64)
                if not typed:
                    return
                # An arrow or function key arrives as Esc followed by more characters. That is not the key.
                if not typed.startswith(b"\x1b") and key in typed.lower():
                    repeated, pressed = now - pressed < REPEAT_SECONDS, now
                    if not repeated and recorder is None:
                        recorder = Recorder(directory / time.strftime("dualsense-%Y%m%d-%H%M%S.csv"), pad)
                    elif not repeated:
                        _finish(recorder)
                        recorder = None
            if recorder is not None and now - shown >= PROGRESS_SECONDS:
                shown = now
                print(f"\r\033[Krecording {recorder.path.name}  {recorder.seconds():.1f} s, "
                      f"{recorder.events} events", end="", flush=True)
    finally:
        if recorder is not None:
            _finish(recorder)


def _finish(recorder: Recorder) -> None:
    """End the recording and say where it is, on the line that showed its progress."""
    recorder.close()
    print(f"\r\033[Ksaved {recorder.path}  {recorder.seconds():.1f} s, {recorder.events} events", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record what the DualSense controller does. A key pressed in this terminal starts a "
                    "recording, the same key stops it, and Ctrl+C quits. ./padview.py FILE plays one back.")
    parser.add_argument("--key", default="r", metavar="KEY",
                        help="the key that starts and stops a recording: a character, "
                             "or space, enter or tab (default: r)")
    parser.add_argument("--dir", default=".", metavar="DIR", type=Path,
                        help="where the recordings are saved (default: the current directory)")
    options = parser.parse_args()
    key = KEY_NAMES.get(options.key.lower(), options.key.lower())
    if len(key) != 1 or not key.isascii() or not (key.isprintable() or key in KEY_NAMES.values()):
        parser.error("--key takes one plain character, or space, enter or tab")
    label = {character: name for name, character in KEY_NAMES.items()}.get(key, key)
    if not options.dir.is_dir():
        sys.exit(f"error: {options.dir} is not a directory")
    if not sys.stdin.isatty():
        sys.exit("error: the key is read from the terminal, so the input of this program cannot be redirected")
    try:
        pad = DualSense()
    except DualSenseError as error:
        sys.exit(f"error: {error}")
    for number in (signal.SIGHUP, signal.SIGTERM):      # end the way Ctrl+C does, which finishes a recording
        signal.signal(number, signal.default_int_handler)
    print(f"{pad.name} ({pad.connection})")
    print(f"Press {label} to start recording and {label} again to stop. Ctrl+C quits.", flush=True)
    try:
        with _single_keys(sys.stdin.fileno()):
            _record(pad, key.encode(), options.dir)
    except KeyboardInterrupt:
        pass
    except (DualSenseError, OSError) as error:
        sys.exit(f"error: {error}")


if __name__ == "__main__":
    main()
