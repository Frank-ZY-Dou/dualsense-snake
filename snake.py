#!/usr/bin/python3
"""Snake, steered with a PS5 DualSense controller.

    D-pad or left stick                   steer
    triangle / square / cross / circle    speed 1 / 2 / 3 / 4
    Options                               start, pause, new game
    q or Esc on the keyboard              quit

The window shows the controller at the bottom left and lights up whatever is pressed on it.

Run ./setup.sh once per computer to connect the controller, then start the game as ./snake.py
(it needs the system Python, /usr/bin/python3, for GTK).
"""
from __future__ import annotations

import random
import sys
import time
from collections import deque

from dualsense import DualSense, DualSenseError, Event
from padview import ASPECT, TAU, Gdk, GLib, Gtk, cairo, draw_controller, symbol

BOARD_WIDTH, BOARD_HEIGHT = 20, 20      # in cells
STEP_SECONDS = {1: 0.20, 2: 0.14, 3: 0.10, 4: 0.07}     # time per move at each speed
SPEED_BUTTONS = {"triangle": 1, "square": 2, "cross": 3, "circle": 4}
STICK_THRESHOLD = 0.6                   # left-stick deflection that counts as a direction
BATTERY_SECONDS = 30                    # how often the battery level is read

UP, DOWN, LEFT, RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)

# Layout of the window, in pixels: a panel with the controller on the left, the board on the right.
MARGIN = 20
CELL = 28
PANEL_WIDTH = 440
BOARD_LEFT = MARGIN + PANEL_WIDTH + MARGIN
WINDOW_WIDTH = BOARD_LEFT + BOARD_WIDTH * CELL + MARGIN
WINDOW_HEIGHT = MARGIN + BOARD_HEIGHT * CELL + MARGIN


class Game:
    """Rules and state of one round, independent of the controller and of the screen."""

    def __init__(self, rng: random.Random, speed: int) -> None:
        self.rng = rng
        self.speed = speed
        self.score = 0
        self.over = False
        x, y = BOARD_WIDTH // 2, BOARD_HEIGHT // 2
        self.snake = deque([(x, y), (x - 1, y), (x - 2, y)])    # head first
        self.direction = RIGHT
        self.turns: deque[tuple[int, int]] = deque()            # turns asked for, not yet made
        self.food = self._free_cell()

    @property
    def won(self) -> bool:
        """True once the snake fills the whole board."""
        return self.food is None

    def steer(self, direction: tuple[int, int]) -> None:
        """Ask for a turn. Up to two turns wait for the coming steps; a U-turn is ignored."""
        heading = self.turns[-1] if self.turns else self.direction
        opposite = (-heading[0], -heading[1])
        if len(self.turns) < 2 and direction not in (heading, opposite):
            self.turns.append(direction)

    def step(self) -> None:
        """Move the snake one cell."""
        if self.turns:
            self.direction = self.turns.popleft()
        head = (self.snake[0][0] + self.direction[0], self.snake[0][1] + self.direction[1])
        on_board = 0 <= head[0] < BOARD_WIDTH and 0 <= head[1] < BOARD_HEIGHT
        # The tail moves away in this same step, so its cell is free.
        if not on_board or head in list(self.snake)[:-1]:
            self.over = True
            return
        self.snake.appendleft(head)
        if head == self.food:
            self.score += self.speed
            self.food = self._free_cell()
            self.over = self.won
        else:
            self.snake.pop()

    def _free_cell(self) -> tuple[int, int] | None:
        """A random cell without snake, or None when there is none left."""
        occupied = set(self.snake)
        free = [(x, y) for x in range(BOARD_WIDTH) for y in range(BOARD_HEIGHT) if (x, y) not in occupied]
        return self.rng.choice(free) if free else None


def stick_direction(x: float, y: float) -> tuple[int, int] | None:
    """The direction a stick at (x, y) points to, or None near its centre."""
    if max(abs(x), abs(y)) < STICK_THRESHOLD:
        return None
    if abs(x) > abs(y):
        return RIGHT if x > 0 else LEFT
    return DOWN if y > 0 else UP


