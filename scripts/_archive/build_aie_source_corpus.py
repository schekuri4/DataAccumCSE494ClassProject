from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw"
DEFAULT_SCRAPE_OUTPUT = DEFAULT_OUTPUT_DIR / "aie_github_sources.jsonl"
DEFAULT_SYNTHETIC_OUTPUT = DEFAULT_OUTPUT_DIR / "aie_synthetic_sources.jsonl"
DEFAULT_COMBINED_OUTPUT = DEFAULT_OUTPUT_DIR / "aie_expanded_sources.jsonl"
DEFAULT_CACHE_DIR = ROOT / ".cache" / "aie_source_corpus"
USER_AGENT = "GitHub-Copilot-AIE-Corpus-Builder"
SOURCE_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}
API_DELAY_SECONDS = 1.0
CODE_SEARCH_PAGE_SIZE = 100
CODE_SEARCH_MAX_RESULTS = 1000
SYNTHETIC_SEED = 3407

KERNEL_KEYWORDS = [
    "aie::vector",
    "input_buffer",
    "output_buffer",
    "input_stream",
    "output_stream",
    "chess_prepare_for_pipelining",
    "aie::begin_vector",
    "readincr",
    "writeincr",
    "input_window",
    "output_window",
    "aie::accum",
]

GRAPH_KEYWORDS = [
    "adf::graph",
    "adf::connect",
    "adf::PLIO",
    "adf::GMIO",
    "adf::runtime",
    "adf::parameter",
    "kernel::create",
    "source(",
]

PATH_HINTS = [
    "aie",
    "graph",
    "kernel",
    "versal",
    "adf",
    "gmio",
    "plio",
    "beam",
    "fft",
    "fir",
    "matmul",
    "gemm",
    "channel",
    "demod",
    "coder",
    "cfar",
]

COMMUNITY_CODE_SEARCH_QUERIES = [
    '"aie::vector" language:cpp',
    '"input_buffer" "aie_api" language:cpp',
    '"adf::graph" "adf::kernel" language:cpp',
    '"chess_prepare_for_pipelining" language:cpp',
    '"#include <aie_api/aie.hpp>" language:cpp',
    '"#include <adf.h>" language:cpp',
]

DISCOVERY_QUERIES = [
    '"aie_api/aie.hpp" language:cpp',
    '"adf.h" language:cpp',
]

BUG_TYPES = [
    "unconsumed_stream_deadlock",
    "buffer_size_mismatch",
    "wrong_vector_lane_width",
    "missing_chess_prepare_for_pipelining",
    "off_by_one_circular_buffer_index",
    "incorrect_lock_ordering",
    "stream_tlast_missing",
    "rtp_read_inside_loop",
    "mismatched_plio_width",
    "blocking_read_on_async_port",
    "double_free_buffer_iterator",
    "uninitialized_accumulator",
    "incorrect_shuffle_mode",
    "output_buffer_overrun",
    "wrong_accumulator_mode",
]


@dataclass(frozen=True)
class RepoTarget:
    owner: str
    repo: str
    branches: tuple[str, ...] = ()
    include_prefixes: tuple[str, ...] = ()

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass
class CorpusEntry:
    filename: str
    code: str
    source: str
    category: str
    bug_type: str | None
    bug_explanation: str | None
    metadata: dict
    source_url: str | None = None
    repo: str | None = None
    branch: str | None = None

    def to_dict(self) -> dict:
        payload = {
            "filename": self.filename,
            "code": self.code,
            "source": self.source,
            "category": self.category,
            "bug_type": self.bug_type,
            "bug_explanation": self.bug_explanation,
            "metadata": self.metadata,
        }
        if self.source_url is not None:
            payload["source_url"] = self.source_url
        if self.repo is not None:
            payload["repo"] = self.repo
        if self.branch is not None:
            payload["branch"] = self.branch
        return payload


@dataclass(frozen=True)
class DataTypeSpec:
    name: str
    scalar_type: str
    vector_width: int
    accum_type: str
    sample_literal: str
    coeff_literal: str
    zero_literal: str
    complex_type: bool = False


@dataclass(frozen=True)
class KernelSpec:
    compute_pattern: str
    data_type: DataTypeSpec
    interface: str
    complexity: str
    variant_index: int


@dataclass(frozen=True)
class GraphSpec:
    topology: str
    io_width_bits: int
    interface: str
    parameter_mode: str
    complexity: str
    variant_index: int


REPO_TARGETS = [
    RepoTarget("Xilinx", "Vitis-Tutorials", branches=("2022.1", "2022.2", "2023.1", "2023.2", "2024.1", "2024.2")),
    RepoTarget("Xilinx", "Vitis-In-Depth-Tutorial"),
    RepoTarget("Xilinx", "Vitis_Accel_Examples"),
    RepoTarget("Xilinx", "Vitis-AI"),
    RepoTarget("Xilinx", "XRT"),
    RepoTarget("Xilinx", "embeddedsw"),
    RepoTarget(
        "Xilinx",
        "Vitis_Libraries",
        include_prefixes=("dsp/", "data_compression/", "solver/", "blas/"),
    ),
    RepoTarget("Xilinx", "Vitis_Model_Composer"),
    RepoTarget("AMD", "RyzenAI-SW"),
    RepoTarget("arc-research-lab", "Aries", include_prefixes=("example_new/", "templates/")),
    RepoTarget("arc-research-lab", "AIM", include_prefixes=("application/", "template/")),
    RepoTarget("arc-research-lab", "SSR", include_prefixes=("SSR_Designs_Experiments/",)),
    RepoTarget("advent-lab", "GAMA", include_prefixes=("aie/",)),
    RepoTarget(
        "enyac-group",
        "MaxEVA",
        include_prefixes=("Pattern1_fp32/src/", "Pattern1_int8/src/", "Pattern2_fp32/src/", "Pattern2_int8/src/"),
    ),
    RepoTarget("hanchenye", "polyaie", include_prefixes=("polyaie/", "samples/")),
    RepoTarget("rehohoho", "onnx2versal", include_prefixes=("design/aie_src/",)),
    RepoTarget("Paolo309", "XOHW-23-Versal-Registration", include_prefixes=("aie/",)),
    RepoTarget("nod-ai", "iree-amd-aie", include_prefixes=("compiler/", "runtime/", "tests/")),
    RepoTarget("pjh177787", "my_mlir-aie", include_prefixes=("programming_examples/", "test/")),
]

DATA_TYPE_SPECS = [
    DataTypeSpec("int8", "int8", 32, "acc48", "1", "2", "0"),
    DataTypeSpec("int16", "int16", 16, "acc48", "3", "4", "0"),
    DataTypeSpec("int32", "int32", 8, "acc80", "5", "6", "0"),
    DataTypeSpec("float", "float", 8, "accfloat", "1.0f", "0.5f", "0.0f"),
    DataTypeSpec("cint16", "cint16", 8, "cacc48", "cint16(1, -1)", "cint16(2, 1)", "cint16(0, 0)", complex_type=True),
    DataTypeSpec("cint32", "cint32", 4, "cacc80", "cint32(2, -2)", "cint32(1, 3)", "cint32(0, 0)", complex_type=True),
    DataTypeSpec("cfloat", "cfloat", 4, "caccfloat", "cfloat(1.0f, -0.5f)", "cfloat(0.25f, 0.75f)", "cfloat(0.0f, 0.0f)", complex_type=True),
]

