import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "aie_dataset" / "external"
USER_AGENT = "GitHub-Copilot-AIE-Dataset-Collector"
SOURCE_SUFFIXES = {".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"}
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

TARGETS = [
    {"owner": "Xilinx", "repo": "Vitis_Libraries", "ref": None, "prefixes": ["dsp/L1/src/aie/", "dsp/L1/include/aie/", "vision/L1/include/aie/", "vision/L1/src/aie/"]},
    {"owner": "Xilinx", "repo": "mlir-aie", "ref": None, "prefixes": ["programming_examples/", "test/"]},
    {"owner": "Xilinx", "repo": "xup_aie_training", "ref": None, "prefixes": ["sources/", "pbl/aie_single_kernel/", "pbl/aie_multi_kernel/"]},
    {"owner": "Xilinx", "repo": "Vitis_Model_Composer", "ref": None, "prefixes": ["Tutorials/AIEngine_Library/", "Tutorials/AIE-PL/", "Examples/AIENGINE/"]},
    {"owner": "Xilinx", "repo": "Vitis-AI", "ref": None, "prefixes": ["src/vai_runtime/"]},
    {"owner": "Xilinx", "repo": "Vitis-AI-Tutorials", "ref": None, "prefixes": ["Design_Tutorials/"]},
    {"owner": "Xilinx", "repo": "triSYCL", "ref": None, "prefixes": ["include/triSYCL/", "tests/"]},
    {"owner": "Xilinx", "repo": "llvm-aie", "ref": None, "prefixes": ["llvm/lib/Target/AIE/", "clang/lib/"]},
    {"owner": "arc-research-lab", "repo": "Aries", "ref": None, "prefixes": ["example_new/", "templates/"]},
    {"owner": "arc-research-lab", "repo": "AIM", "ref": None, "prefixes": ["application/", "template/"]},
    {"owner": "arc-research-lab", "repo": "SSR", "ref": None, "prefixes": ["SSR_Designs_Experiments/"]},
    {"owner": "JinmingZhuang", "repo": "CHARM", "ref": None, "prefixes": ["aie_src/", "pl_src/", "gen_scripts/"]},
    {"owner": "enyac-group", "repo": "MaxEVA", "ref": None, "prefixes": ["Pattern1_fp32/src/", "Pattern1_int8/src/", "Pattern2_fp32/src/", "Pattern2_int8/src/"]},
    {"owner": "advent-lab", "repo": "GAMA", "ref": None, "prefixes": ["aie/"]},
    {"owner": "hanchenye", "repo": "polyaie", "ref": None, "prefixes": ["polyaie/", "samples/"]},
    {"owner": "rehohoho", "repo": "onnx2versal", "ref": None, "prefixes": ["design/aie_src/"]},
    {"owner": "Paolo309", "repo": "XOHW-23-Versal-Registration", "ref": None, "prefixes": ["aie/"]},
    {"owner": "esa-tu-darmstadt", "repo": "graphtoy", "ref": None, "prefixes": [""]},
    {"owner": "nod-ai", "repo": "iree-amd-aie", "ref": None, "prefixes": ["compiler/", "runtime/", "tests/"]},
    {"owner": "donghoang93", "repo": "Xilinx-Vitis-Tutorials", "ref": None, "prefixes": ["AI_Engine_Development/", "Developer_Contributed/"]},
    {"owner": "pjh177787", "repo": "my_mlir-aie", "ref": None, "prefixes": ["programming_examples/", "test/"]},
    {"owner": "triSYCL", "repo": "sycl", "ref": None, "prefixes": [""]},
]

AIE_PATH_HINTS = ["/aie/", "aie_", "_aie", "adf", "graph", "kernel", "versal", "gmio", "plio", "cascade", "fft", "fir", "beamform", "channelizer", "softdemod", "matmul", "gemm"]
AIE_TEXT_HINTS = ["#include <adf.h>", "#include <aie_api/aie.hpp>", "adf::graph", "kernel::create", "input_buffer", "output_buffer", "input_window", "output_window", "input_stream", "output_stream", "input_plio", "output_plio", "input_gmio", "output_gmio", "aie::vector", "aie::accum", "connect<", "runtime<ratio>", "chess_prepare_for_pipelining"]


def build_headers(extra: dict | None = None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    if extra:
        headers.update(extra)
    return headers


def http_get_json(url: str):
    request = urllib.request.Request(url, headers=build_headers())
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def http_get_text(url: str) -> str:
    request = urllib.request.Request(url, headers=build_headers({"Accept": "text/plain"}))
    with urllib.request.urlopen(request) as response:
        return response.read().decode("utf-8", errors="replace")


def get_default_branch(owner: str, repo: str) -> str:
    payload = http_get_json(f"https://api.github.com/repos/{owner}/{repo}")
    return payload["default_branch"]


def get_tree(owner: str, repo: str, ref: str):
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(ref, safe='')}?recursive=1"
    return http_get_json(url)


def path_matches_prefixes(path: str, prefixes: list[str]) -> bool:
    return any(not prefix or path.startswith(prefix) for prefix in prefixes)


def path_looks_relevant(path: str) -> bool:
    lowered = path.lower()
    return any(hint in lowered for hint in AIE_PATH_HINTS)


def text_looks_relevant(text: str) -> bool:
    lowered = text.lower()
    return any(hint.lower() in lowered for hint in AIE_TEXT_HINTS)


