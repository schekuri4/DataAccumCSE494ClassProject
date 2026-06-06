#!/usr/bin/env python3
"""Inline xf::cv::aie::METADATA_SIZE in config headers for ADF constexpr use.

Several older Vitis Vision graph configs use `xf::cv::aie::METADATA_SIZE` as a
non-type template argument.  In the recovered corpus/aiecompiler combination,
that qualified constant is sometimes not accepted as a constant expression even
though the hydrated `xf_aie_const.hpp` defines it as 32 int16 metadata elements,
i.e. 64 bytes.  Rewriting config headers keeps the generated window sizes
equivalent while avoiding the frontend constexpr failure.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


OLD = "xf::cv::aie::METADATA_SIZE"
NEW = "64"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-root", default="golden repos")
    ap.add_argument("--limit", type=int, default=0)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.corpus_root)
    changed_files: list[dict[str, object]] = []
    seen = 0
    for pattern in ("config.h", "config.hpp", "graph.h", "graph.hpp"):
        for path in sorted(root.rglob(pattern)):
            seen += 1
            if args.limit > 0 and seen > args.limit:
                break
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if OLD not in text:
                continue
            updated = text.replace(OLD, NEW)
            path.write_text(updated, encoding="utf-8")
            row = {"updated": str(path), "replacements": text.count(OLD)}
            changed_files.append(row)
            print(json.dumps(row), flush=True)
        if args.limit > 0 and seen > args.limit:
            break
    print(
        json.dumps(
            {
                "files_updated": len(changed_files),
                "replacements": sum(int(row["replacements"]) for row in changed_files),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
