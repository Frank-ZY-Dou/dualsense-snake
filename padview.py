#!/usr/bin/python3
"""A lifelike picture of the DualSense controller that shows live what is pressed.

    ./padview.py        a small window with the controller, kept above other windows,
                        at the bottom left of the screen

draw_controller() draws the same picture into any cairo context, e.g. inside a game window.

Needs PyGObject with GTK 3 and cairo. The system Python of a desktop Linux has them, so
start this file as ./padview.py or with /usr/bin/python3.
"""
from __future__ import annotations

import math
import os
import sys

if __name__ == "__main__":
    # Only an X11 client can place its window and keep it above the others, so on a Wayland
    # desktop the viewer runs through XWayland. GTK has to be told before it is loaded.
    os.environ["GDK_BACKEND"] = "x11"

try:
    import cairo
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_foreign("cairo")
    from gi.repository import Gdk, GLib, Gtk
except (ImportError, ValueError) as error:
    sys.exit(f"error: {error}.\nThis needs PyGObject with GTK 3 and cairo, which belong to the system Python: "
             "start it as ./padview.py or ./snake.py, or with /usr/bin/python3.")

from dualsense import DualSense, DualSenseError

TAU = 2 * math.pi
ASPECT = 0.74           # height of the picture divided by its width
ACCENT = "#3d9eff"      # colour of the light bar and of everything that is pressed
LIT = ((0, "#a6d8ff"), (1, "#1f78f0"))      # gradient of a pressed part
GLYPH = "#9a9da6"       # colour of the symbols on the buttons
STICK_TRAVEL = 20       # how far a fully pushed stick moves in the picture

# The picture is designed on a grid 1000 wide and 740 high and is symmetric about x = 500.
# An outline is given by its top point on the axis and the cubic curves of its left half, down to the axis.
BODY = ((500, 97), (
    ((430, 95), (350, 95), (290, 100)),         # top edge
    ((225, 104), (150, 108), (112, 148)),       # shoulder
    ((78, 184), (52, 262), (44, 345)),          # side
    ((36, 430), (50, 552), (80, 628)),          # outside of the grip
    ((97, 672), (126, 699), (160, 696)),        # tip of the grip
    ((200, 693), (224, 652), (246, 598)),
    ((268, 546), (296, 508), (350, 498)),       # inside of the grip
    ((400, 489), (450, 487), (500, 487)),       # bottom edge between the grips
))
PANEL = ((500, 99), (                           # the glossy piece: middle of the body and inside of the grips
    ((430, 97), (350, 97), (286, 102)),
    ((280, 160), (272, 250), (268, 300)),       # down beside the touchpad
    ((256, 330), (246, 372), (248, 412)),       # around the stick
    ((250, 470), (232, 560), (196, 640)),       # along the inside of the grip
    ((186, 662), (176, 684), (170, 720)),
    ((300, 730), (400, 730), (500, 730)),       # below the body, which cuts it off
))
TOUCHPAD = ((500, 100), (
    ((430, 98), (360, 99), (296, 103)),
    ((300, 170), (308, 235), (318, 272)),
    ((322, 290), (334, 300), (354, 301)),
    ((410, 303), (460, 304), (500, 304)),
))
DPAD = (192, 275)       # centre of the four direction buttons
FACE = (808, 275)       # centre of the four face buttons
FACE_BUTTONS = {"triangle": (0, -58), "circle": (58, 0), "cross": (0, 58), "square": (-58, 0)}
STICKS = {"left": (340, 385, "l3"), "right": (660, 385, "r3")}