def infer_domain(path: str) -> str:
    lowered = path.lower()
    if "debug" in lowered:
        return "Debug / AIE"
    if "beamform" in lowered:
        return "DSP — Beamforming"
    if "fir" in lowered or "filter" in lowered:
        return "DSP — FIR / Filtering"
    if "fft" in lowered or "channelizer" in lowered:
        return "DSP — FFT / Channelizer"
    if "gemm" in lowered or "blas" in lowered or "matmul" in lowered:
        return "Matrix Operations / Linear Algebra"
    if "packet" in lowered:
        return "Packet Switching / Dataflow"
    if "gmio" in lowered:
        return "GMIO / Data Movement"
    if "rtp" in lowered:
        return "Runtime Parameter Reconfiguration"
    if "n-body" in lowered:
        return "Simulation / N-Body"
    if "softdemod" in lowered:
        return "DSP — Soft Demodulation"
    if "vision" in lowered or "resize" in lowered or "filter2d" in lowered:
        return "Vision / Imaging"
    if "registration" in lowered or "phylogenetic" in lowered:
        return "Scientific Computing"
    return "AIE Source"


def infer_interface(text: str) -> str:
    lowered = text.lower()
    parts = []
    if any(token in lowered for token in ["input_buffer", "output_buffer", "input_window", "output_window", "connect<window"]):
        parts.append("Window")
    if any(token in lowered for token in ["input_stream", "output_stream", "connect<stream>"]):
        parts.append("Stream")
    if "connect<cascade>" in lowered or "input_cascade" in lowered or "output_cascade" in lowered:
        parts.append("Cascade")
    if any(token in lowered for token in ["input_plio", "output_plio", "plio_", "input_gmio", "output_gmio"]):
        parts.append("PLIO/GMIO")
    return ", ".join(parts) if parts else "Unknown"


def infer_key_intrinsics(text: str) -> str:
    matches = []
    patterns = [r"aie::[A-Za-z_][A-Za-z0-9_]*", r"\breadincr(?:_v\d+)?\b", r"\bwriteincr(?:_v\d+)?\b", r"\bwindow_readincr(?:_v<\d+>)?\b", r"\bwindow_writeincr\b", r"\bmul4\b", r"\bmac4\b", r"\bupd_v\b", r"\bsrs\b", r"\bconnect(?:<[^>]+>)?\b", r"\bdimensions\b", r"\bruntime\b", r"\blocation\b", r"\bbank\b", r"\btile\b", r"\bwindow_acquire\b", r"\bwindow_release\b"]
    for pattern in patterns:
        matches.extend(re.findall(pattern, text))
    deduped = []
    seen = set()
    for item in matches:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return ", ".join(deduped[:12]) if deduped else "Unknown"


def infer_vector_types(text: str) -> str:
    matches = []
    matches.extend(re.findall(r"aie::vector<\s*[^>]+>", text))
    matches.extend(re.findall(r"aie::accum<\s*[^>]+>", text))
    matches.extend(re.findall(r"\bv\d+[A-Za-z0-9_]+\b", text))
    deduped = []
    seen = set()
    for item in matches:
        normalized = re.sub(r"\s+", "", item)
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return ", ".join(deduped[:10]) if deduped else "Unknown"


def build_header(owner: str, repo: str, ref: str, path: str, text: str) -> str:
    return (
        "/*\n"
        f"SOURCE: {owner}/{repo}, branch {ref}\n"
        f"PATH: {path}\n"
        f"DOMAIN: {infer_domain(path)}\n"
        f"INTERFACE: {infer_interface(text)}\n"
        f"KEY INTRINSICS: {infer_key_intrinsics(text)}\n"
        f"VECTOR TYPES: {infer_vector_types(text)}\n"
        "*/\n\n"
    )


def write_file(owner: str, repo: str, ref: str, repo_path: str, text: str) -> Path:
    local_path = OUTPUT_ROOT / repo / repo_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text(build_header(owner, repo, ref, repo_path, text) + text, encoding="utf-8", newline="\n")
    return local_path


def download_raw(owner: str, repo: str, ref: str, repo_path: str) -> str:
    quoted_path = "/".join(urllib.parse.quote(part) for part in repo_path.split("/"))
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{quoted_path}"
    return http_get_text(url)


def collect_target(owner: str, repo: str, ref: str | None, prefixes: list[str], downloaded: list[Path]) -> None:
    try:
        resolved_ref = ref or get_default_branch(owner, repo)
        payload = get_tree(owner, repo, resolved_ref)
    except urllib.error.HTTPError as exc:
        print(f"SKIP tree {owner}/{repo}@{ref or 'default'} ({exc.code})")
        return

    for item in payload.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item.get("path", "")
        if Path(path).suffix.lower() not in SOURCE_SUFFIXES:
            continue
        if not path_matches_prefixes(path, prefixes):
            continue
        if not path_looks_relevant(path):
            continue
        try:
            text = download_raw(owner, repo, resolved_ref, path)
        except urllib.error.HTTPError as exc:
            print(f"SKIP raw {owner}/{repo}:{path} ({exc.code})")
            continue
        if not text_looks_relevant(text):
            continue
        local_path = write_file(owner, repo, resolved_ref, path, text)
        downloaded.append(local_path)
        print(f"FETCHED {owner}/{repo}:{path}")
        time.sleep(0.03)


def main() -> None:
    if GITHUB_TOKEN:
        print("Using GitHub token authentication.")
    else:
        print("No GITHUB_TOKEN or GH_TOKEN set. Public rate limits may truncate results.")

    downloaded: list[Path] = []
    for target in TARGETS:
        collect_target(target["owner"], target["repo"], target["ref"], target["prefixes"], downloaded)

    unique_files = sorted({str(path.relative_to(ROOT)) for path in downloaded})
    print(f"Downloaded {len(unique_files)} source files.")
    for path in unique_files:
        print(path)


if __name__ == "__main__":
    main()