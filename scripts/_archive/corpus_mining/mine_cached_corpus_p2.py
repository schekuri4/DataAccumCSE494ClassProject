#!/usr/bin/env python3
"""
Mine AIE source files from the GitHub API blob cache (.cache/aie_source_corpus/),
using broader path/content filters than the original corpus builder.
Deduplicates against aie_github_sources.jsonl, aie_expanded_sources.jsonl,
and aie_local_corpus_p2.jsonl.
Appends new entries to data/raw/aie_local_corpus_p2.jsonl.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / ".cache" / "aie_source_corpus"
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_PATH = RAW_DIR / "aie_local_corpus_p2.jsonl"

EXISTING_CORPUS_FILES = [
    RAW_DIR / "aie_github_sources.jsonl",
    RAW_DIR / "aie_expanded_sources.jsonl",
]

SOURCE_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}
MAX_BLOB_BYTES = 60_000

KERNEL_KEYWORDS = [
    "aie::vector", "input_buffer", "output_buffer", "input_stream", "output_stream",
    "chess_prepare_for_pipelining", "aie::begin_vector", "readincr", "writeincr",
    "input_window", "output_window", "aie::accum", "aie_api/aie.hpp", "#include <adf.h>",
]
GRAPH_KEYWORDS = [
    "adf::graph", "adf::connect", "adf::PLIO", "adf::GMIO", "kernel::create",
]

SKIP_PATH_FRAGMENTS = [
    "/.autopilot/", "/_x/", "/test/", "/tests/", "/experimental/",
    "/extern/", "/third_party/", "/__pycache__/", "/build/",
]


def content_hash(code: str) -> str:
    return hashlib.sha256(code.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def load_blob_text(blob_path: Path) -> str:
    blob = json.loads(blob_path.read_text(encoding="utf-8"))
    content = str(blob.get("content") or "")
    if not content:
        return ""
    if blob.get("encoding") == "base64":
        return base64.b64decode(content.encode("ascii")).decode("utf-8", errors="replace")
    return content


def repo_full_name_from_tree(tree: dict) -> str:
    url = str(tree.get("url") or "")
    m = re.search(r"/repos/([^/]+/[^/]+)/git/trees/", url)
    return m.group(1) if m else ""


def ref_from_tree_url(tree: dict) -> str:
    """Try to extract branch/ref from the tree's URL or sha."""
    return "main"  # conservative default; trees don't embed branch name


def cached_blob_path(full_name: str, blob_sha: str) -> Path:
    url = f"https://api.github.com/repos/{full_name}/git/blobs/{blob_sha}"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return CACHE_DIR / "blobs" / f"{digest}.json"


def is_aie_candidate(code: str) -> bool:
    low = code.lower()
    return any(k.lower() in low for k in KERNEL_KEYWORDS + GRAPH_KEYWORDS)


def is_valid_path(path: str) -> bool:
    suf = Path(path).suffix.lower()
    if suf not in SOURCE_SUFFIXES:
        return False
    low = "/" + path.replace("\\", "/").lower() + "/"
    return not any(frag in low for frag in SKIP_PATH_FRAGMENTS)


def detect_category(code: str) -> str:
    low = code.lower()
    if "adf::graph" in low or "connect<" in low or "kernel::create" in low:
        return "graph"
    return "kernel"


def detect_interfaces(code: str) -> list[str]:
    low = code.lower()
    out = []
    if any(t in low for t in ["input_buffer", "output_buffer", "input_window", "output_window"]):
        out.append("buffer")
    if any(t in low for t in ["input_stream", "output_stream", "readincr", "writeincr"]):
        out.append("stream")
    if "cascade" in low:
        out.append("cascade")
    if any(t in low for t in ["plio", "gmio"]):
        out.append("external_io")
    return out or ["unknown"]


def detect_data_types(code: str) -> list[str]:
    low = code.lower()
    return [t for t in ["int8","int16","int32","float","cint16","cint32","cfloat"] if t in low] or ["unknown"]


def detect_compute_patterns(code: str) -> list[str]:
    low = code.lower()
    checks = {
        "fir_filter": ["fir", "tap", "mac"],
        "fft_butterfly": ["fft", "butterfly", "twiddle"],
        "matrix_multiply": ["gemm", "matmul", "matrix"],
        "beamforming": ["beam", "steer"],
        "interpolation": ["interpol", "upsample"],
        "decimation": ["decim", "downsample"],
        "sorting_network": ["sort", "bitonic"],
        "peak_detection": ["peak", "threshold"],
        "qam_demodulation": ["qam", "llr"],
        "channel_estimation": ["channel estimation", "pilot"],
        "cfar_detection": ["cfar"],
        "digital_downconversion": ["downconversion", "nco", "mixer"],
        "ldpc_update": ["ldpc", "belief"],
        "viterbi_decoder": ["viterbi", "trellis"],
    }
    return [p for p, hints in checks.items() if any(h in low for h in hints)] or ["generic_aie"]


