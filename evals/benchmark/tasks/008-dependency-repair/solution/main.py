"""Entrypoint that prints a circle area and a factorial (import repaired)."""

from __future__ import annotations

from arithmetic import factorial  # fixed: import from the module that actually exists.
from geometry import circle_area


def main() -> None:
    print(f"area={circle_area(2):.2f}")
    print(f"fact={factorial(5)}")


if __name__ == "__main__":
    main()