def _rgba(code: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    return (int(code[1:3], 16) / 255, int(code[3:5], 16) / 255, int(code[5:7], 16) / 255, alpha)


def _stops(pattern: cairo.Gradient, stops: tuple) -> cairo.Gradient:
    for offset, code, *alpha in stops:
        pattern.add_color_stop_rgba(offset, *_rgba(code, *alpha))
    return pattern


def _linear(x0: float, y0: float, x1: float, y1: float, *stops: tuple) -> cairo.Gradient:
    return _stops(cairo.LinearGradient(x0, y0, x1, y1), stops)


def _radial(x: float, y: float, inner: float, outer: float, *stops: tuple) -> cairo.Gradient:
    return _stops(cairo.RadialGradient(x, y, inner, x, y, outer), stops)


def _outline(cr: cairo.Context, shape: tuple) -> None:
    """Path of a symmetric outline such as BODY."""
    start, curves = shape
    points = [start] + [point for curve in curves for point in curve]
    cr.move_to(*start)
    for first, second, end in curves:
        cr.curve_to(*first, *second, *end)
    mirrored = [(1000 - x, y) for x, y in reversed(points)]
    for i in range(1, len(mirrored), 3):
        cr.curve_to(*mirrored[i], *mirrored[i + 1], *mirrored[i + 2])
    cr.close_path()


def _rounded(cr: cairo.Context, x: float, y: float, width: float, height: float, radius: float) -> None:
    """Path of a rectangle with rounded corners."""
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -TAU / 4, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, TAU / 4)
    cr.arc(x + radius, y + height - radius, radius, TAU / 4, TAU / 2)
    cr.arc(x + radius, y + radius, radius, TAU / 2, 3 * TAU / 4)
    cr.close_path()


def _label(cr: cairo.Context, text: str, x: float, y: float, size: float, code: str) -> None:
    """Text centred on (x, y)."""
    cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(size)
    extents = cr.text_extents(text)
    cr.move_to(x - extents.x_bearing - extents.width / 2, y - extents.y_bearing - extents.height / 2)
    cr.set_source_rgba(*_rgba(code))
    cr.show_text(text)


def _glow(cr: cairo.Context, x: float, y: float, radius: float) -> None:
    """Light around something pressed."""
    cr.set_source(_radial(x, y, radius * 0.6, radius, (0, ACCENT, 0.6), (1, ACCENT, 0)))
    cr.arc(x, y, radius, 0, TAU)
    cr.fill()


def _part(cr: cairo.Context, x: float, y: float, radius: float, down: bool) -> None:
    """Paint the current path as a glossy black part sunk into the shell; lit while it is down.
    (x, y) and radius say roughly where and how big it is, for the shading."""
    cr.set_source_rgba(0, 0, 0, 0.55)       # the gap around the part
    cr.set_line_width(7)
    cr.stroke_preserve()
    if down:
        cr.set_source(_radial(x - radius * 0.3, y - radius * 0.3, 0, radius * 1.4, *LIT))
    else:
        cr.set_source(_radial(x - radius * 0.3, y - radius * 0.4, 0, radius * 1.4, (0, "#34343a"), (1, "#0a0a0b")))
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.6 if down else 0.18)
    cr.set_line_width(1.6)
    cr.stroke()


def _shoulders(cr: cairo.Context, pad: DualSense) -> None:
    """L2/R2 and L1/R1. They lie behind the body, which hides their lower parts."""
    for side, mirror in (("l", False), ("r", True)):
        depth = (pad.axes[f"{side}2_trigger"] + 1) / 2      # how far the trigger is pulled, 0 to 1
        bumper = pad.buttons[f"{side}1"]
        cr.save()
        if mirror:
            cr.translate(1000, 0)
            cr.scale(-1, 1)
        cr.move_to(148, 130)            # the trigger
        cr.line_to(158, 44)
        cr.curve_to(160, 26, 172, 14, 192, 14)
        cr.line_to(252, 14)
        cr.curve_to(270, 14, 282, 26, 282, 44)
        cr.line_to(282, 130)
        cr.close_path()
        trigger = cr.copy_path()
        cr.set_source(_linear(0, 14, 0, 80, (0, "#45454c"), (1, "#151517")))
        cr.fill_preserve()
        cr.save()
        cr.clip()
        cr.rectangle(140, 72 - 58 * depth, 150, 58 * depth)     # fills from below as the trigger is pulled
        cr.set_source(_linear(0, 14, 0, 80, *LIT))
        cr.fill()
        cr.restore()
        cr.append_path(trigger)
        cr.set_source_rgba(1, 1, 1, 0.20)
        cr.set_line_width(1.6)
        cr.stroke()
        cr.move_to(300, 180)            # the bumper, wrapped around the shoulder
        cr.line_to(300, 82)
        cr.curve_to(300, 70, 292, 64, 280, 64)
        cr.curve_to(230, 64, 170, 68, 138, 86)
        cr.curve_to(110, 102, 92, 132, 90, 180)
        cr.close_path()
        cr.set_source(_linear(0, 64, 0, 130, *(LIT if bumper else ((0, "#55555d"), (1, "#1a1a1d")))))
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.5 if bumper else 0.22)
        cr.stroke()
        cr.restore()
        x = 780 if mirror else 220
        _label(cr, f"{side.upper()}2", x, 40, 21, "#ffffff" if depth >= 0.5 else GLYPH)
        _label(cr, f"{side.upper()}1", x, 85, 18, "#ffffff" if bumper else GLYPH)


