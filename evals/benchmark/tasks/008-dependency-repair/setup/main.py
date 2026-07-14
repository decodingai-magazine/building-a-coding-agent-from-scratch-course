"""Entrypoint that prints a circle area and a factorial.

BUG: ``factorial`` is imported from ``calc``, but that module was renamed to ``arithmetic`` — so this
import fails at startup. The fix is to import from the module that actually exists.
"""

from __future__ import annotations

from calc import factorial  # BUG: the module is now named ``arithmetic``, not ``calc``.
from geometry import circle_area


def main() -> None:
    print(f"area={circle_area(2):.2f}")
    print(f"fact={factorial(5)}")


if __name__ == "__main__":
    main()
