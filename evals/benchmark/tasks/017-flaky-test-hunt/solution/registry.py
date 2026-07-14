"""Collect named items into a bucket."""

from __future__ import annotations


def collect(item, bucket=None):
    """Append ``item`` to ``bucket`` and return it.

    A fresh call with no bucket starts from a new empty bucket and returns just ``[item]``; the
    default is created per call, so no state leaks between calls.
    """
    if bucket is None:
        bucket = []
    bucket.append(item)
    return bucket