def _body(cr: cairo.Context) -> None:
    """Shadow, shell, glossy piece, touchpad, light bars and the small fixed details."""
    for step in range(6):               # a soft shadow under the controller
        cr.save()
        cr.translate(0, 8 + 3 * step)
        _outline(cr, BODY)
        cr.set_source_rgba(0, 0, 0, 0.045)
        cr.fill_preserve()
        cr.set_line_width(6 + 7 * step)
        cr.stroke()
        cr.restore()

    _outline(cr, BODY)
    cr.set_source(_linear(0, 95, 0, 700, (0, "#3d3d43"), (0.35, "#2b2b2f"), (1, "#18181b")))
    cr.fill_preserve()
    cr.save()
    cr.clip()
    for x in (200, 800):                # light falling on the two wings of the shell
        cr.set_source(_radial(x, 215, 0, 250, (0, "#ffffff", 0.07), (1, "#ffffff", 0)))
        cr.paint()
    _outline(cr, PANEL)
    cr.set_source(_linear(0, 95, 0, 700, (0, "#1c1c1f"), (0.4, "#0b0b0d"), (1, "#040405")))
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.10)   # the seam between the shell and the glossy piece
    cr.set_line_width(1.6)
    cr.stroke_preserve()
    cr.clip()
    cr.move_to(134, 740)                # a reflection across the glossy piece
    cr.line_to(405, 95)
    cr.line_to(545, 95)
    cr.line_to(274, 740)
    cr.close_path()
    cr.set_source_rgba(1, 1, 1, 0.04)
    cr.fill()
    cr.restore()
    _outline(cr, BODY)
    cr.set_source(_linear(0, 95, 0, 700, (0, "#ffffff", 0.34), (0.5, "#ffffff", 0.12), (1, "#ffffff", 0.06)))
    cr.set_line_width(2.2)
    cr.stroke()

    for mirror in (False, True):        # light bars along both sides of the touchpad
        cr.save()
        if mirror:
            cr.translate(1000, 0)
            cr.scale(-1, 1)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for width, alpha in ((16, 0.10), (9, 0.22), (3.5, 1.0)):
            cr.move_to(289, 112)
            cr.curve_to(293, 172, 301, 236, 311, 270)
            cr.set_source_rgba(*_rgba(ACCENT, alpha))
            cr.set_line_width(width)
            cr.stroke()
        cr.restore()

    _outline(cr, TOUCHPAD)
    cr.set_source(_linear(0, 100, 0, 304, (0, "#3a3a40"), (1, "#232327")))
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.14)
    cr.set_line_width(1.6)
    cr.stroke()

    for i in range(5):                  # player lights under the touchpad
        _rounded(cr, 470 + 13 * i, 314, 8, 4, 2)
    cr.set_source_rgba(1, 1, 1, 0.22)
    cr.fill()
    for row, count in enumerate((5, 6, 5)):     # speaker
        for i in range(count):
            cr.arc(500 + 11 * (i - (count - 1) / 2), 338 + 9 * row, 2.2, 0, TAU)
            cr.fill()
    _rounded(cr, 478, 446, 44, 13, 6.5)         # mute button
    cr.set_source_rgba(*_rgba("#1d1d20"))
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.15)
    cr.set_line_width(1.4)
    cr.stroke()


