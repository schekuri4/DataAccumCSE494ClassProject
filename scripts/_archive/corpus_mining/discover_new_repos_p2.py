#!/usr/bin/env python3
"""
Discover new GitHub repos containing AIE code, vet them for quality,
then fetch their AIE source files into data/raw/aie_local_corpus_p2.jsonl.

Skips all repos already in the existing corpus.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
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
API_DELAY = 0.4

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

PATH_HINTS = [
    "aie", "graph", "kernel", "versal", "adf", "gmio", "plio",
    "fft", "fir", "matmul", "gemm", "beam", "channel", "demod",
]

KNOWN_REPOS = {
    "Xilinx/Vitis-Tutorials", "Xilinx/Vitis-In-Depth-Tutorial", "Xilinx/Vitis_Accel_Examples",
    "Xilinx/Vitis-AI", "Xilinx/XRT", "Xilinx/embeddedsw", "Xilinx/Vitis_Libraries",
    "Xilinx/Vitis_Model_Composer", "Xilinx/mlir-aie", "Xilinx/xup_aie_training",
    "AMD/RyzenAI-SW", "amd/RyzenAI-SW",
    "arc-research-lab/Aries", "arc-research-lab/AIM", "arc-research-lab/SSR",
    "advent-lab/GAMA", "enyac-group/MaxEVA", "hanchenye/polyaie",
    "rehohoho/onnx2versal", "Paolo309/XOHW-23-Versal-Registration",
    "nod-ai/iree-amd-aie", "pjh177787/my_mlir-aie",
}

SEARCH_QUERIES = [
    '"aie_api/aie.hpp" language:c++',
    '"input_buffer" "aie::vector" language:c++',
    '"chess_prepare_for_pipelining" language:c++',
    '"adf::graph" "adf::kernel" language:c++',
    '"input_window" "output_window" "aie" language:c++',
    '"writeincr" "readincr" "aie" language:c++',
]

# Min files an AIE repo must have to be worth fetching
MIN_AIE_FILES = 3

# Curated high-value repos to fetch directly (--curated mode bypasses discovery)
CURATED_REPOS = [
    # AMD Official
    "AMDResearch/NPUEval",
    "AMDResearch/Riallto",
    "amd/IRON",
    "amd/transformers",
    "Xilinx/aie_api",
    "Xilinx/aiebaremetal",
    "Xilinx/aiehlc",
    "Xilinx/AI-Engine-Test-Harness",
    "Xilinx/vitis_templates",
    "Xilinx/mlir-air",
    "Xilinx/plnx-aie-examples",
    "Xilinx/emb_plus_vitis_platforms",
    "Xilinx/sandpiper",
    # Research Labs
    "advent-lab/IRONSmith",
    "SPIRE-GMU/AXIOS",
    "SPIRE-GMU/tfhe-aie",
    "SPIRE-GMU/SPIRE-ARKANE",
    "SPIRE-GMU/binius-spire",
    "KastnerRG/aie-intrinsics-nn",
    "KastnerRG/hls4ml-backend",
    "necst/AIE-kmeans",
    "necst/Hpps24-fpga2aie",
    "necst/peterpan",
    "necst/trilli",
    "necst/voted",
    "atlarge-research/AIE-BLAS",
    "accl-kaust/fp-versal-bench",
    "accl-kaust/PRNGine",
    "accl-kaust/mc-option-pricing-aie",
    "cornell-zhang/allo",
    "nasa-jpl/versal-sar-backprojection-design",
    # Individual Research / FPGA Competition
    "GeertRoks/AMD-Versal-phylogenetic-likelihood-function",
    "Jingyi-li/SCD_VCK5000",
    "Jingyi-li/SSCA_Implementation",
    "JinmingZhuang/CHARM",
    "JinmingZhuang/FPGA25_ARIES_AE",
    "zerefwayne/kmeans-aie",
    "ilot95/amd-aie-tests",
    "searsm8/AIEplace",
    "lele1001/AOHW-334",
    "Yanze66/AIE_XMSS",
    "Yanze66/KAN_AIE",
    "Yanze66/junction_tree",
    "qingyin-alice-zhong/decomposition_kernel",
    "ChengyueWang/ISCA25-Stream-Network-Arch",
    "Fahim-103/NTT_Code",
    "omaralkhatib03/mudkip",
    "opensensor/bionpu",
    "adps/anomaly-aie-lstm",
    "bol-edu/2023-spring-nthu",
    "vickyiii/WinterCamp-2023-AIE",
    "dsnehadri/aie-unsupervised-search",
    "FedericoMansutti/Accelerated_Similarity_Metric_Library_CC_MSE",
    "SeiHau02250816/AMD-Xilinx-AI-Engine-based-High-Speed-Viterbi-Decoding",
    "circuitmaster/VVC-Decoder-Accelerator",
    "ngdymx/NPU_FDTD",
    "joeldushouyu/LinearStateSpaceCircuitSimulation",
]


def content_hash(code: str) -> str:
    return hashlib.sha256(code.replace("\r\n", "\n").strip().encode("utf-8")).hexdigest()


def is_valid_path(path: str) -> bool:
    if Path(path).suffix.lower() not in SOURCE_SUFFIXES:
        return False
    low = "/" + path.replace("\\", "/").lower() + "/"
    if any(frag in low for frag in SKIP_PATH_FRAGMENTS):
        return False
    return any(hint in low for hint in PATH_HINTS)


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
        "fft_butterfly": ["fft", "butterfly"],
        "matrix_multiply": ["gemm", "matmul", "matrix"],
        "beamforming": ["beam", "steer"],
        "interpolation": ["interpol", "upsample"],
        "decimation": ["decim", "downsample"],
        "sorting_network": ["sort", "bitonic"],
        "qam_demodulation": ["qam", "llr"],
        "cfar_detection": ["cfar"],
        "digital_downconversion": ["nco", "mixer"],
        "ldpc_update": ["ldpc"],
        "viterbi_decoder": ["viterbi"],
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


class GitHubClient:
    def __init__(self, token: str | None) -> None:
        self.token = token
        self.last_request = 0.0
        self.requests_made = 0
        self.rate_remaining = 5000

    def _headers(self) -> dict:
        h = {
            "User-Agent": "AIE-Corpus-Miner",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _get(self, url: str) -> dict | None:
        elapsed = time.monotonic() - self.last_request
        if elapsed < API_DELAY:
            time.sleep(API_DELAY - elapsed)
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                self.rate_remaining = int(resp.headers.get("X-RateLimit-Remaining", self.rate_remaining))
                self.requests_made += 1
                self.last_request = time.monotonic()
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 403:
                print(f"  [rate-limit] sleeping 60s (remaining={self.rate_remaining})")
                time.sleep(60)
            elif e.code == 422:
                pass  # search index not available for this repo
            return None
        except Exception:
            return None

    def search_code(self, query: str) -> list[dict]:
        items: list[dict] = []
        for page in range(1, 11):
            params = urllib.parse.urlencode({"q": query, "per_page": 100, "page": page})
            data = self._get(f"https://api.github.com/search/code?{params}")
            if not data:
                break
            page_items = data.get("items", [])
            items.extend(page_items)
            if len(page_items) < 100:
                break
            time.sleep(2)  # search API is stricter
        return items

    def get_tree(self, full_name: str, ref: str = "HEAD") -> dict | None:
        quoted = urllib.parse.quote(ref, safe="")
        return self._get(f"https://api.github.com/repos/{full_name}/git/trees/{quoted}?recursive=1")

    def get_blob(self, full_name: str, blob_sha: str) -> str | None:
        # Check cache first
        cache_path = self._blob_cache_path(full_name, blob_sha)
        if cache_path.exists():
            blob = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            blob = self._get(f"https://api.github.com/repos/{full_name}/git/blobs/{blob_sha}")
            if blob is None:
                return None
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(blob, indent=2), encoding="utf-8")

        content = str(blob.get("content") or "")
        if not content:
            return ""
        if blob.get("encoding") == "base64":
            return base64.b64decode(content.encode("ascii")).decode("utf-8", errors="replace")
        return content

    def _blob_cache_path(self, full_name: str, blob_sha: str) -> Path:
        url = f"https://api.github.com/repos/{full_name}/git/blobs/{blob_sha}"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return CACHE_DIR / "blobs" / f"{digest}.json"

    def get_default_branch(self, full_name: str) -> str:
        data = self._get(f"https://api.github.com/repos/{full_name}")
        return (data or {}).get("default_branch", "main")


def discover_new_repos(client: GitHubClient) -> dict[str, dict]:
    """Run code searches and return new repos not in KNOWN_REPOS."""
    discovered: dict[str, dict] = {}
    for query in SEARCH_QUERIES:
        print(f"  Searching: {query}")
        items = client.search_code(query)
        for item in items:
            repo_meta = item.get("repository", {})
            full_name = repo_meta.get("full_name", "")
            if not full_name:
                continue
            if full_name in KNOWN_REPOS:
                continue
            if full_name not in discovered:
                discovered[full_name] = repo_meta
        print(f"    -> {len(items)} hits, {len(discovered)} new repos so far")
        time.sleep(3)  # search API rate limit
    return discovered


def vet_repo(client: GitHubClient, full_name: str) -> tuple[bool, str, int]:
    """
    Check if a repo has enough AIE files to be worth including.
    Returns (ok, default_branch, aie_file_count).
    """
    branch = client.get_default_branch(full_name)
    tree = client.get_tree(full_name, "HEAD")
    if not tree:
        return False, branch, 0

    aie_count = 0
    for entry in tree.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path = str(entry.get("path") or "")
        size = int(entry.get("size") or 0)
        if is_valid_path(path) and 0 < size <= MAX_BLOB_BYTES:
            aie_count += 1

    return aie_count >= MIN_AIE_FILES, branch, aie_count


def fetch_repo_blobs(
    client: GitHubClient,
    full_name: str,
    branch: str,
    seen_hashes: set[str],
    seen_urls: set[str],
    out_fh,
) -> int:
    """Fetch all AIE blobs from a repo, write new ones to out_fh. Returns count written."""
    tree = client.get_tree(full_name, "HEAD")
    if not tree:
        return 0

    written = 0
    for entry in tree.get("tree", []):
        if entry.get("type") != "blob":
            continue
        rel_path = str(entry.get("path") or "").replace("\\", "/")
        if not is_valid_path(rel_path):
            continue
        blob_size = int(entry.get("size") or 0)
        if blob_size <= 0 or blob_size > MAX_BLOB_BYTES:
            continue
        blob_sha = str(entry.get("sha") or "")
        if not blob_sha:
            continue
        source_url = f"https://github.com/{full_name}/blob/{branch}/{rel_path}"
        if source_url in seen_urls:
            continue

        code = client.get_blob(full_name, blob_sha)
        if not code:
            continue
        code = code.replace("\r\n", "\n").strip()
        if not is_aie_candidate(code):
            continue
        h = content_hash(code)
        if h in seen_hashes:
            continue

        seen_hashes.add(h)
        seen_urls.add(source_url)

        entry_out = {
            "filename": Path(rel_path).name,
            "code": code,
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
                "source": "github_search_discover",
            },
            "source_url": source_url,
            "repo": full_name,
            "branch": branch,
        }
        out_fh.write(json.dumps(entry_out, ensure_ascii=False) + "\n")
        out_fh.flush()
        written += 1
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover and fetch new AIE repos into corpus_p2.")
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--dry-run", action="store_true", help="Discover only, don't fetch blobs")
    parser.add_argument("--min-files", type=int, default=MIN_AIE_FILES)
    parser.add_argument("--curated", action="store_true", help="Skip discovery; fetch only the CURATED_REPOS list")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("ERROR: GITHUB_TOKEN not set")
        return

    client = GitHubClient(token)
    output_path = Path(args.output)

    print("Loading existing corpus for dedup...")
    seen_hashes, seen_urls = load_existing_seen()
    print(f"  {len(seen_hashes)} content hashes, {len(seen_urls)} URLs")

    if args.curated:
        print(f"\nCurated mode: fetching {len(CURATED_REPOS)} pre-selected repos")
        good_repos: list[tuple[str, str, int]] = []
        for full_name in CURATED_REPOS:
            branch = client.get_default_branch(full_name)
            good_repos.append((full_name, branch, 0))
            print(f"  queued  {full_name}  (branch={branch})")
    else:
        print("\nDiscovering new repos via code search...")
        new_repos = discover_new_repos(client)
        print(f"\nFound {len(new_repos)} candidate repos not in known set")

        # Vet each repo
        print("\nVetting repos for AIE file count...")
        good_repos = []  # (full_name, branch, count)
        for full_name, meta in sorted(new_repos.items()):
            ok, branch, count = vet_repo(client, full_name)
            status = "OK" if ok else "SKIP"
            print(f"  [{status}] {full_name}  ({count} AIE files, branch={branch})")
            if ok:
                good_repos.append((full_name, branch, count))

        print(f"\n{len(good_repos)} repos passed vetting (>= {args.min_files} AIE files)")
        for fn, br, ct in sorted(good_repos, key=lambda x: -x[2]):
            print(f"  {ct:4d}  {fn}")

    if args.dry_run:
        print("\n[dry-run] Skipping blob fetch.")
        return

    print("\nFetching blobs...")
    repo_counts: Counter[str] = Counter()
    total_written = 0
    with output_path.open("a", encoding="utf-8") as out_fh:
        for full_name, branch, _ in good_repos:
            n = fetch_repo_blobs(client, full_name, branch, seen_hashes, seen_urls, out_fh)
            repo_counts[full_name] = n
            total_written += n
            print(f"  {n:4d} new  {full_name}")

    print(f"\nTotal new entries written: {total_written} → {output_path}")
    print(f"API calls made: {client.requests_made}")


if __name__ == "__main__":
    main()
