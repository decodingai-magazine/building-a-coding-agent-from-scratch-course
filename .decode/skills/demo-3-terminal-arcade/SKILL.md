---
name: demo-3-terminal-arcade
description: Demo skill that builds a playable terminal Snake game in a single pure-stdlib Python curses file.
---

Build a playable game of Snake that runs in the terminal, as a single self-contained Python file
`snake.py`.

## Constraints

- Pure standard library only — use the built-in `curses` module for rendering and input. Add zero
  third-party dependencies (nothing that would need `uv add` or `pip install`).
- Keep it small and readable: roughly 100 lines. This is a demo people will read, not just play.

## Gameplay to implement

- A bordered play field drawn with `curses`.
- A snake that moves continuously and is steered with the arrow keys.
- Food that spawns at a random empty cell; eating it grows the snake and increases the score.
- Game over when the snake hits a wall or itself; show the final score and wait for a keypress to
  exit.
- Restore the terminal cleanly on exit (use `curses.wrapper` so a crash never leaves the terminal
  garbled).

## How to iterate

`curses` needs a real TTY, so you cannot fully play it headlessly. Iterate like this instead:

1. After each change, syntax-check it: `uv run python -m py_compile snake.py`.
2. Smoke-check that it imports and its pure helpers work without opening a screen — e.g. factor the
   food-placement and collision logic into small functions and exercise them from
   `uv run python -c "..."`, or guard the `curses.wrapper(main)` call behind
   `if __name__ == "__main__":` so importing the module never grabs the terminal.
3. Hand the finished `snake.py` to the human to play with `uv run python snake.py`.

Report the final file and the one-line command to play it.
