"""Collect named items into a bucket."""

from __future__ import annotations


def collect(item, bucket=[]):  # noqa: B006 - the seeded bug this task asks you to fix
    """Append ``item`` to ``bucket`` and return it.

    A fresh call with no bucket should start from an empty bucket and return just ``[item]``.
    """
    bucket.append(item)
    return bucket
