"""A tiny argparse greeter. Default mode prints greeting lines; --json prints one JSON object."""

from __future__ import annotations

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser(description="Greet NAME one or more times.")
    parser.add_argument("name", help="who to greet")
    parser.add_argument("--times", type=int, default=1, help="how many greeting lines to print")
    parser.add_argument("--json", action="store_true", help="emit a JSON object instead of text")
    args = parser.parse_args()

    greeting = f"Hello, {args.name}!"
    if args.json:
        print(json.dumps({"name": args.name, "times": args.times, "greeting": greeting}))
        return

    for _ in range(args.times):
        print(greeting)


if __name__ == "__main__":
    main()
