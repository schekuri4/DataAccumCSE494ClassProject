#!/usr/bin/env python3
"""Rewrite old top-level GMIO graph ports to modern input_gmio/output_gmio.

Older ADF examples often declare raw GMIO objects and pass pointers through
simulation::platform. Newer ADF headers expose graph-connectable ports through
input_gmio::out and output_gmio::in instead. This repair is intentionally
conservative: it only converts declarations whose names are clearly used as
graph input/output GMIO ports.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SCALAR_GMIO_RE = re.compile(
    r"(?m)^(?P<indent>\s*)(?P<prefix>adf::)?GMIO\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\((?P<args>[^;\n]*)\)\s*;"
)
ARRAY_GMIO_RE = re.compile(
    r"(?ms)^(?P<indent>\s*)(?P<prefix>adf::)?GMIO\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*\[(?P<size>[^\]]+)\]\s*=\s*"
    r"\{(?P<body>.*?)\}\s*;"
)
CONNECT_MEMBER_RE = re.compile(
    r"&?\b(?P<name>[A-Za-z_]\w*)(?P<index>\[[^\]]+\])?\s*->\s*"
    r"(?P<port>out|in)\[(?P<num>\d+)\]"
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-root", default="golden repos")
    ap.add_argument("--limit", type=int, default=0)
    return ap.parse_args()


def collect_port_directions(text: str) -> tuple[set[str], set[str]]:
    inputs: set[str] = set()
    outputs: set[str] = set()

    for name, _, port, _ in re.findall(
        r"\b([A-Za-z_]\w*)(\[[^\]]+\])?\s*(?:\.|->)\s*(out|in)\[(\d+)\]",
        text,
    ):
        if port == "out":
            inputs.add(name)
        else:
            outputs.add(name)

    for name in re.findall(r"\b([A-Za-z_]\w*)(?:\[[^\]]+\])?\.gm2aie(?:_nb)?\s*\(", text):
        inputs.add(name)
    for name in re.findall(r"\b([A-Za-z_]\w*)(?:\[[^\]]+\])?\.aie2gm(?:_nb)?\s*\(", text):
        outputs.add(name)

    for decl_name in re.findall(r"\b(?:adf::)?GMIO\s+([A-Za-z_]\w*)", text):
        lowered = decl_name.lower()
        if "in" in lowered and "out" not in lowered:
            inputs.add(decl_name)
        if "out" in lowered:
            outputs.add(decl_name)

    return inputs, outputs


def direction_for(name: str, inputs: set[str], outputs: set[str]) -> str | None:
    in_seen = name in inputs
    out_seen = name in outputs
    lowered = name.lower()

    if in_seen and not out_seen:
        return "input"
    if out_seen and not in_seen:
        return "output"
    if "out" in lowered:
        return "output"
    if "in" in lowered:
        return "input"
    return None


def rewrite_creator_calls(body: str, kind: str) -> str:
    creator = f"{kind}_gmio::create"
    return re.sub(r"\b(?:adf::)?GMIO\s*\(", f"{creator}(", body)


def rewrite_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    inputs, outputs = collect_port_directions(text)
    changed = False

    def repl_array(match: re.Match[str]) -> str:
        nonlocal changed
        name = match.group("name")
        direction = direction_for(name, inputs, outputs)
        if direction is None:
            return match.group(0)
        changed = True
        kind = "input" if direction == "input" else "output"
        prefix = match.group("prefix") or ""
        body = rewrite_creator_calls(match.group("body"), kind)
        return (
            f"{match.group('indent')}{prefix}{kind}_gmio {name}"
            f"[{match.group('size')}] = {{{body}}};"
        )

    def repl_scalar(match: re.Match[str]) -> str:
        nonlocal changed
        name = match.group("name")
        direction = direction_for(name, inputs, outputs)
        if direction is None:
            return match.group(0)
        changed = True
        kind = "input" if direction == "input" else "output"
        prefix = match.group("prefix") or ""
        return (
            f"{match.group('indent')}{prefix}{kind}_gmio {name} = "
            f"{prefix}{kind}_gmio::create({match.group('args')});"
        )

    updated = ARRAY_GMIO_RE.sub(repl_array, text)
    updated = SCALAR_GMIO_RE.sub(repl_scalar, updated)

    def repl_member(match: re.Match[str]) -> str:
        nonlocal changed
        name = match.group("name")
        if name not in inputs and name not in outputs:
            return match.group(0)
        changed = True
        index = match.group("index") or ""
        return f"{name}{index}.{match.group('port')}[{match.group('num')}]"

    updated = CONNECT_MEMBER_RE.sub(repl_member, updated)

    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return changed


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
