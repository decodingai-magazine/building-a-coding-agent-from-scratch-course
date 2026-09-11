"""A tiny argparse greeter. Default mode prints one greeting line per --times."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Greet NAME one or more times.")
    parser.add_argument("name", help="who to greet")
    parser.add_argument("--times", type=int, default=1, help="how many greeting lines to print")
    args = parser.parse_args()

    for _ in range(args.times):
        print(f"Hello, {args.name}!")


if __name__ == "__main__":
    main()
