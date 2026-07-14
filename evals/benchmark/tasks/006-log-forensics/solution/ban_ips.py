"""Print a JSON array of client IPs with 5 or more 404 responses in access.log."""

from __future__ import annotations

import collections
import json


def find_offenders(log_path: str = "access.log", *, threshold: int = 5) -> list[str]:
    counts: collections.Counter[str] = collections.Counter()
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            fields = line.split()
            if len(fields) < 2:
                continue
            ip = fields[0]
            status = fields[-2]
            if status == "404":
                counts[ip] += 1
    return [ip for ip, count in counts.items() if count >= threshold]


if __name__ == "__main__":
    print(json.dumps(find_offenders()))
