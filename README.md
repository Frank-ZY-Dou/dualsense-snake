<div align="center">

# dualsense-snake

**A PS5 DualSense on Linux, in plain Python.**

One script pairs the controller over Bluetooth. Then read it from your own code, watch it<br>
live on screen, record what it does and play that back, or just play Snake with it.

<p>
<img alt="Linux 5.12+" src="https://img.shields.io/badge/Linux-5.12%2B-3d9eff?style=flat-square&logo=linux&logoColor=white">
<img alt="Python 3.8+" src="https://img.shields.io/badge/Python-3.8%2B-3d9eff?style=flat-square&logo=python&logoColor=white">
<img alt="GTK 3" src="https://img.shields.io/badge/GTK-3-3d9eff?style=flat-square&logo=gtk&logoColor=white">
<img alt="Bluetooth" src="https://img.shields.io/badge/Bluetooth-BlueZ-3d9eff?style=flat-square&logo=bluetooth&logoColor=white">
<a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/License-MIT-3d9eff?style=flat-square"></a>
</p>

**English** | [简体中文](README.zh-CN.md)

<img src="assets/demo.gif" width="820" alt="The Snake window. The controller drawn in its corner lights up whatever is pressed.">

</div>

## What is in here

| File | What it does |
|---|---|
| [`setup.sh`](setup.sh) | Checks the kernel driver and Python, pairs and connects the controller over Bluetooth, and verifies that Python can read it. No root needed. |
| [`dualsense.py`](dualsense.py) | The reader: buttons, sticks, triggers, D-pad and battery level. One file, standard library only. |
| [`padview.py`](padview.py) | A picture of the controller that lights up what you press, drawn with cairo. Runs as a small always-on-top window, or inside your own program. |
| [`record.py`](record.py) | Records what the controller does into a CSV file, started and stopped with a key, and plays a recording back. Standard library only. |
| [`snake.py`](snake.py) | Snake, as something to steer. |

## Quick start

```bash
git clone https://github.com/Frank-ZY-Dou/dualsense-snake.git
cd dualsense-snake
./setup.sh
./snake.py
```

`setup.sh` asks you to put the controller into pairing mode: switch it off, then hold **Create** (the small button at the top left of the touchpad) and **PS** together for a few seconds, until the light bar blinks quickly. That is needed once per computer. From then on, pressing PS reconnects the controller. The script prints its messages in Chinese.

You need:

- Linux 5.12 or newer (for the `hid_playstation` driver) and BlueZ. Ubuntu 22.04 has both. Ubuntu 20.04 needs its HWE kernel (5.13 or newer), because its original 5.4 kernel does not ship the driver
- Python 3.8 or newer
- for the two windows, PyGObject with GTK 3 and pycairo. A desktop Ubuntu usually has them already; if not: `sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0`

## Controls

| Controller | Snake |
|---|---|
| D-pad or left stick | steer |
| △ □ ✕ ○ | speed 1, 2, 3, 4 |
| Options | start, pause, new game |

An apple is worth as many points as the current speed. `q` or `Esc` on the keyboard quits.

## The controller on screen

`./padview.py` shows the picture on its own, in a small window at the bottom left of the screen that stays above other windows. Keep it next to whatever the controller is driving.

<p align="center">
<img width="448" src="assets/padview.gif" alt="The viewer window going through all inputs of the controller">
</p>

Buttons light up while they are held, the stick caps move, and L2 and R2 fill up as far as they are pulled. Two things stay dark, the touchpad click and the mute button: the kernel's gamepad device does not report them.

It is a vector drawing, so it stays sharp at any size. To put it into your own GTK or cairo program:

```python
from padview import draw_controller

draw_controller(cr, pad, x, y, width)   # cr is a cairo context
```

## Reading the controller in your own program

`dualsense.py` has no dependencies and does not need the other files.

```python
import select
from dualsense import DualSense

with DualSense() as pad:
    print(pad.name, pad.connection, pad.battery())
    while True:
        select.select([pad], [], [])     # sleep until something happens
        for event in pad.poll():         # Event(kind, name, value)
            print(event)
        if pad.buttons["cross"] and pad.axes["left_y"] < -0.5:
            ...                          # the current state is always there
```

