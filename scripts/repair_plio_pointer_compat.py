#!/usr/bin/env python3
"""Convert obsolete PLIO pointer snippets to modern input/output PLIO objects.

Some hydrated examples use:

    PLIO* in = new PLIO(...);
    connect<>(in->out[0], graph.in);

ADF 2025.x exposes `out`/`in` on input_plio/output_plio objects, not on a
generic PLIO pointer.  This repair infers direction from connect usage and
rewrites only declarations whose direction is unambiguous.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PLIO_DECL_RE = re.compile(
    r"(?P<indent>^[ \t]*)(?P<prefix>(?:adf::)?PLIO)\s*\*\s*(?P<name>[A-Za-z_]\w*)\s*=\s*new\s+(?:adf::)?PLIO\s*\((?P<args>.*?)\)\s*;",
    re.MULTILINE | re.DOTALL,
)


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-root", default="golden repos")
    ap.add_argument("--limit", type=int, default=0)
    return ap.parse_args()


def rewrite_file(path: Path) -> tuple[bool, int]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out_names = set(re.findall(r"\b([A-Za-z_]\w*)\s*->\s*out\s*\[", text))
    in_names = set(re.findall(r"\b([A-Za-z_]\w*)\s*->\s*in\s*\[", text))
    if not out_names and not in_names:
        return False, 0

    converted: dict[str, str] = {}

    def repl_decl(match: re.Match[str]) -> str:
        name = match.group("name")
        is_input = name in out_names
        is_output = name in in_names
        if is_input == is_output:
            return match.group(0)

        namespace = "adf::" if match.group("prefix").startswith("adf::") else ""
        plio_type = "input_plio" if is_input else "output_plio"
        converted[name] = "out" if is_input else "in"
        return (
            f"{match.group('indent')}{namespace}{plio_type} {name} = "
            f"{namespace}{plio_type}::create({match.group('args')});"
        )

    updated = PLIO_DECL_RE.sub(repl_decl, text)
    for name, port in converted.items():
        updated = re.sub(rf"\b{re.escape(name)}\s*->\s*{port}\s*\[", f"{name}.{port}[", updated)

    if updated == text:
        return False, 0
    path.write_text(updated, encoding="utf-8")
    return True, len(converted)


def main() -> int:
    args = parse_args()
    root = Path(args.corpus_root)
    changed_files: list[dict[str, object]] = []
    seen = 0
    for suffix in ("*.cpp", "*.cc", "*.cxx"):
        for path in sorted(root.rglob(suffix)):
            seen += 1
            if args.limit > 0 and seen > args.limit:
                break
            try:
                changed, count = rewrite_file(path)
            except OSError:
                continue
            if changed:
                row = {"updated": str(path), "plios_converted": count}
                changed_files.append(row)
                print(json.dumps(row), flush=True)
        if args.limit > 0 and seen > args.limit:
            break
    print(
        json.dumps(
            {
                "files_updated": len(changed_files),
                "plios_converted": sum(int(row["plios_converted"]) for row in changed_files),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