def load_existing_seen() -> tuple[set[str], set[str]]:
    hashes: set[str] = set()
    urls: set[str] = set()
    for path in EXISTING_CORPUS_FILES + [OUTPUT_PATH]:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    code = row.get("code", "")
                    if code:
                        hashes.add(content_hash(code))
                    url = row.get("source_url") or row.get("source") or ""
                    if url:
                        urls.add(url)
                except Exception:
                    pass
    return hashes, urls


def build_source_url(full_name: str, branch: str, path: str) -> str:
    parts = "/".join(p for p in path.split("/"))
    return f"https://github.com/{full_name}/blob/{branch}/{parts}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Mine GitHub API blob cache into corpus_p2.")
    parser.add_argument("--cache-dir", default=str(CACHE_DIR))
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--max-bytes", type=int, default=MAX_BLOB_BYTES)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    output_path = Path(args.output)
    trees_dir = cache_dir / "trees"

    print("Loading existing seen hashes + URLs for dedup...")
    seen_hashes, seen_urls = load_existing_seen()
    print(f"  {len(seen_hashes)} content hashes, {len(seen_urls)} URLs loaded")

    new_entries: list[dict] = []
    skipped_no_blob = skipped_dedup = skipped_size = skipped_not_aie = skipped_path = 0
    repo_counts: Counter[str] = Counter()

    tree_paths = sorted(trees_dir.glob("*.json"))
    print(f"Scanning {len(tree_paths)} cached trees...")

    for tree_path in tree_paths:
        tree = json.loads(tree_path.read_text(encoding="utf-8"))
        full_name = repo_full_name_from_tree(tree)
        if not full_name:
            continue

        # Try to infer branch from tree SHA by checking repos cache
        branch = "main"

        for entry in tree.get("tree", []):
            if entry.get("type") != "blob":
                continue
            rel_path = str(entry.get("path") or "").replace("\\", "/")
            if not is_valid_path(rel_path):
                skipped_path += 1
                continue
            blob_size = int(entry.get("size") or 0)
            if blob_size <= 0 or blob_size > args.max_bytes:
                skipped_size += 1
                continue
            blob_sha = str(entry.get("sha") or "")
            if not blob_sha:
                continue
            source_url = build_source_url(full_name, branch, rel_path)
            if source_url in seen_urls:
                skipped_dedup += 1
                continue
            blob_path = cached_blob_path(full_name, blob_sha)
            if not blob_path.exists():
                skipped_no_blob += 1
                continue
            code = load_blob_text(blob_path).strip()
            if not code:
                continue
            if not is_aie_candidate(code):
                skipped_not_aie += 1
                continue
            h = content_hash(code)
            if h in seen_hashes:
                skipped_dedup += 1
                continue

            seen_hashes.add(h)
            seen_urls.add(source_url)
            repo_counts[full_name] += 1
            new_entries.append({
                "filename": Path(rel_path).name,
                "code": code.replace("\r\n", "\n").strip(),
                "source": source_url,
                "category": detect_category(code),
                "bug_type": None,
                "bug_explanation": None,
                "metadata": {
                    "path": rel_path,
                    "compute_pattern": detect_compute_patterns(code),
                    "data_types": detect_data_types(code),
                    "interfaces": detect_interfaces(code),
                    "blob_sha": blob_sha,
                    "blob_size": blob_size,
                    "source": "cache_mine",
                },
                "source_url": source_url,
                "repo": full_name,
                "branch": branch,
            })

    print(f"\n=== Cache mining results ===")
    print(f"  New entries found:    {len(new_entries)}")
    print(f"  Skipped (dedup):      {skipped_dedup}")
    print(f"  Skipped (no blob):    {skipped_no_blob}")
    print(f"  Skipped (not AIE):    {skipped_not_aie}")
    print(f"  Skipped (path/size):  {skipped_path + skipped_size}")
    print(f"\n  Breakdown by repo:")
    for repo, count in repo_counts.most_common():
        print(f"    {count:4d}  {repo}")

    if args.dry_run:
        print("\n[dry-run] No files written.")
        return

    with output_path.open("a", encoding="utf-8") as fh:
        for entry in new_entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"\nAppended {len(new_entries)} entries → {output_path}")


if __name__ == "__main__":
    main()