class Steering:
    """Turns D-pad and left-stick events into the direction they ask for."""

    def __init__(self) -> None:
        self._stick = {"left_x": 0.0, "left_y": 0.0}
        self._pointing: tuple[int, int] | None = None   # where the stick points at the moment

    def direction(self, event: Event) -> tuple[int, int] | None:
        """The direction `event` asks for, or None if it asks for none."""
        if event.kind != "axis":
            return None
        if event.name in ("dpad_x", "dpad_y") and abs(event.value) > 0.5:
            if event.name == "dpad_x":
                return RIGHT if event.value > 0 else LEFT
            return DOWN if event.value > 0 else UP
        if event.name in self._stick:
            self._stick[event.name] = event.value
            pointing = stick_direction(self._stick["left_x"], self._stick["left_y"])
            if pointing != self._pointing:      # ask once per push, not on every small movement
                self._pointing = pointing
                return pointing
        return None


def _text(cr: cairo.Context, text: str, x: float, y: float, size: float, grey: float,
          bold: bool = False, centred: bool = False) -> None:
    """Text on the baseline y, starting at x or, if `centred`, centred on x."""
    cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    if centred:
        x -= cr.text_extents(text).x_advance / 2
    cr.set_source_rgb(grey, grey, grey)
    cr.move_to(x, y)
    cr.show_text(text)


def _cell(cr: cairo.Context, cell: tuple[int, int], inset: float, radius: float) -> None:
    """Path of a rounded square in a cell of the board."""
    x = BOARD_LEFT + cell[0] * CELL + inset
    y = MARGIN + cell[1] * CELL + inset
    side = CELL - 2 * inset
    cr.new_sub_path()
    cr.arc(x + side - radius, y + radius, radius, -TAU / 4, 0)
    cr.arc(x + side - radius, y + side - radius, radius, 0, TAU / 4)
    cr.arc(x + radius, y + side - radius, radius, TAU / 4, TAU / 2)
    cr.arc(x + radius, y + radius, radius, TAU / 2, 3 * TAU / 4)
    cr.close_path()


def draw(cr: cairo.Context, game: Game, banner: str | None, battery: int, pad: DualSense) -> None:
    """Paint the whole window."""
    background = cairo.LinearGradient(0, 0, 0, WINDOW_HEIGHT)
    background.add_color_stop_rgb(0, 0.24, 0.27, 0.33)
    background.add_color_stop_rgb(1, 0.15, 0.17, 0.21)
    cr.set_source(background)
    cr.paint()

    # The panel: numbers, help, and the controller at the bottom left.
    _text(cr, "SNAKE", MARGIN, 54, 34, 1.0, bold=True)
    for row, (name, value) in enumerate((("score", game.score), ("speed", game.speed), ("battery", f"{battery}%"))):
        _text(cr, name, MARGIN, 96 + 28 * row, 19, 0.66)
        _text(cr, str(value), MARGIN + 92, 96 + 28 * row, 19, 1.0, bold=True)
    _text(cr, "steer: D-pad or left stick", MARGIN + 190, 96, 15, 0.66)
    _text(cr, "speed:", MARGIN + 190, 124, 15, 0.66)
    for i, (name, speed) in enumerate(SPEED_BUTTONS.items()):
        x = MARGIN + 252 + 46 * i
        symbol(cr, name, x, 119, 7)
        cr.set_source_rgb(0.85, 0.87, 0.9)
        cr.set_line_width(2)
        cr.stroke()
        _text(cr, str(speed), x + 13, 124, 15, 1.0, bold=True)
    _text(cr, "Options: start, pause    q: quit", MARGIN + 190, 152, 15, 0.66)
    draw_controller(cr, pad, MARGIN, round(WINDOW_HEIGHT - MARGIN - PANEL_WIDTH * ASPECT), PANEL_WIDTH)

    # The board.
    width, height = BOARD_WIDTH * CELL, BOARD_HEIGHT * CELL
    cr.rectangle(BOARD_LEFT, MARGIN, width, height)
    cr.set_source_rgb(0.06, 0.075, 0.10)
    cr.fill()
    cr.set_source_rgba(1, 1, 1, 0.035)
    cr.set_line_width(1)
    for i in range(1, BOARD_WIDTH):
        cr.move_to(BOARD_LEFT + i * CELL + 0.5, MARGIN)
        cr.rel_line_to(0, height)
    for i in range(1, BOARD_HEIGHT):
        cr.move_to(BOARD_LEFT, MARGIN + i * CELL + 0.5)
        cr.rel_line_to(width, 0)
    cr.stroke()
    cr.rectangle(BOARD_LEFT - 1, MARGIN - 1, width + 2, height + 2)
    cr.set_source_rgba(1, 1, 1, 0.25)
    cr.set_line_width(2)
    cr.stroke()

    if game.food is not None:
        x = BOARD_LEFT + (game.food[0] + 0.5) * CELL
        y = MARGIN + (game.food[1] + 0.5) * CELL
        cr.arc(x, y, CELL * 0.36, 0, TAU)
        apple = cairo.RadialGradient(x - 3, y - 4, 1, x, y, CELL * 0.4)
        apple.add_color_stop_rgb(0, 1.0, 0.62, 0.55)
        apple.add_color_stop_rgb(1, 0.86, 0.16, 0.16)
        cr.set_source(apple)
        cr.fill()
    for segment in list(game.snake)[1:]:
        _cell(cr, segment, 2, 7)
    cr.set_source_rgb(0.20, 0.72, 0.42)
    cr.fill()
    _cell(cr, game.snake[0], 1, 8)
    cr.set_source_rgb(0.75, 0.95, 0.45)
    cr.fill()

    if banner is not None:      # a quarter down the board, clear of the snake's starting row
        x, y = BOARD_LEFT + width / 2, MARGIN + height / 4
        cr.rectangle(x - 150, y - 44, 300, 88)
        cr.set_source_rgba(0, 0, 0, 0.72)
        cr.fill()
        _text(cr, banner, x, y - 6, 30, 1.0, bold=True, centred=True)
        _text(cr, "press Options", x, y + 24, 16, 0.75, centred=True)