KERNEL_PATTERNS = [
    "fir_filter",
    "fft_butterfly",
    "matrix_multiply",
    "beamforming",
    "interpolation",
    "decimation",
    "lut_nonlinear",
    "sorting_network",
    "crc_computation",
    "peak_detection",
    "moving_average",
    "correlation",
    "polyphase_filterbank",
    "cordic_rotation",
    "viterbi_decoder",
    "ldpc_update",
    "qam_demodulation",
    "channel_estimation",
    "mimo_precoding",
    "sample_rate_conversion",
    "noise_shaping",
    "envelope_detection",
    "automatic_gain_control",
    "pulse_compression",
    "matched_filtering",
    "doppler_processing",
    "cfar_detection",
    "digital_downconversion",
    "numerically_controlled_oscillator",
]

GRAPH_TOPOLOGIES = [
    "single_kernel",
    "linear_chain",
    "broadcast_merge",
    "feedback_loop",
    "multirate_pipeline",
    "shared_buffer_fanout",
    "subgraph_composition",
    "multi_input_multi_output",
]

KERNEL_INTERFACES = ["buffer_only", "stream_only", "buffer_stream", "cascade", "async"]
GRAPH_INTERFACES = ["plio", "gmio", "plio_gmio"]
COMPLEXITY_LEVELS = ["simple", "moderate", "complex"]


class GitHubClient:
    def __init__(self, token: str | None, cache_dir: Path, delay_seconds: float = API_DELAY_SECONDS) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self.delay_seconds = delay_seconds
        self.last_request_time = 0.0

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _sleep_if_needed(self) -> None:
        elapsed = time.monotonic() - self.last_request_time
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    def _cache_path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / namespace / f"{digest}.json"

    def get_json(self, url: str, namespace: str = "api") -> dict:
        cache_path = self._cache_path(namespace, url)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        self._sleep_if_needed()
        request = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API request failed for {url}: {exc.code} {body}") from exc

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.last_request_time = time.monotonic()
        return payload

    def get_repo(self, full_name: str) -> dict:
        return self.get_json(f"https://api.github.com/repos/{full_name}", namespace="repos")

    def get_tree(self, full_name: str, ref: str) -> dict:
        quoted_ref = urllib.parse.quote(ref, safe="")
        return self.get_json(
            f"https://api.github.com/repos/{full_name}/git/trees/{quoted_ref}?recursive=1",
            namespace="trees",
        )

    def get_blob_text(self, full_name: str, sha: str) -> str:
        payload = self.get_json(
            f"https://api.github.com/repos/{full_name}/git/blobs/{sha}",
            namespace="blobs",
        )
        encoding = payload.get("encoding")
        content = payload.get("content", "")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return content

    def search_code(self, query: str) -> list[dict]:
        items: list[dict] = []
        max_page = CODE_SEARCH_MAX_RESULTS // CODE_SEARCH_PAGE_SIZE
        for page in range(1, max_page + 1):
            params = urllib.parse.urlencode(
                {
                    "q": query,
                    "per_page": CODE_SEARCH_PAGE_SIZE,
                    "page": page,
                }
            )
            url = f"https://api.github.com/search/code?{params}"
            payload = self.get_json(url, namespace="search")
            page_items = payload.get("items", [])
            items.extend(page_items)
            if len(page_items) < CODE_SEARCH_PAGE_SIZE:
                break
        return items


def normalize_code(code: str) -> str:
    return code.replace("\r\n", "\n").strip()


def path_is_candidate(path: str, include_prefixes: tuple[str, ...]) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix not in SOURCE_SUFFIXES:
        return False
    lowered = path.lower()
    if include_prefixes and not any(lowered.startswith(prefix.lower()) for prefix in include_prefixes):
        return False
    return any(hint in lowered for hint in PATH_HINTS)


def code_is_candidate(code: str) -> bool:
    lowered = code.lower()
    if any(keyword.lower() in lowered for keyword in KERNEL_KEYWORDS):
        return True
    if any(keyword.lower() in lowered for keyword in GRAPH_KEYWORDS):
        return True
    return False


def detect_category(code: str) -> str:
    lowered = code.lower()
    if "adf::graph" in lowered or "connect<" in lowered or "kernel::create" in lowered:
        return "graph"
    return "kernel"


def detect_interfaces(code: str) -> list[str]:
    lowered = code.lower()
    interfaces = []
    if any(token in lowered for token in ["input_buffer", "output_buffer", "input_window", "output_window"]):
        interfaces.append("buffer")
    if any(token in lowered for token in ["input_stream", "output_stream", "readincr", "writeincr"]):
        interfaces.append("stream")
    if "cascade" in lowered:
        interfaces.append("cascade")
    if any(token in lowered for token in ["async_buffer", "input_async_buffer", "output_async_buffer"]):
        interfaces.append("async")
    if any(token in lowered for token in ["plio", "gmio"]):
        interfaces.append("external_io")
    return interfaces or ["unknown"]


def detect_data_types(code: str) -> list[str]:
    lowered = code.lower()
    types = []
    for candidate in ["int8", "int16", "int32", "float", "cint16", "cint32", "cfloat"]:
        if candidate in lowered:
            types.append(candidate)
    return types or ["unknown"]


def detect_compute_patterns(code: str) -> list[str]:
    lowered = code.lower()
    pattern_checks = {
        "fir_filter": ["fir", "tap", "sliding", "mac"],
        "fft_butterfly": ["fft", "butterfly", "twiddle"],
        "matrix_multiply": ["gemm", "matmul", "matrix"],
        "beamforming": ["beam", "steer", "phase_shift"],
        "interpolation": ["interpol", "upsample"],
        "decimation": ["decim", "downsample"],
        "lut_nonlinear": ["lut", "lookup", "piecewise"],
        "sorting_network": ["sort", "bitonic", "merge_sort"],
        "crc_computation": ["crc", "polynomial"],
        "peak_detection": ["peak", "threshold"],
        "moving_average": ["moving average", "boxcar"],
        "correlation": ["correlation", "matched"],
        "polyphase_filterbank": ["polyphase", "channelizer"],
        "cordic_rotation": ["cordic", "rotation"],
        "viterbi_decoder": ["viterbi", "trellis"],
        "ldpc_update": ["ldpc", "belief"],
        "qam_demodulation": ["qam", "llr", "constellation"],
        "channel_estimation": ["channel estimation", "pilot"],
        "mimo_precoding": ["mimo", "precoding"],
        "sample_rate_conversion": ["sample rate", "resampler"],
        "noise_shaping": ["noise shaping", "sigma delta"],
        "envelope_detection": ["envelope"],
        "automatic_gain_control": ["agc", "gain control"],
        "pulse_compression": ["pulse compression", "chirp"],
        "matched_filtering": ["matched filter"],
        "doppler_processing": ["doppler"],
        "cfar_detection": ["cfar"],
        "digital_downconversion": ["downconversion", "nco", "mixer"],
        "numerically_controlled_oscillator": ["nco", "phase accumulator"],
    }
    matches = []
    for pattern, hints in pattern_checks.items():
        if any(hint in lowered for hint in hints):
            matches.append(pattern)
    return matches or ["generic_aie"]


