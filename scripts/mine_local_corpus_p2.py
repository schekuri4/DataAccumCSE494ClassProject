#!/usr/bin/env python3
"""
Mine AIE source files from locally cloned repos under aie_dataset/external/,
deduplicate against existing corpus (aie_github_sources.jsonl + aie_expanded_sources.jsonl),
and write new entries to data/raw/aie_local_corpus_p2.jsonl.

Output format matches aie_github_sources.jsonl:
  filename, code, source, category, bug_type, bug_explanation, metadata, source_url, repo, branch
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_DIR = ROOT / "aie_dataset" / "external"
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_PATH = RAW_DIR / "aie_local_corpus_p2.jsonl"

EXISTING_CORPUS_FILES = [
    RAW_DIR / "aie_github_sources.jsonl",
    RAW_DIR / "aie_expanded_sources.jsonl",
]

SOURCE_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}

KERNEL_KEYWORDS = [
    "aie::vector", "input_buffer", "output_buffer", "input_stream", "output_stream",
    "chess_prepare_for_pipelining", "aie::begin_vector", "readincr", "writeincr",
    "input_window", "output_window", "aie::accum",
]
GRAPH_KEYWORDS = [
    "adf::graph", "adf::connect", "adf::PLIO", "adf::GMIO", "kernel::create",
]

# Max file size in bytes — skip huge generated files
MAX_FILE_BYTES = 60_000

# Repo name → (owner/repo, branch hint)
REPO_META: dict[str, tuple[str, str]] = {
    "AIM":                   ("arc-research-lab/AIM", "main"),
    "Aries":                 ("arc-research-lab/Aries", "main"),
    "GAMA":                  ("advent-lab/GAMA", "main"),
    "iree-amd-aie":          ("nod-ai/iree-amd-aie", "main"),
    "MaxEVA":                ("enyac-group/MaxEVA", "main"),
    "mlir-aie":              ("Xilinx/mlir-aie", "main"),
    "my_mlir-aie":           ("pjh177787/my_mlir-aie", "main"),
    "onnx2versal":           ("rehohoho/onnx2versal", "main"),
    "polyaie":               ("hanchenye/polyaie", "main"),
    "SSR":                   ("arc-research-lab/SSR", "main"),
    "sycl":                  ("intel/llvm", "sycl"),
    "Vitis-Tutorials":       ("Xilinx/Vitis-Tutorials", "2024.2"),
    "Vitis_Libraries":       ("Xilinx/Vitis_Libraries", "main"),
    "Vitis_Model_Composer":  ("Xilinx/Vitis_Model_Composer", "main"),
    "Xilinx-Vitis-Tutorials":("Xilinx/Vitis-Tutorials", "2024.2"),
    "XOHW-23-Versal-Registration": ("Paolo309/XOHW-23-Versal-Registration", "main"),
    "xup_aie_training":      ("Xilinx/xup_aie_training", "main"),
}


def content_hash(code: str) -> str:
    return hashlib.sha256(code.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def is_aie_candidate(code: str) -> bool:
    low = code.lower()
    return any(k.lower() in low for k in KERNEL_KEYWORDS + GRAPH_KEYWORDS)


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
    if any(t in low for t in ["input_async_buffer", "output_async_buffer", "async_buffer"]):
        out.append("async")
    if any(t in low for t in ["plio", "gmio"]):
        out.append("external_io")
    return out or ["unknown"]


def detect_data_types(code: str) -> list[str]:
    low = code.lower()
    return [t for t in ["int8", "int16", "int32", "float", "cint16", "cint32", "cfloat"] if t in low] or ["unknown"]


def detect_compute_patterns(code: str) -> list[str]:
    low = code.lower()
    checks = {
        "fir_filter": ["fir", "tap", "mac"],
        "fft_butterfly": ["fft", "butterfly", "twiddle"],
        "matrix_multiply": ["gemm", "matmul", "matrix"],
        "beamforming": ["beam", "steer", "phase_shift"],
        "interpolation": ["interpol", "upsample"],
        "decimation": ["decim", "downsample"],
        "sorting_network": ["sort", "bitonic"],
        "peak_detection": ["peak", "threshold"],
        "correlation": ["correlation", "matched"],
        "qam_demodulation": ["qam", "llr", "constellation"],
        "channel_estimation": ["channel estimation", "pilot"],
        "cfar_detection": ["cfar"],
        "digital_downconversion": ["downconversion", "nco", "mixer"],
        "ldpc_update": ["ldpc", "belief"],
        "viterbi_decoder": ["viterbi", "trellis"],
    }
    return [p for p, hints in checks.items() if any(h in low for h in hints)] or ["generic_aie"]


def load_existing_hashes() -> tuple[set[str], set[str]]:
    """Returns (content_hashes, source_urls) from existing corpus files."""
    hashes: set[str] = set()
    urls: set[str] = set()
    for path in EXISTING_CORPUS_FILES:
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


def load_p2_hashes(output_path: Path) -> tuple[set[str], set[str]]:
    """Load hashes+urls already written to the output file (for resume support)."""
    hashes: set[str] = set()
    urls: set[str] = set()
    if not output_path.exists():
        return hashes, urls
    with output_path.open("r", encoding="utf-8") as fh:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Mine local AIE repos into corpus_p2.")
    parser.add_argument("--external-dir", default=str(EXTERNAL_DIR))
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--max-bytes", type=int, default=MAX_FILE_BYTES)
    parser.add_argument("--repos", nargs="+", help="Only process these repo names (default: all)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    external_dir = Path(args.external_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Loading existing corpus hashes for dedup...")
    seen_hashes, seen_urls = load_existing_hashes()
    print(f"  {len(seen_hashes)} content hashes, {len(seen_urls)} URLs loaded from existing corpus")

    p2_hashes, p2_urls = load_p2_hashes(output_path)
    seen_hashes |= p2_hashes
    seen_urls |= p2_urls
    print(f"  {len(p2_hashes)} hashes already in p2 output (resume)")

    repo_dirs = sorted(d for d in external_dir.iterdir() if d.is_dir())
    if args.repos:
        repo_dirs = [d for d in repo_dirs if d.name in args.repos]

    new_entries: list[dict] = []
    skipped_size = skipped_dedup = skipped_not_aie = 0
    repo_counts: Counter[str] = Counter()

    for repo_dir in repo_dirs:
        repo_name = repo_dir.name
        full_name, branch = REPO_META.get(repo_name, (f"unknown/{repo_name}", "main"))

        for f in sorted(repo_dir.rglob("*")):
            if not f.is_file():
                continue
            if f.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            # Skip build artifacts and test outputs
            parts_lower = [p.lower() for p in f.parts]
            if any(skip in parts_lower for skip in [".autopilot", "_x", "build", "__pycache__"]):
                continue
            if f.stat().st_size > args.max_bytes:
                skipped_size += 1
                continue
            try:
                code = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not is_aie_candidate(code):
                skipped_not_aie += 1
                continue
            h = content_hash(code)
            if h in seen_hashes:
                skipped_dedup += 1
                continue
            rel_path = f.relative_to(external_dir).as_posix()
            source_url = f"https://github.com/{full_name}/blob/{branch}/{f.relative_to(repo_dir).as_posix()}"
            if source_url in seen_urls:
                skipped_dedup += 1
                continue
            seen_hashes.add(h)
            seen_urls.add(source_url)
            category = detect_category(code)

            entry = {
                "filename": f.name,
                "code": code.replace("\r\n", "\n").strip(),
                "source": source_url,
                "category": category,
                "bug_type": None,
                "bug_explanation": None,
                "metadata": {
                    "path": rel_path,
                    "compute_pattern": detect_compute_patterns(code),
                    "data_types": detect_data_types(code),
                    "interfaces": detect_interfaces(code),
                    "file_size_bytes": f.stat().st_size,
                    "local_path": str(f.relative_to(external_dir / repo_name).as_posix()),
                },
                "source_url": source_url,
                "repo": full_name,
                "branch": branch,
            }
            new_entries.append(entry)
            repo_counts[full_name] += 1

    print(f"\n=== Mining results ===")
    print(f"  New entries found:   {len(new_entries)}")
    print(f"  Skipped (dedup):     {skipped_dedup}")
    print(f"  Skipped (not AIE):   {skipped_not_aie}")
    print(f"  Skipped (too large): {skipped_size}")
    print(f"\n  Breakdown by repo:")
    for repo, count in repo_counts.most_common():
        print(f"    {count:4d}  {repo}")

    if args.dry_run:
        print("\n[dry-run] No files written.")
        return

    # Append to output (resume-safe)
    mode = "a" if p2_hashes else "w"
    with output_path.open(mode, encoding="utf-8") as fh:
        for entry in new_entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    total_p2 = len(p2_hashes) + len(new_entries)
    print(f"\nWrote {len(new_entries)} new entries → {output_path}")
    print(f"Total in p2 corpus: {total_p2}")


if __name__ == "__main__":
    main()