class Window(Gtk.Window):
    """The game window: reads the controller, moves the snake, redraws."""

    def __init__(self, pad: DualSense) -> None:
        super().__init__(title="Snake")
        self.pad = pad
        self.rng = random.Random()
        self.game = Game(self.rng, speed=1)
        self.steering = Steering()
        self.banner: str | None = "READY"       # shown while the snake stands still; None while it moves
        self.last_step = time.monotonic()
        self.battery = pad.battery()[0]
        self.failure: DualSenseError | None = None      # set if the controller is lost

        self.set_resizable(False)
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.area.connect("draw", lambda _, cr: draw(cr, self.game, self.banner, self.battery, self.pad))
        self.add(self.area)
        self.connect("key-press-event", self.on_key)
        self.connect("destroy", Gtk.main_quit)
        GLib.io_add_watch(pad.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_input)
        GLib.timeout_add(10, self.on_tick)
        GLib.timeout_add_seconds(BATTERY_SECONDS, self.on_battery)

    def fail(self, error: DualSenseError) -> bool:
        """End the program because the controller is gone."""
        self.failure = error
        Gtk.main_quit()
        return False

    def on_input(self, *_: object) -> bool:
        try:
            events = self.pad.poll()
        except DualSenseError as error:
            return self.fail(error)
        for event in events:
            direction = self.steering.direction(event)
            if direction is not None:
                if self.banner is None:
                    self.game.steer(direction)
            elif event.kind == "button" and event.value:
                if event.name in SPEED_BUTTONS:
                    self.game.speed = SPEED_BUTTONS[event.name]
                elif event.name == "options":
                    if self.game.over:
                        self.game = Game(self.rng, self.game.speed)
                    self.banner = "PAUSED" if self.banner is None else None
                    self.last_step = time.monotonic()
        self.area.queue_draw()
        return True

    def on_tick(self) -> bool:
        now = time.monotonic()
        if self.banner is None and now - self.last_step >= STEP_SECONDS[self.game.speed]:
            self.game.step()
            self.last_step = now
            if self.game.over:
                self.banner = "YOU WIN" if self.game.won else "GAME OVER"
            self.area.queue_draw()
        return True

    def on_battery(self) -> bool:
        try:
            self.battery = self.pad.battery()[0]
        except DualSenseError as error:
            return self.fail(error)
        self.area.queue_draw()
        return True

    def on_key(self, _: Gtk.Widget, event: Gdk.EventKey) -> None:
        if event.keyval in (Gdk.KEY_q, Gdk.KEY_Q, Gdk.KEY_Escape):
            self.destroy()


def main() -> None:
    if not Gtk.init_check()[0]:
        sys.exit("error: there is no graphical desktop to open a window on")
    try:
        pad = DualSense()
    except DualSenseError as error:
        sys.exit(f"error: {error}")
    window = Window(pad)
    window.show_all()
    Gtk.main()
    if window.failure is not None:
        sys.exit(f"error: {window.failure}")
    print(f"score: {window.game.score}")


if __name__ == "__main__":
    main()