def _small_buttons(cr: cairo.Context, pad: DualSense) -> None:
    """Create, Options and the PS button."""
    for name, x, angle in (("create", 262, -0.2), ("options", 738, 0.2)):
        down = pad.buttons[name]
        if down:
            _glow(cr, x, 160, 44)
        cr.save()
        cr.translate(x, 160)
        cr.rotate(angle)
        _rounded(cr, -9, -21, 18, 42, 9)
        cr.restore()
        _part(cr, x, 160, 18, down)
        cr.set_source_rgba(*_rgba(GLYPH))       # the marks printed above the two buttons
        cr.set_line_width(2.2)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        for i in (-1, 0, 1):
            if name == "create":
                cr.move_to(x - 4 + 7 * i, 127 + 2 * abs(i))
                cr.line_to(x - 4 + 11 * i, 120 + 3 * abs(i))
            else:
                cr.move_to(x - 3, 119 + 5 * i)
                cr.line_to(x + 11, 119 + 5 * i)
            cr.stroke()
    down = pad.buttons["ps"]
    if down:
        _glow(cr, 500, 410, 52)
    _rounded(cr, 470, 396, 60, 28, 14)
    _part(cr, 500, 410, 30, down)


def _dpad(cr: cairo.Context, pad: DualSense) -> None:
    """Four separate direction buttons, each pointing at the centre."""
    held = (pad.axes["dpad_y"] < -0.5, pad.axes["dpad_x"] > 0.5,       # up, right, down, left
            pad.axes["dpad_y"] > 0.5, pad.axes["dpad_x"] < -0.5)
    for quarter, down in enumerate(held):
        cr.save()
        cr.translate(*DPAD)
        cr.rotate(quarter * TAU / 4)
        if down:
            _glow(cr, 0, -50, 50)
        cr.new_sub_path()               # drawn as the button pointing up
        cr.arc(-13, -72, 10, TAU / 2, 3 * TAU / 4)
        cr.arc(13, -72, 10, 3 * TAU / 4, TAU)
        cr.line_to(23, -42)
        cr.line_to(3, -19)
        cr.arc(0, -22, 4.2, 0.7, TAU / 2 - 0.7)
        cr.line_to(-23, -42)
        cr.close_path()
        _part(cr, 0, -50, 34, down)
        cr.move_to(0, -71)              # the small arrow printed on it
        cr.line_to(7, -61)
        cr.line_to(-7, -61)
        cr.close_path()
        cr.set_source_rgba(*_rgba("#ffffff" if down else GLYPH))
        cr.fill()
        cr.restore()


def symbol(cr: cairo.Context, name: str, x: float, y: float, size: float) -> None:
    """Path of the symbol of a face button ("triangle", "circle", "cross" or "square") around (x, y);
    `size` is half its width. Stroke it to draw it."""
    if name == "triangle":
        cr.move_to(x, y - 1.07 * size)
        cr.line_to(x + size, y + 0.67 * size)
        cr.line_to(x - size, y + 0.67 * size)
        cr.close_path()
    elif name == "circle":
        cr.new_sub_path()
        cr.arc(x, y, 0.9 * size, 0, TAU)
    elif name == "cross":
        cr.move_to(x - 0.8 * size, y - 0.8 * size)
        cr.line_to(x + 0.8 * size, y + 0.8 * size)
        cr.move_to(x + 0.8 * size, y - 0.8 * size)
        cr.line_to(x - 0.8 * size, y + 0.8 * size)
    else:
        cr.rectangle(x - 0.8 * size, y - 0.8 * size, 1.6 * size, 1.6 * size)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)


def _face_buttons(cr: cairo.Context, pad: DualSense) -> None:
    for name, (dx, dy) in FACE_BUTTONS.items():
        x, y, down = FACE[0] + dx, FACE[1] + dy, pad.buttons[name]
        if down:
            _glow(cr, x, y, 58)
        cr.arc(x, y, 31, 0, TAU)
        _part(cr, x, y, 31, down)
        symbol(cr, name, x, y, 15)
        cr.set_source_rgba(*_rgba("#ffffff" if down else GLYPH))
        cr.set_line_width(3.6)
        cr.stroke()


