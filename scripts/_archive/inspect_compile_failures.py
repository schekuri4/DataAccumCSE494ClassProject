#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def first_error_line(result: dict) -> str:
    text = f"{result.get('stderr_tail') or ''}\n{result.get('stdout_tail') or ''}"
    for line in text.splitlines():
        if "error:" in line or "ERROR:" in line or "fatal error:" in line:
            return line.strip()
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return "(no compiler output)"


def load_rows(paths: list[Path]) -> dict[tuple[str, int], dict]:
    rows: dict[tuple[str, int], dict] = {}
    for path in paths:
        key_paths = {str(path), str(path.resolve())}
        try:
            key_paths.add(str(path.resolve().relative_to(Path.cwd().resolve())))
        except ValueError:
            pass
        with path.open("r", encoding="utf-8") as fp:
            for idx, line in enumerate(fp):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for key_path in key_paths:
                    rows[(key_path, idx)] = row
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize AIE compile validation failures.")
    ap.add_argument("--results", required=True, help="Compile results JSONL")
    ap.add_argument("--inputs", nargs="+", required=True, help="Input dataset JSONL path(s)")
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()

    result_path = Path(args.results)
    input_paths = [Path(p) for p in args.inputs]
    source_rows = load_rows(input_paths)

    results = []
    with result_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    print(f"results={len(results)} file={result_path}")
    print("breakdown:")
    counts = collections.Counter((r.get("compiler"), r.get("error_class")) for r in results)
    for (compiler, error_class), count in counts.most_common():
        print(f"  {compiler} | {error_class}: {count}")

    print("\nfailures:")
    shown = 0
    for result in results:
        if result.get("compile_ok"):
            continue
        input_path = str(Path(result.get("input_path", "")))
        row_index = int(result.get("row_index", -1))
        row = source_rows.get((input_path, row_index), {})
        md = row.get("metadata") or {}
        print(
            f"row={row_index} compiler={result.get('compiler')} type={result.get('file_type')} "
            f"class={result.get('error_class')} rc={result.get('return_code')}"
        )
        print(f"  variant={md.get('variant')} bug_type={md.get('bug_type')}")
        print(f"  source={md.get('source') or md.get('source_path') or md.get('relative_path')}")
        print(f"  error={first_error_line(result)[:280]}")
        shown += 1
        if shown >= args.limit:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())