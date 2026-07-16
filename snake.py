#!/usr/bin/env python3
"""
Playable, colorful game of Snake running in the terminal.
Written using pure Python standard library `curses`.
"""

import contextlib
import curses
import random
import sys


class SnakeGameLogic:
    """
    Pure logic class for managing the state of the Snake game.
    Can be run headlessly or with curses.
    """

    def __init__(self, height=20, width=40):
        self.height = height
        self.width = width
        self.reset()

    def reset(self):
        # Start in the middle of the board
        self.snake = [
            (self.height // 2, self.width // 2),
            (self.height // 2, self.width // 2 - 1),
            (self.height // 2, self.width // 2 - 2),
        ]
        self.direction = (0, 1)  # Initial direction: right
        self.score = 0
        self.speed = 1
        self.food_eaten = 0
        self.food = None
        self.game_over = False
        self.flash_head_ticks = 0
        self.spawn_food()

    def spawn_food(self):
        """Spawns food at a random empty location on the board."""
        snake_set = set(self.snake)
        empty_spaces = [
            (y, x) for y in range(self.height) for x in range(self.width) if (y, x) not in snake_set
        ]
        if empty_spaces:
            self.food = random.choice(empty_spaces)
        else:
            self.food = None  # Win condition or board filled

    def change_direction(self, new_dir):
        """Changes snake direction if it's not a 180-degree turn."""
        dy, dx = self.direction
        if (new_dir[0] + dy == 0) and (new_dir[1] + dx == 0):
            return
        self.direction = new_dir

    def tick(self):
        """Advances the game state by one frame. Returns True if alive, False if dead."""
        if self.game_over:
            return False

        # Handle non-blocking animations ticks
        if self.flash_head_ticks > 0:
            self.flash_head_ticks -= 1

        # Calculate next position of snake head
        head_y, head_x = self.snake[0]
        dy, dx = self.direction
        next_y = head_y + dy
        next_x = head_x + dx

        # Collision Check: borders
        if next_y < 0 or next_y >= self.height or next_x < 0 or next_x >= self.width:
            self.game_over = True
            return False

        # Collision Check: itself (excluding the very tail tip if the snake moves)
        next_pos = (next_y, next_x)
        if next_pos in self.snake[:-1]:
            self.game_over = True
            return False

        # Move snake
        self.snake.insert(0, next_pos)

        # Check if food is eaten
        if next_pos == self.food:
            self.score += 10
            self.food_eaten += 1
            # Speed ramp: increase speed every 3 pieces of food, cap at 10
            if self.food_eaten % 3 == 0:
                self.speed = min(10, self.speed + 1)
            self.flash_head_ticks = 4  # Start head flash flourish
            self.spawn_food()
        else:
            self.snake.pop()

        return True


def render_game(stdscr, game, start_y, start_x):
    """Draws the current game state onto the curses stdscr."""
    stdscr.erase()

    def cp(num):
        return curses.color_pair(num) if curses.has_colors() else 0

    # 1. Render HUD (centered)
    hud_text = f" SCORE {game.score:04d} · SPEED {game.speed:02d} "
    hud_attr = cp(3) | curses.A_BOLD if game.flash_head_ticks > 0 else cp(3)
    stdscr.addstr(start_y, start_x + (game.width + 2 - len(hud_text)) // 2, hud_text, hud_attr)

    # 2. Render Border Top
    stdscr.addstr(start_y + 1, start_x, "┌" + "─" * game.width + "┐", cp(4))

    # 3. Render Field Left/Right Borders
    for y in range(game.height):
        stdscr.addstr(start_y + 2 + y, start_x, "│", cp(4))
        # Clear field lines
        stdscr.addstr(start_y + 2 + y, start_x + 1, " " * game.width)
        stdscr.addstr(start_y + 2 + y, start_x + 1 + game.width, "│", cp(4))

    # 4. Render Border Bottom
    stdscr.addstr(start_y + 2 + game.height, start_x, "└" + "─" * game.width + "┘", cp(4))

    # 5. Render Food (star)
    if game.food:
        fy, fx = game.food
        with contextlib.suppress(curses.error):
            stdscr.addch(start_y + 2 + fy, start_x + 1 + fx, "★", cp(2) | curses.A_BOLD)

    # 6. Render Snake
    for i, (sy, sx) in enumerate(game.snake):
        if i == 0:
            # Snake Head: Flashes yellow during eat flourish, else cyan
            if game.flash_head_ticks > 0 and game.flash_head_ticks % 2 == 0:
                char = "⬤"
                attr = cp(3) | curses.A_BOLD
            else:
                char = "⬤"
                attr = cp(5) | curses.A_BOLD
        else:
            char = "●"
            attr = cp(1)

        with contextlib.suppress(curses.error):
            stdscr.addch(start_y + 2 + sy, start_x + 1 + sx, char, attr)


def play_death_animation(stdscr, game, start_y, start_x):
    """Flashes and disintegrates the snake tail-to-head on game over."""

    # 1. Quick initial flash of the whole snake
    def cp(num):
        return curses.color_pair(num) if curses.has_colors() else 0

    # Flash the snake twice
    for _ in range(2):
        # Change whole snake color to Magenta / Flash
        for sy, sx in game.snake:
            with contextlib.suppress(curses.error):
                stdscr.addch(start_y + 2 + sy, start_x + 1 + sx, "●", cp(6) | curses.A_BOLD)
        stdscr.refresh()
        curses.napms(100)

        # Draw it back normally
        render_game(stdscr, game, start_y, start_x)
        stdscr.refresh()
        curses.napms(100)

    # 2. Disintegrate tail to head
    while len(game.snake) > 0:
        game.snake.pop()  # Remove tail element
        render_game(stdscr, game, start_y, start_x)
        stdscr.refresh()
        # Adjust delay based on snake size to keep total time <= ~400ms
        delay = max(10, min(50, 400 // (len(game.snake) + 1)))
        curses.napms(delay)


def draw_game_over(stdscr, game, start_y, start_x):
    """Draws a centered Game Over message dialog."""

    def cp(num):
        return curses.color_pair(num) if curses.has_colors() else 0

    # Ensure box remains centered on the playfield
    box_h = 6
    box_w = 30
    box_y = start_y + (game.height + 3 - box_h) // 2
    box_x = start_x + (game.width + 2 - box_w) // 2

    # Clear box background
    for y in range(box_h):
        stdscr.addstr(box_y + y, box_x, " " * box_w, cp(6))

    # Draw Box Borders
    stdscr.addstr(box_y, box_x, "┏" + "━" * (box_w - 2) + "┓", cp(6))
    for y in range(1, box_h - 1):
        stdscr.addstr(box_y + y, box_x, "┃" + " " * (box_w - 2) + "┃", cp(6))
    stdscr.addstr(box_y + box_h - 1, box_x, "┗" + "━" * (box_w - 2) + "┛", cp(6))

    # Text rendering inside the box
    text_title = "G A M E   O V E R"
    text_score = f"Final Score: {game.score}"
    text_prompt = "Press any key to exit..."

    stdscr.addstr(
        box_y + 1, box_x + (box_w - len(text_title)) // 2, text_title, cp(6) | curses.A_BOLD
    )
    stdscr.addstr(
        box_y + 2, box_x + (box_w - len(text_score)) // 2, text_score, cp(3) | curses.A_BOLD
    )
    stdscr.addstr(
        box_y + 4, box_x + (box_w - len(text_prompt)) // 2, text_prompt, cp(4) | curses.A_DIM
    )

    stdscr.refresh()


def main(stdscr):
    # Standard terminal initialization
    curses.curs_set(0)
    stdscr.keypad(True)

    # Initialize custom colors if available
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except Exception:
            bg = curses.COLOR_BLACK

        # Define color pairs
        curses.init_pair(1, curses.COLOR_GREEN, bg)  # Snake body
        curses.init_pair(2, curses.COLOR_RED, bg)  # Food
        curses.init_pair(3, curses.COLOR_YELLOW, bg)  # HUD / Eat flash / highlighted text
        curses.init_pair(4, curses.COLOR_WHITE, bg)  # Borders
        curses.init_pair(5, curses.COLOR_CYAN, bg)  # Snake Head
        curses.init_pair(6, curses.COLOR_MAGENTA, bg)  # Game Over / flash effects

    game = SnakeGameLogic(height=18, width=40)

    # Render first frame
    max_y, max_x = stdscr.getmaxyx()
    required_height = game.height + 4
    required_width = game.width + 4
    start_y = (max_y - required_height) // 2
    start_x = (max_x - required_width) // 2

    if max_y >= required_height and max_x >= required_width:
        render_game(stdscr, game, start_y, start_x)
        stdscr.refresh()

    while True:
        max_y, max_x = stdscr.getmaxyx()
        required_height = game.height + 4
        required_width = game.width + 4

        # Gracefully handle small window sizes
        if max_y < required_height or max_x < required_width:
            stdscr.erase()
            stdscr.addstr(0, 0, "Terminal too small!", curses.A_BOLD)
            stdscr.addstr(1, 0, f"Required: {required_width}x{required_height}")
            stdscr.addstr(2, 0, f"Current: {max_x}x{max_y}")
            stdscr.addstr(4, 0, "Resize your window, or press 'q' to quit.")
            stdscr.refresh()
            stdscr.timeout(200)
            ch = stdscr.getch()
            if ch in [ord("q"), ord("Q"), 27]:
                break
            continue

        start_y = (max_y - required_height) // 2
        start_x = (max_x - required_width) // 2

        # Speed curve tick delays:
        # Speed 1: 150ms
        # Speed 10: 42ms
        delay = max(40, 160 - (game.speed - 1) * 12)
        stdscr.timeout(delay)

        ch = stdscr.getch()

        # Handle instant quit keys (q, Q, ESC)
        if ch in [ord("q"), ord("Q"), 27]:
            break

        # Handle movement keys (WASD + Arrow Keys)
        if ch in [ord("w"), ord("W"), curses.KEY_UP]:
            game.change_direction((-1, 0))
        elif ch in [ord("s"), ord("S"), curses.KEY_DOWN]:
            game.change_direction((1, 0))
        elif ch in [ord("a"), ord("A"), curses.KEY_LEFT]:
            game.change_direction((0, -1))
        elif ch in [ord("d"), ord("D"), curses.KEY_RIGHT]:
            game.change_direction((0, 1))

        # Game simulation tick
        alive = game.tick()

        if not alive:
            # Play death animation before displaying Game Over screen
            play_death_animation(stdscr, game, start_y, start_x)
            draw_game_over(stdscr, game, start_y, start_x)

            # Block input timeout to let user see score and press any key to exit
            stdscr.timeout(-1)
            stdscr.getch()
            break

        render_game(stdscr, game, start_y, start_x)
        stdscr.refresh()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        # Exit cleanly on Ctrl+C without traceback
        sys.exit(0)
