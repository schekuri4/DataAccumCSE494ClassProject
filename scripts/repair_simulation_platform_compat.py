#!/usr/bin/env python3
"""Rewrite obsolete adf::simulation::platform wrappers to direct PLIO connects."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PLATFORM_RE = re.compile(
    r"(?:adf::)?simulation::platform<\s*([^,>]+)\s*,\s*([^>]+)\s*>\s+([A-Za-z_]\w*)\s*\((.*?)\)\s*;",
    re.DOTALL,
)
SRC_CONNECT_RE = re.compile(
    r"connect<>\s+([A-Za-z_]\w+)\s*\(\s*([A-Za-z_]\w*)\.src\[(\d+)\]\s*,\s*(.*?)\s*\);"
)
SINK_CONNECT_RE = re.compile(
    r"connect<>\s+([A-Za-z_]\w+)\s*\(\s*(.*?)\s*,\s*([A-Za-z_]\w*)\.sink\[(\d+)\]\s*\);"
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-root", default="golden repos")
    ap.add_argument("--limit", type=int, default=0)
    return ap.parse_args()


def split_args(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in text:
        if ch == "," and depth == 0:
            piece = "".join(current).strip()
            if piece:
                parts.append(piece)
            current = []
            continue
        if ch in "(<[{":
            depth += 1
        elif ch in ")>]}":
            depth = max(0, depth - 1)
        current.append(ch)
    piece = "".join(current).strip()
    if piece:
        parts.append(piece)
    return parts


def resolve_counts(src_count_text: str, sink_count_text: str, args: list[str]) -> tuple[int, int] | None:
    src_count_text = src_count_text.strip()
    sink_count_text = sink_count_text.strip()
    try:
        return int(src_count_text), int(sink_count_text)
    except ValueError:
        pass

    if src_count_text == sink_count_text and len(args) % 2 == 0:
        half = len(args) // 2
        return half, half
    return None


def platform_endpoint(arg: str, port: str) -> str:
    """Return a direct PLIO/GMIO port expression for a platform argument."""
    expr = arg.strip()
    if expr.startswith("&"):
        expr = expr[1:].strip()
        return f"{expr}.{port}[0]"
    if "->" in expr or "." in expr:
        return f"{expr}.{port}[0]"
    return f"{expr}->{port}[0]"


def rewrite_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = PLATFORM_RE.search(text)
    if not match:
        updated = text.replace("adf::// simulation::platform removed", "// simulation::platform removed")
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            return True
        return False

    platform_name = match.group(3)
    args = split_args(match.group(4))
    counts = resolve_counts(match.group(1), match.group(2), args)
    if counts is None:
        return False
    src_count, sink_count = counts
    if len(args) != src_count + sink_count:
        return False

    src_args = args[:src_count]
    sink_args = args[src_count:]
    changed = False

    def repl_src(m: re.Match[str]) -> str:
        nonlocal changed
        if m.group(2) != platform_name:
            return m.group(0)
        index = int(m.group(3))
        if index >= len(src_args):
            return m.group(0)
        changed = True
        return f"connect<> {m.group(1)}({platform_endpoint(src_args[index], 'out')}, {m.group(4)});"

    def repl_sink(m: re.Match[str]) -> str:
        nonlocal changed
        if m.group(3) != platform_name:
            return m.group(0)
        index = int(m.group(4))
        if index >= len(sink_args):
            return m.group(0)
        changed = True
        return f"connect<> {m.group(1)}({m.group(2)}, {platform_endpoint(sink_args[index], 'in')});"

    updated = SRC_CONNECT_RE.sub(repl_src, text)
    updated = SINK_CONNECT_RE.sub(repl_sink, updated)
    if not changed:
        return False

    updated = updated.replace(match.group(0), f"// simulation::platform removed for newer ADF compatibility.")
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    args = parse_args()
    root = Path(args.corpus_root)
    changed_files: list[str] = []
    seen = 0
    for path in sorted(root.rglob("*.cpp")):
        seen += 1
        if args.limit > 0 and seen > args.limit:
            break
        try:
            if rewrite_file(path):
                changed_files.append(str(path))
                print(json.dumps({"updated": str(path)}), flush=True)
        except OSError:
            continue
    print(json.dumps({"files_updated": len(changed_files)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