def _sticks(cr: cairo.Context, pad: DualSense) -> None:
    for side, (x, y, button) in STICKS.items():
        cr.arc(x, y, 64, 0, TAU)        # the well the stick sits in
        cr.set_source(_radial(x, y, 40, 64, (0, "#000000"), (1, "#1a1a1d")))
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.14)
        cr.set_line_width(1.6)
        cr.stroke()
        down = pad.buttons[button]
        x += STICK_TRAVEL * pad.axes[f"{side}_x"]
        y += STICK_TRAVEL * pad.axes[f"{side}_y"]
        if down:
            _glow(cr, x, y, 76)
        cr.arc(x, y, 50, 0, TAU)        # the cap: a grippy rim around a shallow dish
        cr.set_source(_radial(x - 14, y - 18, 0, 70, (0, "#3d3d43"), (1, "#0c0c0d")))
        cr.fill_preserve()
        cr.set_source_rgba(*(_rgba(ACCENT) if down else (1, 1, 1, 0.22)))
        cr.set_line_width(3 if down else 1.6)
        cr.stroke()
        cr.arc(x, y, 34, 0, TAU)
        cr.set_source(_radial(x + 8, y + 10, 0, 44, (0, "#2e2e33"), (1, "#070708")))
        cr.fill_preserve()
        cr.set_source_rgba(*_rgba(ACCENT if down else "#000000", 0.9 if down else 0.6))
        cr.set_line_width(2)
        cr.stroke()


_body_images: dict[int, cairo.ImageSurface] = {}    # what _body() draws, by width in screen pixels


def _body_image(pixels: int) -> cairo.ImageSurface:
    """The parts that never change. They take most of the drawing time, so they are drawn once per size."""
    if pixels not in _body_images:
        image = cairo.ImageSurface(cairo.FORMAT_ARGB32, pixels, math.ceil(pixels * ASPECT))
        cr = cairo.Context(image)
        cr.scale(pixels / 1000, pixels / 1000)
        _body(cr)
        _body_images[pixels] = image
    return _body_images[pixels]


def draw_controller(cr: cairo.Context, pad: DualSense, x: float, y: float, width: float) -> None:
    """Draw the controller `width` wide (and width * ASPECT high) with its top left corner at (x, y),
    lighting up what is pressed on `pad` and moving its sticks. Whole numbers for x and y keep it sharp."""
    # Screen pixels per unit of x, y and width; more than 1 on a high-resolution screen.
    density = cr.user_to_device_distance(1, 0)[0] * cr.get_target().get_device_scale()[0]
    pixels = round(width * density)
    cr.save()
    cr.translate(x, y)
    cr.save()
    cr.scale(width / 1000, width / 1000)
    _shoulders(cr, pad)
    cr.restore()
    cr.save()
    cr.scale(width / pixels, width / pixels)
    cr.set_source_surface(_body_image(pixels), 0, 0)
    cr.paint()
    cr.restore()
    cr.scale(width / 1000, width / 1000)
    _small_buttons(cr, pad)
    _dpad(cr, pad)
    _face_buttons(cr, pad)
    _sticks(cr, pad)
    cr.restore()


def main() -> None:
    """Show the controller in a window above all others until the window is closed."""
    width, margin = 420, 14
    failure: list[DualSenseError] = []

    def on_draw(_: Gtk.Widget, cr: cairo.Context) -> None:
        cr.set_source_rgba(*_rgba("#3a4252"))
        cr.paint()
        draw_controller(cr, pad, margin, margin, width)

    def on_input(*_: object) -> bool:
        try:
            pad.poll()
        except DualSenseError as error:
            failure.append(error)
            Gtk.main_quit()
            return False
        area.queue_draw()
        return True

    if not Gtk.init_check()[0]:
        sys.exit("error: there is no graphical desktop to open a window on")
    try:
        pad = DualSense()
    except DualSenseError as error:
        sys.exit(f"error: {error}")
    window = Gtk.Window(title="DualSense")
    window.set_resizable(False)
    window.set_keep_above(True)
    area = Gtk.DrawingArea()
    area.set_size_request(width + 2 * margin, round(width * ASPECT) + 2 * margin)
    area.connect("draw", on_draw)
    window.add(area)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    work = Gdk.Display.get_default().get_primary_monitor().get_workarea()
    window.move(work.x + 24, work.y + work.height - window.get_size()[1] - 64)     # bottom left of the screen
    GLib.io_add_watch(pad.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, on_input)
    Gtk.main()
    if failure:
        sys.exit(f"error: {failure[0]}")


if __name__ == "__main__":
    main()
