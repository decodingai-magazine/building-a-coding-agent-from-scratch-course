---
name: demo-1-terminal-arcade
description: Demo skill that builds a colorful, playable terminal Snake game in a single pure-stdlib Python curses file — score HUD, speed ramp, and a game-over screen.
---

Build a playable, **colorful** game of Snake that runs in the terminal, as a single self-contained
Python file `snake.py`.

## Constraints

- Pure standard library only — use the built-in `curses` module for rendering and input. Add zero
  third-party dependencies (nothing that would need `uv add` or `pip install`).
- Keep it small and readable: roughly 150 lines. This is a demo people will read, not just play.

## Plan first

Before writing any code, lay out your steps with `todo_write` (draw field → snake movement → food
and growth → color + HUD → game over → smoke checks) and tick them off as you go.

## Gameplay to implement

- A bordered play field drawn with `curses`.
- A snake that moves continuously and is steered with the arrow keys.
- Food that spawns at a random empty cell; eating it grows the snake and increases the score.
- Game over when the snake hits a wall or itself; show a centered "GAME OVER — score N" screen and
  wait for a keypress to exit.
- Restore the terminal cleanly on exit (use `curses.wrapper` so a crash never leaves the terminal
  garbled).

## Make it flashy

- **Color** (guard with `curses.has_colors()`): green snake, red food, yellow score bar — set up
  `curses.init_pair` pairs once at start and fall back to plain text when colors are unsupported.
- **Score HUD**: a top status line like `SCORE 7 · SPEED 3` that updates live.
- **Speed ramp**: every 3 pieces of food eaten, shorten the tick timeout a little so the game gets
  faster — cap it so it stays playable.

## How to iterate

`curses` needs a real TTY, so you cannot fully play it headlessly. Iterate like this instead:

1. After each change, syntax-check it: `uv run python -m py_compile snake.py`.
2. Factor the pure logic — food placement, collision detection, the speed-for-score curve — into
   small functions and exercise them headlessly with `uv run python -c "..."`. Guard the
   `curses.wrapper(main)` call behind `if __name__ == "__main__":` so importing the module never
   grabs the terminal.
3. Hand the finished `snake.py` to the human to play with `uv run python snake.py`.

Report the final file, the controls, and the one-line command to play it.