def make_content_hash(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode("utf-8")).hexdigest()


def build_source_url(repo: str, branch: str, path: str) -> str:
    encoded_path = "/".join(urllib.parse.quote(part) for part in path.split("/"))
    return f"https://github.com/{repo}/blob/{branch}/{encoded_path}"


def collect_repo_branch(
    client: GitHubClient,
    target: RepoTarget,
    branch: str,
    seen_hashes: set[str],
) -> list[CorpusEntry]:
    entries: list[CorpusEntry] = []
    tree = client.get_tree(target.full_name, branch)
    for item in tree.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if not path_is_candidate(path, target.include_prefixes):
            continue
        code = client.get_blob_text(target.full_name, item["sha"])
        if not code_is_candidate(code):
            continue
        content_hash = make_content_hash(code)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        category = detect_category(code)
        entries.append(
            CorpusEntry(
                filename=Path(path).name,
                code=normalize_code(code),
                source=build_source_url(target.full_name, branch, path),
                source_url=build_source_url(target.full_name, branch, path),
                repo=target.full_name,
                branch=branch,
                category=category,
                bug_type=None,
                bug_explanation=None,
                metadata={
                    "path": path,
                    "compute_pattern": detect_compute_patterns(code),
                    "data_types": detect_data_types(code),
                    "interfaces": detect_interfaces(code),
                    "complexity": "real_world",
                    "origin": "github_tree_scan",
                },
            )
        )
    return entries


def collect_seed_repo_entries(client: GitHubClient, seen_hashes: set[str]) -> list[CorpusEntry]:
    entries: list[CorpusEntry] = []
    for target in REPO_TARGETS:
        if target.branches:
            branches = list(target.branches)
        else:
            repo_payload = client.get_repo(target.full_name)
            branches = [repo_payload["default_branch"]]
        for branch in branches:
            entries.extend(collect_repo_branch(client, target, branch, seen_hashes))
    return entries


def collect_search_entries(
    client: GitHubClient,
    queries: Iterable[str],
    seen_hashes: set[str],
) -> tuple[list[CorpusEntry], set[str]]:
    entries: list[CorpusEntry] = []
    discovered_repos: set[str] = set()
    for query in queries:
        for item in client.search_code(query):
            repository = item["repository"]["full_name"]
            discovered_repos.add(repository)
            path = item["path"]
            if Path(path).suffix.lower() not in SOURCE_SUFFIXES:
                continue
            code = client.get_blob_text(repository, item["sha"])
            if not code_is_candidate(code):
                continue
            content_hash = make_content_hash(code)
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            branch = item["repository"].get("default_branch")
            source_url = build_source_url(repository, branch, path) if branch else item.get("html_url")
            entries.append(
                CorpusEntry(
                    filename=Path(path).name,
                    code=normalize_code(code),
                    source=source_url or item.get("html_url") or repository,
                    source_url=source_url or item.get("html_url"),
                    repo=repository,
                    branch=branch,
                    category=detect_category(code),
                    bug_type=None,
                    bug_explanation=None,
                    metadata={
                        "path": path,
                        "compute_pattern": detect_compute_patterns(code),
                        "data_types": detect_data_types(code),
                        "interfaces": detect_interfaces(code),
                        "complexity": "real_world",
                        "origin": "github_code_search",
                        "query": query,
                    },
                )
            )
    return entries, discovered_repos


def collect_discovered_repo_entries(
    client: GitHubClient,
    discovered_repos: Iterable[str],
    seen_hashes: set[str],
) -> list[CorpusEntry]:
    existing_targets = {target.full_name for target in REPO_TARGETS}
    entries: list[CorpusEntry] = []
    for full_name in sorted(discovered_repos):
        if full_name in existing_targets:
            continue
        repo_payload = client.get_repo(full_name)
        branch = repo_payload["default_branch"]
        owner, repo = full_name.split("/", 1)
        target = RepoTarget(owner, repo)
        entries.extend(collect_repo_branch(client, target, branch, seen_hashes))
    return entries