| | Names |
|---|---|
| Buttons | `cross` `circle` `triangle` `square` `l1` `r1` `l2` `r2` `create` `options` `ps` `l3` `r3` |
| Axes | `left_x` `left_y` `right_x` `right_y` `l2_trigger` `r2_trigger` `dpad_x` `dpad_y` |

Axes run from -1.0 to 1.0. x grows to the right and y downwards, so a stick pushed forward reads -1.0. A released trigger reads -1.0, a fully pulled one 1.0.

The object has a `fileno()`, so it plugs into `select`, `asyncio` or a GLib main loop. Run as a program, the file prints what it found:

```console
$ python3 dualsense.py
controller : DualSense Wireless Controller
device     : /dev/input/js0 (bluetooth)
battery    : 95% (Charging)
inputs     : 13 buttons, 8 axes
```

## Recording and replay

`./record.py` writes down what the controller does. Press `r` in its terminal to start a recording and `r` again to stop it. `--key` chooses another key, for example `--key space`. The controller can drive another program at the same time, because both receive its input.

```console
$ ./record.py
DualSense Wireless Controller (bluetooth)
Press r to start recording and r again to stop. Ctrl+C quits.
saved dualsense-20261117-233740.csv  1.2 s, 5 events
```

Each recording is a CSV file in the current directory, or in the one given with `--dir`:

```csv
time,kind,name,value
0.000,button,cross,0
0.000,button,circle,0
...
0.300,button,cross,1
0.450,button,cross,0
0.600,axis,left_x,-0.42189
0.600,axis,left_y,1.00000
0.800,button,square,0
1.200,end,,
```

The first 21 rows are the state at the start: one row for each button and each axis, at time 0. Then comes one row per change, with the seconds since the start. The last row is the moment the recording was stopped. Names and values are those of the table above; a button is 1 while it is pressed.

`./padview.py dualsense-20261117-233740.csv` plays a recording on the on-screen controller, in real time, and closes when it is over.

In a program, `Replay` takes the place of `DualSense`:

```python
from record import Replay

pad = Replay("dualsense-20261117-233740.csv")    # instead of DualSense()
```

It has the same `buttons`, `axes`, `poll()` and `fileno()` and hands out the recorded events at the recorded times, so the rest of the program stays as it is. The clock of the recording starts with the first call of `poll()` or `fileno()`. When the recording is over, `poll()` raises `EndOfRecording`, which is a `DualSenseError`: a program written for the controller sees the controller going away.

To record from inside a program, give what `poll()` returns to a `Recorder`:

```python
from record import Recorder

with Recorder("jump.csv", pad) as recorder:    # begins with the state of pad
    while running:
        events = pad.poll()
        recorder.write(events)                 # at the time of this call
```

## How it works

The kernel driver `hid_playstation` turns the controller into ordinary input devices. This project reads the plainest of them, the joystick device `/dev/input/jsN`. Any user may read it, so there are no udev rules to install, and an event is eight bytes. `dualsense.py` asks the driver once which button and axis each number stands for, and from then on it only reads events. The battery level comes from `/sys/class/power_supply`.

Pairing is where the script earns its place. On the machine this was written on, `bluetoothctl show` said `Pairable: no`. In that state `bluetoothctl pair` still reports success, but BlueZ does not keep the pairing, and it then refuses the controller's input connection; the log of `bluetoothd` says `Rejected connection from !bonded device`. `setup.sh` makes the adapter pairable while it pairs, trusts the controller so that it may reconnect by itself, and puts the setting back afterwards.

## Notes

- If the controller was paired with a console or another computer in the meantime and no longer reconnects, run `./setup.sh --repair`.
- `snake.py` and `padview.py` start with `#!/usr/bin/python3` on purpose, because GTK belongs to the system Python. Inside conda or a virtualenv, `python3 snake.py` does not find it; `./snake.py` does.
- The driver also registers the touchpad as a mouse. On X11, `xinput disable "DualSense Wireless Controller Touchpad"` stops that.
- Tested on Ubuntu 22.04 (X11 session, kernel 6.8, BlueZ 5.64, Python 3.10) with a standard DualSense over Bluetooth. Not tested: Ubuntu 20.04, a Wayland session (there `padview.py` runs through XWayland, the only way for a window to place itself and stay on top), USB, and the DualSense Edge. `record.py` has been tested with simulated controller input only so far.

## License

[MIT](LICENSE). DualSense and PlayStation are trademarks of Sony Interactive Entertainment. This project is not affiliated with Sony.
