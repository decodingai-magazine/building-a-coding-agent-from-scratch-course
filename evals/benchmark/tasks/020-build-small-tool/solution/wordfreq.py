"""Word-frequency CLI: top-N, case-insensitive, punctuation-stripped."""

from __future__ import annotations

import argparse
import string
from collections import Counter
from pathlib import Path


def count_words(text: str) -> Counter[str]:
    """Count words in ``text`` case-insensitively, stripping surrounding punctuation."""
    counts: Counter[str] = Counter()
    for token in text.split():
        word = token.strip(string.punctuation).lower()
        if word:
            counts[word] += 1
    return counts


def top_words(text: str, n: int) -> list[tuple[str, int]]:
    """The ``n`` most frequent words, ordered by count descending then word ascending."""
    counts = count_words(text)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:n]


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the most frequent words in a text file.")
    parser.add_argument("path")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    text = Path(args.path).read_text(encoding="utf-8")
    for word, count in top_words(text, args.top):
        print(f"{word} {count}")


if __name__ == "__main__":
    main()
