#!/usr/bin/env python3
"""Remove invalid explicit ADF address placements.

Some older generated graph headers pin buffers/stacks to offsets such as
0x8000 or 0xF800. Vitis 2025.2 rejects the third adf::address parameter when
it is outside [0, 32768). For compile-clean corpus generation, removing only
the invalid placement constraints is safer than inventing new tile offsets.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


LOCATION_ASSIGN_RE = re.compile(
    r"(?ms)^(?P<indent>\s*)location<(?P<kind>buffer|stack)>\s*"
    r"\((?P<target>.*?)\)\s*=\s*\{(?P<body>.*?)\}\s*;"
)
ADDRESS_RE = re.compile(
    r"address\s*\(\s*[^,]+,\s*[^,]+,\s*(?P<offset>0x[0-9A-Fa-f]+|\d+)\s*\)"
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-root", default="golden repos")
    ap.add_argument("--limit", type=int, default=0)
    return ap.parse_args()


def offset_value(text: str) -> int | None:
    try:
        return int(text, 0)
    except ValueError:
        return None


def has_invalid_offset(body: str) -> bool:
    for match in ADDRESS_RE.finditer(body):
        value = offset_value(match.group("offset"))
        if value is not None and value >= 0x8000:
            return True
    return False


def rewrite_file(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    removed = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal removed
        if not has_invalid_offset(match.group("body")):
            return match.group(0)
        removed += 1
        return (
            f"{match.group('indent')}// Removed invalid location<{match.group('kind')}> "
            "address placement for Vitis 2025.2 compatibility."
        )

    updated = LOCATION_ASSIGN_RE.sub(repl, text)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
    return removed


def main() -> int:
    args = parse_args()
    root = Path(args.corpus_root)
    changed_files = 0
    removed_total = 0
    seen = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".h", ".hh", ".hpp", ".hxx", ".cpp", ".cc"}:
            continue
        seen += 1
        if args.limit > 0 and seen > args.limit:
            break
        try:
            removed = rewrite_file(path)
        except OSError:
            continue
        if removed:
            changed_files += 1
            removed_total += removed
            print(json.dumps({"updated": str(path), "placements_removed": removed}), flush=True)
    print(json.dumps({"files_updated": changed_files, "placements_removed": removed_total}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