def write_jsonl(path: Path, entries: Iterable[CorpusEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")


def summarize_real_coverage(entries: Iterable[CorpusEntry]) -> dict[str, Counter]:
    coverage = {
        "compute_pattern": Counter(),
        "data_types": Counter(),
        "interfaces": Counter(),
        "category": Counter(),
    }
    for entry in entries:
        coverage["category"][entry.category] += 1
        for pattern in entry.metadata.get("compute_pattern", []):
            coverage["compute_pattern"][pattern] += 1
        for dtype in entry.metadata.get("data_types", []):
            coverage["data_types"][dtype] += 1
        for interface in entry.metadata.get("interfaces", []):
            coverage["interfaces"][interface] += 1
    return coverage


def build_kernel_specs(target_count: int, coverage: dict[str, Counter]) -> list[KernelSpec]:
    candidates: list[tuple[tuple[int, int, int, int], KernelSpec]] = []
    for pattern_index, pattern in enumerate(KERNEL_PATTERNS):
        for dtype_index, dtype in enumerate(DATA_TYPE_SPECS):
            for interface_index, interface in enumerate(KERNEL_INTERFACES):
                for complexity_index, complexity in enumerate(COMPLEXITY_LEVELS):
                    score = (
                        -coverage["compute_pattern"][pattern],
                        -coverage["data_types"][dtype.name],
                        -coverage["interfaces"][interface.split("_")[0]],
                        -(pattern_index + dtype_index + interface_index + complexity_index),
                    )
                    candidates.append(
                        (
                            score,
                            KernelSpec(
                                compute_pattern=pattern,
                                data_type=dtype,
                                interface=interface,
                                complexity=complexity,
                                variant_index=pattern_index * 100 + dtype_index * 10 + interface_index + complexity_index,
                            ),
                        )
                    )
    candidates.sort(key=lambda item: item[0])
    return [spec for _, spec in candidates[:target_count]]


def build_graph_specs(target_count: int) -> list[GraphSpec]:
    specs: list[GraphSpec] = []
    variant = 0
    for topology in GRAPH_TOPOLOGIES:
        for io_width_bits in [32, 64, 128]:
            for interface in GRAPH_INTERFACES:
                for parameter_mode in ["static", "rtp", "mixed"]:
                    for complexity in COMPLEXITY_LEVELS:
                        specs.append(
                            GraphSpec(
                                topology=topology,
                                io_width_bits=io_width_bits,
                                interface=interface,
                                parameter_mode=parameter_mode,
                                complexity=complexity,
                                variant_index=variant,
                            )
                        )
                        variant += 1
    return specs[:target_count]


def join_lines(lines: Iterable[str], indent: int = 0) -> str:
    prefix = " " * indent
    return "\n".join(f"{prefix}{line}" if line else "" for line in lines)


def make_coeff_values(spec: DataTypeSpec) -> str:
    return ", ".join([spec.coeff_literal] * spec.vector_width)


def make_delay_values(spec: DataTypeSpec) -> str:
    return ", ".join([spec.zero_literal] * (spec.vector_width * 2))


def build_kernel_signature(class_name: str, spec: KernelSpec) -> tuple[str, list[str], list[str], list[str]]:
    dtype = spec.data_type.scalar_type
    width = spec.data_type.vector_width
    setup: list[str] = []
    reads: list[str] = []
    writes: list[str] = []
    if spec.interface == "buffer_only":
        signature = (
            f"void run(input_buffer<{dtype}, extents<{width * 16}>>& __restrict in, "
            f"output_buffer<{dtype}, extents<{width * 16}>>& __restrict out)"
        )
        setup.extend(
            [
                f"auto in_iter = aie::begin_vector<{width}>(in);",
                f"auto out_iter = aie::begin_restrict_vector<{width}>(out);",
            ]
        )
        reads.append("auto vin = *in_iter++;")
        writes.append("*out_iter++ = result.template to_vector<" + dtype + ">();")
    elif spec.interface == "stream_only":
        signature = f"void run(input_stream<{dtype}>* __restrict in, output_stream<{dtype}>* __restrict out)"
        reads.append(f"auto vin = readincr_v<{width}>(in);")
        writes.append("writeincr(out, result.template to_vector<" + dtype + ">());")
    elif spec.interface == "buffer_stream":
        signature = (
            f"void run(input_buffer<{dtype}, extents<{width * 16}>>& __restrict in, "
            f"output_stream<{dtype}>* __restrict out)"
        )
        setup.append(f"auto in_iter = aie::begin_vector<{width}>(in);")
        reads.append("auto vin = *in_iter++;")
        writes.append("writeincr(out, result.template to_vector<" + dtype + ">());")
    elif spec.interface == "cascade":
        signature = (
            f"void run(input_stream<{dtype}>* __restrict in, input_cascade<{dtype}>* __restrict cin, "
            f"output_cascade<{dtype}>* __restrict cout)"
        )
        reads.extend([f"auto vin = readincr_v<{width}>(in);", f"auto cascade_in = readincr_v<{width}>(cin);"])
        writes.append("writeincr(cout, result.template to_vector<" + dtype + ">());")
    else:
        signature = (
            f"void run(input_async_buffer<{dtype}>& __restrict in, output_async_buffer<{dtype}>& __restrict out)"
        )
        setup.extend(
            [
                "auto in_port = aie::begin_restrict_vector<" + str(width) + ">(in);",
                "auto out_port = aie::begin_restrict_vector<" + str(width) + ">(out);",
                "auto lock_in = acquire();",
            ]
        )
        reads.append("auto vin = *in_port++;")
        writes.extend(["*out_port++ = result.template to_vector<" + dtype + ">();", "release(lock_in);"])
    return signature, setup, reads, writes


def build_pattern_body(spec: KernelSpec) -> tuple[list[str], list[str]]:
    dtype = spec.data_type.scalar_type
    width = spec.data_type.vector_width
    complex_multiplier = "aie::conj(vin)" if spec.data_type.complex_type else "vin"
    state_lines = [
        f"alignas(32) {dtype} coeffs[{width}] = {{{make_coeff_values(spec.data_type)}}};",
        f"alignas(32) {dtype} delay_line[{width * 2}] = {{{make_delay_values(spec.data_type)}}};",
    ]
    bodies = {
        "fir_filter": [
            "state = aie::shuffle_up(state, vin, 1);",
            "acc = aie::mul(state, coeff);",
            "result = aie::add(acc, aie::broadcast<" + dtype + ", " + str(width) + ">(" + spec.data_type.zero_literal + "));",
        ],
        "fft_butterfly": [
            "auto upper = aie::shuffle_down(vin, vin, " + str(width // 2) + ");",
            "auto twiddled = aie::mul(upper, coeff);",
            "acc = aie::add(vin, twiddled);",
            "result = aie::sub(vin, twiddled);",
        ],
        "matrix_multiply": [
            "acc = aie::mul(vin, coeff);",
            "acc = aie::mac(acc, state, coeff);",
            "result = aie::add(acc, vin);",
        ],
        "beamforming": [
            "auto steered = aie::mul(vin, coeff);",
            "acc = aie::mac(aie::zeros<" + spec.data_type.accum_type + ", " + str(width) + ">(), steered, coeff);",
            "result = aie::add(acc, steered);",
        ],
        "interpolation": [
            "auto even_lanes = aie::interleave_zip(vin, state);",
            "acc = aie::mul(even_lanes.first, coeff);",
            "result = aie::add(acc, even_lanes.second);",
        ],
        "decimation": [
            "acc = aie::mul(vin, coeff);",
            "auto filtered = aie::add(acc, state);",
            "result = aie::shuffle_down(filtered, filtered, 2);",
        ],
        "lut_nonlinear": [
            "auto magnitude = aie::abs(vin);",
            "auto clamped = aie::min(magnitude, aie::broadcast<" + dtype + ", " + str(width) + ">(" + spec.data_type.coeff_literal + "));",
            "result = aie::select(vin, clamped, aie::gt(magnitude, clamped));",
        ],
        "sorting_network": [
            "auto shuffled = aie::shuffle(vin, vin, 0x32107654);",
            "auto mins = aie::min(vin, shuffled);",
            "auto maxs = aie::max(vin, shuffled);",
            "result = aie::interleave_unzip(mins, maxs).first;",
        ],
        "crc_computation": [
            "auto shifted = aie::bit_shift_left(vin, 1);",
            "auto taps = aie::bit_xor(shifted, coeff);",
            "result = aie::bit_xor(taps, state);",
        ],
        "peak_detection": [
            "auto peak_mask = aie::gt(vin, state);",
            "auto gated = aie::select(state, vin, peak_mask);",
            "result = aie::max(gated, coeff);",
        ],
        "moving_average": [
            "acc = aie::add(state, vin);",
            "acc = aie::sub(acc, coeff);",
            "result = aie::shift_right(acc, 2);",
        ],
        "correlation": [
            "auto matched = aie::mul(" + complex_multiplier + ", coeff);",
            "acc = aie::mac(aie::zeros<" + spec.data_type.accum_type + ", " + str(width) + ">(), matched, state);",
            "result = aie::add(acc, matched);",
        ],
        "polyphase_filterbank": [
            "auto poly0 = aie::shuffle(vin, state, 0x76543210);",
            "auto poly1 = aie::shuffle(state, vin, 0x32107654);",
            "acc = aie::add(aie::mul(poly0, coeff), aie::mul(poly1, coeff));",
            "result = aie::sub(acc, poly1);",
        ],
        "cordic_rotation": [
            "auto x = vin;",
            "auto y = state;",
            "auto x_shift = aie::shift_right(x, 1);",
            "auto y_shift = aie::shift_right(y, 1);",
            "result = aie::add(aie::sub(x, y_shift), x_shift);",
        ],
        "viterbi_decoder": [
            "auto branch0 = aie::add(vin, coeff);",
            "auto branch1 = aie::sub(vin, coeff);",
            "auto metric = aie::min(branch0, branch1);",
            "result = aie::add(metric, state);",
        ],
        "ldpc_update": [
            "auto sign_term = aie::sign(vin);",
            "auto mag_term = aie::abs(vin);",
            "result = aie::mul(sign_term, aie::min(mag_term, coeff));",
        ],
        "qam_demodulation": [
            "auto dist = aie::abs_square(aie::sub(vin, coeff));",
            "auto soft = aie::neg(dist);",
            "result = aie::add(soft, state);",
        ],
        "channel_estimation": [
            "auto pilot_mix = aie::mul(vin, aie::conj(coeff));",
            "acc = aie::add(pilot_mix, state);",
            "result = aie::shift_right(acc, 1);",
        ],
        "mimo_precoding": [
            "auto lane0 = aie::mul(vin, coeff);",
            "auto lane1 = aie::mul(state, coeff);",
            "result = aie::add(lane0, lane1);",
        ],
        "sample_rate_conversion": [
            "phase_acc = aie::add(phase_acc, phase_step);",
            "auto phase_sel = aie::shuffle(vin, state, 0x54107632);",
            "result = aie::mul(phase_sel, coeff);",
        ],
        "noise_shaping": [
            "auto quantized = aie::round(vin);",
            "auto error = aie::sub(vin, quantized);",
            "result = aie::add(quantized, aie::shift_left(error, 1));",
        ],
        "envelope_detection": [
            "auto power = aie::abs_square(vin);",
            "result = aie::sqrt(power);",
        ],
        "automatic_gain_control": [
            "auto power = aie::abs_square(vin);",
            "auto gain = aie::inv(aie::add(power, coeff));",
            "result = aie::mul(vin, gain);",
        ],
        "pulse_compression": [
            "auto chirp_mix = aie::mul(vin, aie::conj(coeff));",
            "acc = aie::mac(aie::zeros<" + spec.data_type.accum_type + ", " + str(width) + ">(), chirp_mix, state);",
            "result = aie::add(acc, chirp_mix);",
        ],
        "matched_filtering": [
            "acc = aie::mac(aie::zeros<" + spec.data_type.accum_type + ", " + str(width) + ">(), vin, coeff);",
            "result = aie::add(acc, state);",
        ],
        "doppler_processing": [
            "auto slow_time = aie::mul(vin, coeff);",
            "auto doppler_bin = aie::fft_dit_r2_stage(slow_time, state);",
            "result = doppler_bin;",
        ],
        "cfar_detection": [
            "auto noise_floor = aie::reduce_add(state);",
            "auto threshold = aie::add(noise_floor, coeff);",
            "result = aie::select(state, vin, aie::gt(vin, threshold));",
        ],
        "digital_downconversion": [
            "auto mixed = aie::mul(vin, coeff);",
            "acc = aie::mac(aie::zeros<" + spec.data_type.accum_type + ", " + str(width) + ">(), mixed, state);",
            "result = aie::shift_right(acc, 1);",
        ],
        "numerically_controlled_oscillator": [
            "phase_acc = aie::add(phase_acc, phase_step);",
            "auto osc = aie::sin(phase_acc);",
            "result = aie::mul(vin, osc);",
        ],
    }
    return state_lines, bodies[spec.compute_pattern]


def generate_kernel_code(spec: KernelSpec) -> tuple[str, dict]:
    class_name = f"{spec.compute_pattern}_{spec.data_type.name}_{spec.interface}_{spec.variant_index}"
    signature, setup_lines, read_lines, write_lines = build_kernel_signature(class_name, spec)
    state_lines, pattern_lines = build_pattern_body(spec)
    width = spec.data_type.vector_width
    dtype = spec.data_type.scalar_type
    extra_setup = [
        f"auto coeff = aie::load_v<{width}>(coeffs);",
        f"aie::vector<{dtype}, {width}> state = aie::load_v<{width}>(delay_line);",
        f"aie::accum<{spec.data_type.accum_type}, {width}> acc = aie::zeros<{spec.data_type.accum_type}, {width}>();",
        f"aie::accum<{spec.data_type.accum_type}, {width}> result = aie::zeros<{spec.data_type.accum_type}, {width}>();",
        f"aie::vector<{dtype}, {width}> phase_acc = aie::broadcast<{dtype}, {width}>({spec.data_type.zero_literal});",
        f"aie::vector<{dtype}, {width}> phase_step = aie::broadcast<{dtype}, {width}>({spec.data_type.coeff_literal});",
    ]
    loop_count = {"simple": 16, "moderate": 32, "complex": 64}[spec.complexity]
    code = f'''#include <adf.h>
#include <aie_api/aie.hpp>
#include <aie_api/aie_adf.hpp>

using namespace adf;

class {class_name} {{
public:
{join_lines(state_lines, 4)}
    {signature};
}};

{signature.replace("void run", f"void {class_name}::run")} {{
{join_lines(setup_lines + extra_setup, 4)}
    for (unsigned frame = 0; frame < {loop_count}; ++frame)
        chess_prepare_for_pipelining
        chess_loop_count({loop_count}, {loop_count})
    {{
{join_lines(read_lines, 8)}
{join_lines(pattern_lines, 8)}
{join_lines(write_lines, 8)}
    }}
}}
'''
    metadata = {
        "compute_pattern": spec.compute_pattern,
        "data_types": [spec.data_type.name],
        "interfaces": [spec.interface],
        "complexity": spec.complexity,
    }
    return code.strip() + "\n", metadata


def graph_external_decl(interface: str, io_width_bits: int, name: str, direction: str) -> str:
    if interface == "gmio":
        return f"    adf::GMIO {name} = adf::GMIO::create(\"{name}\", {io_width_bits}, 256);"
    return f"    adf::PLIO {name} = adf::PLIO::create(\"{name}\", adf::plio_{io_width_bits}_bits, \"data/{name}.txt\");"


def generate_graph_code(spec: GraphSpec) -> tuple[str, dict]:
    class_name = f"{spec.topology}_{spec.interface}_{spec.io_width_bits}_{spec.variant_index}_graph"
    kernel_count = {
        "single_kernel": 1,
        "linear_chain": 3,
        "broadcast_merge": 4,
        "feedback_loop": 3,
        "multirate_pipeline": 3,
        "shared_buffer_fanout": 3,
        "subgraph_composition": 4,
        "multi_input_multi_output": 4,
    }[spec.topology]
    kernel_decls = [f"    adf::kernel k{i};" for i in range(kernel_count)]
    io_kind = "gmio" if spec.interface == "gmio" else "plio"
    in_decl = graph_external_decl(io_kind, spec.io_width_bits, "in0", "input")
    out_decl = graph_external_decl(io_kind, spec.io_width_bits, "out0", "output")
    aux_decls = []
    if spec.interface == "plio_gmio":
        aux_decls.append(graph_external_decl("gmio", spec.io_width_bits, "in1", "input"))
        aux_decls.append(graph_external_decl("plio", spec.io_width_bits, "out1", "output"))
    parameter_decl = "    adf::parameter coeff_rtp;" if spec.parameter_mode in {"rtp", "mixed"} else ""
    constructor_lines = []
    for i in range(kernel_count):
        constructor_lines.append(f'        k{i} = adf::kernel::create(kernel_fn_{i});')
        constructor_lines.append(f'        adf::source(k{i}) = "generated/kernel_{spec.variant_index}_{i}.cpp";')
        constructor_lines.append(f'        adf::runtime<ratio>(k{i}) = {0.6 + 0.05 * i:.2f};')
    if spec.topology == "single_kernel":
        constructor_lines.extend(
            [
                "        adf::connect<>(in0.out[0], k0.in[0]);",
                "        adf::connect<>(k0.out[0], out0.in[0]);",
            ]
        )
    elif spec.topology == "linear_chain":
        constructor_lines.extend(
            [
                "        adf::connect<>(in0.out[0], k0.in[0]);",
                "        adf::connect<>(k0.out[0], k1.in[0]);",
                "        adf::connect<>(k1.out[0], k2.in[0]);",
                "        adf::connect<>(k2.out[0], out0.in[0]);",
            ]
        )
    elif spec.topology == "broadcast_merge":
        constructor_lines.extend(
            [
                "        adf::connect<>(in0.out[0], k0.in[0]);",
                "        adf::connect<>(in0.out[0], k1.in[0]);",
                "        adf::connect<>(k0.out[0], k2.in[0]);",
                "        adf::connect<>(k1.out[0], k2.in[1]);",
                "        adf::connect<>(k2.out[0], k3.in[0]);",
                "        adf::connect<>(k3.out[0], out0.in[0]);",
            ]
        )
    elif spec.topology == "feedback_loop":
        constructor_lines.extend(
            [
                "        adf::connect<>(in0.out[0], k0.in[0]);",
                "        adf::connect<>(k0.out[0], k1.in[0]);",
                "        adf::connect<>(k1.out[0], k2.in[0]);",
                "        adf::connect<>(k2.out[0], k1.in[1]);",
                "        adf::connect<>(k2.out[1], out0.in[0]);",
            ]
        )
    elif spec.topology == "multirate_pipeline":
        constructor_lines.extend(
            [
                "        adf::connect<>(in0.out[0], k0.in[0]);",
                "        adf::connect<>(k0.out[0], k1.in[0]);",
                "        adf::connect<>(k1.out[0], k2.in[0]);",
                "        adf::connect<>(k2.out[0], out0.in[0]);",
                "        adf::dimensions(k0.in[0]) = {256};",
                "        adf::dimensions(k1.in[0]) = {128};",
                "        adf::dimensions(k2.in[0]) = {64};",
            ]
        )
    elif spec.topology == "shared_buffer_fanout":
        constructor_lines.extend(
            [
                "        adf::connect<adf::window<512>>(in0.out[0], k0.in[0]);",
                "        adf::connect<adf::window<512>>(in0.out[0], k1.in[0]);",
                "        adf::connect<>(k0.out[0], k2.in[0]);",
                "        adf::connect<>(k1.out[0], k2.in[1]);",
                "        adf::connect<>(k2.out[0], out0.in[0]);",
            ]
        )
    elif spec.topology == "subgraph_composition":
        constructor_lines.extend(
            [
                "        adf::connect<>(in0.out[0], k0.in[0]);",
                "        adf::connect<>(k0.out[0], k1.in[0]);",
                "        adf::connect<>(k1.out[0], k2.in[0]);",
                "        adf::connect<>(k2.out[0], k3.in[0]);",
                "        adf::connect<>(k3.out[0], out0.in[0]);",
            ]
        )
    else:
        constructor_lines.extend(
            [
                "        adf::connect<>(in0.out[0], k0.in[0]);",
                "        adf::connect<>(in0.out[0], k1.in[0]);",
                "        adf::connect<>(k0.out[0], k2.in[0]);",
                "        adf::connect<>(k1.out[0], k3.in[0]);",
                "        adf::connect<>(k2.out[0], out0.in[0]);",
                "        adf::connect<>(k3.out[0], out0.in[1]);",
            ]
        )
    if spec.parameter_mode in {"rtp", "mixed"}:
        constructor_lines.append("        adf::connect<>(coeff_rtp, adf::async(k0.in[1]));")
    code = f'''#include <adf.h>

using namespace adf;

extern void kernel_fn_0(input_window<int32>* in, output_window<int32>* out);
extern void kernel_fn_1(input_window<int32>* in, output_window<int32>* out);
extern void kernel_fn_2(input_window<int32>* in, output_window<int32>* out);
extern void kernel_fn_3(input_window<int32>* in, output_window<int32>* out);

class {class_name} : public adf::graph {{
public:
{join_lines(kernel_decls, 0)}
{in_decl}
{out_decl}
{join_lines(aux_decls, 0)}
{parameter_decl}

    {class_name}() {{
{join_lines(constructor_lines, 8)}
    }}
}};
'''
    metadata = {
        "compute_pattern": spec.topology,
        "data_types": ["int32"],
        "interfaces": [spec.interface],
        "complexity": spec.complexity,
    }
    return code.strip() + "\n", metadata


def bug_explanation(bug_type: str, category: str) -> str:
    explanations = {
        "unconsumed_stream_deadlock": "One stream edge stops consuming or producing tokens, so the graph can hang when upstream and downstream rates no longer match.",
        "buffer_size_mismatch": "The graph or kernel window size no longer matches the implementation stride, which can cause truncated processing or out-of-bounds access.",
        "wrong_vector_lane_width": "The vector lane count no longer matches the data type and loop stride, so loads and arithmetic are misaligned.",
        "missing_chess_prepare_for_pipelining": "Removing the pipelining pragma can break the expected schedule and throughput assumptions for the tile.",
        "off_by_one_circular_buffer_index": "The circular buffer wrap condition now overruns the last legal lane before resetting.",
        "incorrect_lock_ordering": "The lock acquire and release order is inconsistent, which can deadlock or expose stale data.",
        "stream_tlast_missing": "The final packet loses its frame boundary marker, so packetized downstream logic never sees end-of-frame.",
        "rtp_read_inside_loop": "Reading RTP values inside the hot loop changes semantics and adds avoidable control overhead.",
        "mismatched_plio_width": "The graph PLIO width no longer matches the connected kernel data width, so packing and unpacking assumptions break.",
        "blocking_read_on_async_port": "The code treats an async port like a blocking stream, which can stall the graph unexpectedly.",
        "double_free_buffer_iterator": "The iterator or lock release happens twice, which is undefined and can corrupt the port state.",
        "uninitialized_accumulator": "The MAC chain starts from an undefined accumulator state, so outputs depend on stale register contents.",
        "incorrect_shuffle_mode": "The shuffle mask no longer matches the lane layout for this data type, so lanes are permuted incorrectly.",
        "output_buffer_overrun": "The loop writes one vector past the legal output extent, risking memory corruption.",
        "wrong_accumulator_mode": "The accumulator precision is too small for this operand type, so saturation or overflow can corrupt results.",
    }
    return explanations[bug_type] + (" This variant is a graph-level integration bug." if category == "buggy_graph" else " This variant is inside the kernel implementation.")


def inject_bug(code: str, category: str, bug_type: str) -> str:
    updated = code
    if bug_type == "unconsumed_stream_deadlock":
        if "writeincr" in updated:
            updated = re.sub(r"writeincr\([^\n]+\);", "// BUG: removed output token write to create a rate mismatch.", updated, count=1)
        else:
            updated = re.sub(r"adf::connect<[^\n]+;", "// BUG: removed a graph edge and introduced a token imbalance.", updated, count=1)
    elif bug_type == "buffer_size_mismatch":
        if "extents<" in updated:
            updated = re.sub(r"extents<(\d+)>", lambda m: f"extents<{int(m.group(1)) + 64}>", updated, count=1)
        else:
            updated = re.sub(r"adf::dimensions\(([^\)]+)\) = \{(\d+)\};", lambda m: f"adf::dimensions({m.group(1)}) = {{{int(m.group(2)) + 64}}};", updated, count=1)
    elif bug_type == "wrong_vector_lane_width":
        if "aie::vector<" in updated:
            updated = re.sub(r"aie::vector<([^,]+),\s*(\d+)>", lambda m: f"aie::vector<{m.group(1)}, {max(2, int(m.group(2)) // 2)}>", updated, count=1)
        else:
            updated = re.sub(r"readincr_v<(\d+)>", lambda m: f"readincr_v<{max(2, int(m.group(1)) // 2)}>", updated, count=1)
    elif bug_type == "missing_chess_prepare_for_pipelining":
        updated = updated.replace("        chess_prepare_for_pipelining\n", "", 1)
    elif bug_type == "off_by_one_circular_buffer_index":
        updated = updated.replace("for (unsigned frame = 0; frame <", "for (unsigned frame = 0; frame <=", 1)
    elif bug_type == "incorrect_lock_ordering":
        if "auto lock_in = acquire();" in updated:
            updated = updated.replace("auto lock_in = acquire();", "release(lock_in);\n    auto lock_in = acquire();", 1)
        else:
            updated = updated.replace("adf::connect<>(coeff_rtp, adf::async(k0.in[1]));", "adf::connect<>(adf::async(k0.in[1]), coeff_rtp);", 1)
    elif bug_type == "stream_tlast_missing":
        updated = updated.replace("writeincr(out, result.template to_vector", "writeincr(out, result.template to_vector", 1)
        updated += "\n// BUG: TLAST handling intentionally omitted on final packet.\n"
    elif bug_type == "rtp_read_inside_loop":
        updated = updated.replace("    {\n", "    {\n        auto dynamic_coeff = coeff;\n", 1)
    elif bug_type == "mismatched_plio_width":
        if "plio_128_bits" in updated:
            updated = updated.replace("plio_128_bits", "plio_32_bits", 1)
        elif "plio_64_bits" in updated:
            updated = updated.replace("plio_64_bits", "plio_32_bits", 1)
        elif "plio_32_bits" in updated:
            updated = updated.replace("plio_32_bits", "plio_128_bits", 1)
        elif "GMIO::create" in updated:
            updated = re.sub(r"GMIO::create\(([^,]+),\s*(\d+),", lambda m: f"GMIO::create({m.group(1)}, 32,", updated, count=1)
    elif bug_type == "blocking_read_on_async_port":
        updated = updated.replace("auto vin = *in_port++;", "auto vin = readincr_v<8>(&in);", 1)
    elif bug_type == "double_free_buffer_iterator":
        updated = updated.replace("release(lock_in);", "release(lock_in);\n        release(lock_in);", 1)
    elif bug_type == "uninitialized_accumulator":
        updated = re.sub(r"aie::accum<[^\n]+ = aie::zeros<[^\n]+;", "aie::accum<acc48, 8> acc;", updated, count=1)
    elif bug_type == "incorrect_shuffle_mode":
        updated = updated.replace("0x32107654", "0x76543210", 1)
    elif bug_type == "output_buffer_overrun":
        updated = updated.replace("++frame)", "++frame) // BUG: loop bound now overruns output budget", 1)
        updated = updated.replace("for (unsigned frame = 0; frame <", "for (unsigned frame = 0; frame <=", 1)
    elif bug_type == "wrong_accumulator_mode":
        updated = updated.replace("acc80", "acc48", 1).replace("cacc80", "cacc48", 1)
    if updated == code:
        if category == "graph":
            updated = updated.replace("    }\n};", "        adf::dimensions(k0.in[0]) = {1025};\n    }\n};", 1)
        else:
            updated = updated.replace("for (unsigned frame = 0; frame <", "for (unsigned frame = 0; frame <=", 1)
    return updated


def select_bug_types(index_seed: int, category: str, code: str) -> list[str]:
    lowered = code.lower()
    bug_pool: list[str] = []
    if category == "graph":
        if "connect<" in lowered:
            bug_pool.append("unconsumed_stream_deadlock")
        if "dimensions(" in lowered or "window<" in lowered:
            bug_pool.append("buffer_size_mismatch")
        if "plio_" in lowered or "gmio::create" in lowered:
            bug_pool.append("mismatched_plio_width")
        if "coeff_rtp" in lowered:
            bug_pool.append("rtp_read_inside_loop")
        if "async(" in lowered:
            bug_pool.append("incorrect_lock_ordering")
        bug_pool.extend([
            "buffer_size_mismatch",
            "mismatched_plio_width",
            "unconsumed_stream_deadlock",
        ])
    else:
        if "writeincr" in lowered or "connect<" in lowered:
            bug_pool.append("unconsumed_stream_deadlock")
        if "extents<" in lowered:
            bug_pool.append("buffer_size_mismatch")
        if "aie::vector<" in lowered or "readincr_v<" in lowered:
            bug_pool.append("wrong_vector_lane_width")
        if "chess_prepare_for_pipelining" in lowered:
            bug_pool.append("missing_chess_prepare_for_pipelining")
        if "acquire()" in lowered:
            bug_pool.append("incorrect_lock_ordering")
            bug_pool.append("double_free_buffer_iterator")
            bug_pool.append("blocking_read_on_async_port")
        if "aie::accum<" in lowered:
            bug_pool.append("uninitialized_accumulator")
            bug_pool.append("wrong_accumulator_mode")
        bug_pool.append("output_buffer_overrun")
        bug_pool.append("off_by_one_circular_buffer_index")
        bug_pool.append("incorrect_shuffle_mode")
    rng = random.Random(SYNTHETIC_SEED + index_seed)
    bug_pool = list(dict.fromkeys(bug_pool))
    rng.shuffle(bug_pool)
    return bug_pool


def make_kernel_filename(spec: KernelSpec, bug_type: str | None = None) -> str:
    base = f"{spec.compute_pattern}_{spec.data_type.name}_{spec.interface}_{spec.complexity}_{spec.variant_index}"
    if bug_type:
        return f"{base}_{bug_type}.cpp"
    return f"{base}.cpp"


def make_graph_filename(spec: GraphSpec, bug_type: str | None = None) -> str:
    base = f"{spec.topology}_{spec.interface}_{spec.io_width_bits}_{spec.parameter_mode}_{spec.complexity}_{spec.variant_index}"
    if bug_type:
        return f"{base}_{bug_type}.h"
    return f"{base}.h"


def synthesize_entries(
    scraped_entries: list[CorpusEntry],
    kernel_count: int,
    graph_count: int,
) -> list[CorpusEntry]:
    coverage = summarize_real_coverage(scraped_entries)
    synthetic_entries: list[CorpusEntry] = []
    seen_hashes = {make_content_hash(entry.code) for entry in scraped_entries}

    for spec in build_kernel_specs(kernel_count, coverage):
        code, metadata = generate_kernel_code(spec)
        content_hash = make_content_hash(code)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        synthetic_entries.append(
            CorpusEntry(
                filename=make_kernel_filename(spec),
                code=code,
                source="synthetic",
                category="kernel",
                bug_type=None,
                bug_explanation=None,
                metadata=metadata,
            )
        )
        selected_bug_count = 0
        for bug_type in select_bug_types(spec.variant_index, "kernel", code):
            bug_code = inject_bug(code, "kernel", bug_type)
            bug_hash = make_content_hash(bug_code)
            if bug_hash in seen_hashes:
                continue
            seen_hashes.add(bug_hash)
            synthetic_entries.append(
                CorpusEntry(
                    filename=make_kernel_filename(spec, bug_type),
                    code=bug_code,
                    source="synthetic",
                    category="buggy_kernel",
                    bug_type=bug_type,
                    bug_explanation=bug_explanation(bug_type, "buggy_kernel"),
                    metadata=metadata,
                )
            )
            selected_bug_count += 1
            if selected_bug_count == 3:
                break

    for spec in build_graph_specs(graph_count):
        code, metadata = generate_graph_code(spec)
        content_hash = make_content_hash(code)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        synthetic_entries.append(
            CorpusEntry(
                filename=make_graph_filename(spec),
                code=code,
                source="synthetic",
                category="graph",
                bug_type=None,
                bug_explanation=None,
                metadata=metadata,
            )
        )
        selected_bug_count = 0
        for bug_type in select_bug_types(spec.variant_index, "graph", code):
            bug_code = inject_bug(code, "graph", bug_type)
            bug_hash = make_content_hash(bug_code)
            if bug_hash in seen_hashes:
                continue
            seen_hashes.add(bug_hash)
            synthetic_entries.append(
                CorpusEntry(
                    filename=make_graph_filename(spec, bug_type),
                    code=bug_code,
                    source="synthetic",
                    category="buggy_graph",
                    bug_type=bug_type,
                    bug_explanation=bug_explanation(bug_type, "buggy_graph"),
                    metadata=metadata,
                )
            )
            selected_bug_count += 1
            if selected_bug_count == 3:
                break

    return synthetic_entries


def run_scrape(args: argparse.Namespace) -> list[CorpusEntry]:
    token = args.github_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    client = GitHubClient(token=token, cache_dir=args.cache_dir, delay_seconds=args.delay_seconds)
    seen_hashes: set[str] = set()
    entries = collect_seed_repo_entries(client, seen_hashes)
    discovered_repos: set[str] = set()
    if not args.skip_code_search:
        search_entries, discovered_repos = collect_search_entries(
            client,
            list(COMMUNITY_CODE_SEARCH_QUERIES) + list(DISCOVERY_QUERIES),
            seen_hashes,
        )
        entries.extend(search_entries)
        if args.expand_discovered_repos:
            entries.extend(collect_discovered_repo_entries(client, discovered_repos, seen_hashes))
    entries.sort(key=lambda entry: ((entry.repo or ""), (entry.branch or ""), entry.filename))
    write_jsonl(args.scrape_output, entries)
    return entries


def run_synthetic(args: argparse.Namespace, scraped_entries: list[CorpusEntry]) -> list[CorpusEntry]:
    entries = synthesize_entries(scraped_entries, args.synthetic_kernels, args.synthetic_graphs)
    write_jsonl(args.synthetic_output, entries)
    return entries


def combine_entries(args: argparse.Namespace, scraped_entries: list[CorpusEntry], synthetic_entries: list[CorpusEntry]) -> None:
    combined = scraped_entries + synthetic_entries
    write_jsonl(args.combined_output, combined)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape real AIE source files from GitHub and generate synthetic AIE kernels and graphs."
    )
    parser.add_argument("mode", choices=["scrape", "synthesize", "all"], nargs="?", default="all")
    parser.add_argument("--github-token", help="GitHub API token. Falls back to GITHUB_TOKEN or GH_TOKEN.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--delay-seconds", type=float, default=API_DELAY_SECONDS)
    parser.add_argument("--scrape-output", type=Path, default=DEFAULT_SCRAPE_OUTPUT)
    parser.add_argument("--synthetic-output", type=Path, default=DEFAULT_SYNTHETIC_OUTPUT)
    parser.add_argument("--combined-output", type=Path, default=DEFAULT_COMBINED_OUTPUT)
    parser.add_argument("--synthetic-kernels", type=int, default=100)
    parser.add_argument("--synthetic-graphs", type=int, default=40)
    parser.add_argument("--expand-discovered-repos", action="store_true", default=True)
    parser.add_argument("--no-expand-discovered-repos", dest="expand_discovered_repos", action="store_false")
    parser.add_argument("--skip-code-search", action="store_true")
    return parser


def load_jsonl(path: Path) -> list[CorpusEntry]:
    if not path.exists():
        return []
    entries = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            entries.append(
                CorpusEntry(
                    filename=row["filename"],
                    code=row["code"],
                    source=row["source"],
                    category=row["category"],
                    bug_type=row.get("bug_type"),
                    bug_explanation=row.get("bug_explanation"),
                    metadata=row.get("metadata", {}),
                    source_url=row.get("source_url"),
                    repo=row.get("repo"),
                    branch=row.get("branch"),
                )
            )
    return entries


def print_summary(scraped_entries: list[CorpusEntry], synthetic_entries: list[CorpusEntry]) -> None:
    counts = Counter(entry.category for entry in scraped_entries + synthetic_entries)
    print(f"Scraped entries: {len(scraped_entries)}")
    print(f"Synthetic entries: {len(synthetic_entries)}")
    print(f"Combined entries: {len(scraped_entries) + len(synthetic_entries)}")
    print("Category counts:")
    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)

    scraped_entries: list[CorpusEntry] = []
    synthetic_entries: list[CorpusEntry] = []

    if args.mode in {"scrape", "all"}:
        scraped_entries = run_scrape(args)
    else:
        scraped_entries = load_jsonl(args.scrape_output)

    if args.mode in {"synthesize", "all"}:
        synthetic_entries = run_synthetic(args, scraped_entries)

    if args.mode == "all":
        combine_entries(args, scraped_entries, synthetic_entries)

    print_summary(scraped_entries, synthetic_entries)


if __name__ == "__main__":
    main()