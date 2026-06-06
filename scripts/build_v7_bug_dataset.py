#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import posixpath
import random
import re
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import difflib


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from validate_aie_compile import compile_one, detect_toolchain, is_graph_file  # noqa: E402


CODE_EXTENSIONS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
GRAPH_HEADER_MARKERS = ("graph", "project", "subsystem", "system", "xgemm")
DEFAULT_CORPUS_ROOT = ROOT / "golden file generation"
DEFAULT_OUT_DIR = ROOT / "data" / "processed" / "v7"
ERROR_LOG_SEPARATOR = "\n\n--- Error Log ---\n"
DEFAULT_INSTRUCTION = "A Versal AIE build is failing with the error below. Return a unified diff that resolves it."
DEFAULT_AIE_PART = "xcvc1902-vsva2197-2MP-e-S"
DEFAULT_AIEML_PART = "xcve2802-vsvh1760-2MP-e-S"
DEFAULT_WSL_DISTRO = "Ubuntu-24.04"
DEFAULT_WSL_VALIDATE_SCRIPT = ROOT / "scripts" / "run_validate_wsl.sh"
DEFAULT_GENERATED_MUTATOR_DIR = ROOT / "bug famalies" / "generated" / "mutators"

INCLUDE_RE = re.compile(r"^\s*#\s*include\s*([<\"])([^>\"]+)([>\"])", re.MULTILINE)
KERNEL_SOURCE_RE = re.compile(r'\bsource\s*\([^)]*\)\s*=\s*"([^"]+\.(?:c|cc|cpp|cxx))"', re.IGNORECASE)
KERNEL_CREATE_RE = re.compile(r"\b(?:adf::)?kernel::create\s*\(\s*([A-Za-z_]\w*)")
CONNECT_RE = re.compile(r"\b(?:adf::)?connect\s*<")
ADF_TYPE_RE = re.compile(
    r"\b(input_window|output_window|input_buffer|output_buffer|input_stream|output_stream|"
    r"input_plio|output_plio|input_gmio|output_gmio)\b"
)
AIE_SYMBOL_RE = re.compile(
    r"\b(load_v|store_v|broadcast|shuffle_up|shuffle_down|sliding_mul|begin_vector|"
    r"begin_restrict_vector|readincr_v|writeincr_v|readincr|writeincr|mac|mul)\b"
)
RUNTIME_RE = re.compile(r"\b(?:adf::)?runtime\b")
MISSING_HEADER_RE = re.compile(
    r"(?:fatal error|error): '?([^\s':]+\.(?:h|hh|hpp|hxx))'?(?::|:?\s+file not found|:?\s+No such file or directory)",
    re.IGNORECASE,
)
SYSTEM_HEADER_PREFIXES = ("asm/", "bits/", "gnu/", "linux/", "sys/")
HOST_MARKERS = (
    "xrt/",
    "xclbin",
    "xrtdevicehandle",
    "cl::",
    "cl/cl.h",
    "experimental/xrt",
)
GRAPH_SEED_MARKERS = (
    "adf.h",
    "simulation::platform",
    "kernel::create",
    "connect<",
    "input_plio",
    "output_plio",
    "input_gmio",
    "output_gmio",
    "public graph",
    "public adf::graph",
)


@dataclass(frozen=True)
class CorpusProject:
    project_dir: Path
    rel_files: tuple[str, ...]
    file_text: dict[str, str]
    target: str
    file_type: str


@dataclass(frozen=True)
class MutationCandidate:
    file_path: str
    bug_type: str
    category: str
    start: int
    end: int
    replacement: str
    original: str
    description: str


@dataclass(frozen=True)
class GeneratedMutator:
    name: str
    path: Path
    module: Any
    bug_type: str


@dataclass(frozen=True)
class MutationOption:
    source: str
    mutator_name: str
    file_path: str
    bug_type: str
    category: str
    original: str
    replacement: str
    description: str
    raw_candidate: Any
    module: Any | None = None


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "Build a fresh v7 AIE bug-repair dataset from the golden corpus by injecting 1-4 "
            "random compile-time bugs, compiling the buggy project, capturing the real error log, "
            "and materializing unified-diff repair targets."
        )
    )
    ap.add_argument("--corpus-root", default=str(DEFAULT_CORPUS_ROOT))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--variants-per-project", type=int, default=1)
    ap.add_argument("--min-bugs", type=int, default=1)
    ap.add_argument("--max-bugs", type=int, default=4)
    ap.add_argument("--use-all-mutations", action="store_true", help="Use every available mutation candidate in a variant instead of sampling 1-4")
    ap.add_argument("--mutation-source", choices=["builtin", "generated", "all"], default="generated")
    ap.add_argument("--generated-mutator-dir", default=str(DEFAULT_GENERATED_MUTATOR_DIR))
    ap.add_argument("--validation-ratio", type=float, default=0.12)
    ap.add_argument("--max-attempts", type=int, default=25)
    ap.add_argument("--checkpoint-every", type=int, default=25)
    ap.add_argument("--progress-every", type=int, default=10)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--project-shards", type=int, default=1)
    ap.add_argument("--project-shard-index", type=int, default=0)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--project-list",
        default=None,
        help="Optional txt/jsonl list of project directory names to include before sharding.",
    )
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument(
        "--keep-baseline-dependency-failures",
        action="store_true",
        help="Keep projects whose baseline compile fails with a dependency-style error instead of skipping them.",
    )
    ap.add_argument("--target", choices=["auto", "AIE", "AIE-ML"], default="auto")
    ap.add_argument("--aie-part", default=DEFAULT_AIE_PART)
    ap.add_argument("--aieml-part", default=DEFAULT_AIEML_PART)
    ap.add_argument("--aietools", default=None)
    ap.add_argument("--vitis", default=None)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--keep-workdir", action="store_true")
    ap.add_argument("--validator-backend", choices=["wsl", "windows"], default="wsl")
    ap.add_argument("--skip-baseline-validation", action="store_true")
    ap.add_argument(
        "--baseline-max-dependency-retries",
        type=int,
        default=4,
        help="When baseline compile fails on missing headers, retry by auto-adding project-local stub headers up to this many rounds.",
    )
    ap.add_argument("--wsl-distro", default=DEFAULT_WSL_DISTRO)
    ap.add_argument("--wsl-validate-script", default=str(DEFAULT_WSL_VALIDATE_SCRIPT))
    return ap.parse_args()


def load_generated_mutators(mutator_dir: Path) -> tuple[list[GeneratedMutator], list[str]]:
    mutators: list[GeneratedMutator] = []
    failures: list[str] = []
    if not mutator_dir.exists():
        return mutators, [f"missing mutator dir: {mutator_dir}"]
    for path in sorted(mutator_dir.glob("*.py")):
        module_name = f"v7_generated_mutator_{path.stem}_{hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:8]}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("could not create module spec")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for required in ("BUG_FAMILY", "find_mutation_candidates", "apply_mutation"):
                if not hasattr(module, required):
                    raise AttributeError(f"missing {required}")
            bug_family = getattr(module, "BUG_FAMILY")
            bug_type = bug_family.get("bug_type") if isinstance(bug_family, dict) else None
            mutators.append(GeneratedMutator(path.stem, path, module, str(bug_type or path.stem)))
        except Exception as exc:  # noqa: BLE001 - keep generation moving while reporting bad modules.
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return mutators, failures


def load_project_name_filter(path: Path) -> set[str]:
    names: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("{"):
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                project = row.get("project")
                if project:
                    names.add(str(project))
            else:
                names.add(stripped)
    return names


def iter_corpus_projects(corpus_root: Path, forced_target: str) -> list[CorpusProject]:
    projects: list[CorpusProject] = []
    for child in sorted(corpus_root.iterdir()):
        if not child.is_dir():
            continue
        file_text: dict[str, str] = {}
        rel_files: list[str] = []
        for path in sorted(child.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS:
                continue
            rel = path.relative_to(child).as_posix()
            file_text[rel] = path.read_text(encoding="utf-8", errors="replace")
            rel_files.append(rel)
        if not file_text:
            continue
        target = forced_target if forced_target != "auto" else infer_target(child, file_text)
        seed_marked = format_marked_project(file_text)
        file_type = "graph" if is_graph_file(seed_marked, None) or any(is_graph_like_header(r) for r in rel_files) else "kernel"
        filtered = prune_compile_files(file_text, file_type)
        marked = format_marked_project(filtered)
        projects.append(
            CorpusProject(
                project_dir=child,
                rel_files=tuple(sorted(filtered)),
                file_text=filtered,
                target=target,
                file_type=file_type,
            )
        )
    return projects


def infer_target(project_dir: Path, file_text: dict[str, str]) -> str:
    haystack = "\n".join([project_dir.name.lower(), *[path.lower() for path in file_text], *[text[:2000].lower() for text in file_text.values()]])
    project_name = project_dir.name.lower()
    if (
        "xilinx_aiebaremetal" in project_name
        or "high-speed-viterbi" in project_name
        or "18-music-algorithm" in project_name
    ):
        return "AIE-ML"
    markers = (
        "aie-ml",
        "aieml",
        "aie_ml",
        "vek280",
        "vek285",
        "vek385",
        "radioml",
    )
    return "AIE-ML" if any(marker in haystack for marker in markers) else "AIE"


def is_graph_like_header(rel_path: str) -> bool:
    path = Path(rel_path.lower())
    return path.suffix in {".h", ".hh", ".hpp", ".hxx"} and any(marker in path.name for marker in GRAPH_HEADER_MARKERS)


def normalize_local_dependency(source_rel: str, dep_rel: str) -> str:
    src_parent = Path(source_rel).parent.as_posix()
    joined = f"{src_parent}/{dep_rel}" if src_parent not in {"", "."} else dep_rel
    normalized = posixpath.normpath(joined).replace("\\", "/")
    while normalized.startswith("../"):
        normalized = normalized[3:]
    return normalized.lstrip("./")


def parse_local_dependencies(source_rel: str, text: str) -> list[str]:
    deps: list[str] = []
    for delim_start, inc, _ in INCLUDE_RE.findall(text or ""):
        normalized = normalize_local_dependency(source_rel, inc.strip())
        if normalized:
            deps.append(normalized)
    for dep in KERNEL_SOURCE_RE.findall(text or ""):
        normalized = normalize_local_dependency(source_rel, dep.strip())
        if normalized:
            deps.append(normalized)
    return deps


def select_dependency_target(dep_rel: str, available: dict[str, str]) -> str | None:
    if dep_rel in available:
        return dep_rel
    dep_name = Path(dep_rel).name
    matches = [rel for rel in available if Path(rel).name == dep_name]
    if len(matches) == 1:
        return matches[0]
    return None


def is_host_side_file(rel_path: str, text: str) -> bool:
    rel_lower = rel_path.lower()
    text_lower = text.lower()
    if "host" in Path(rel_lower).name:
        return True
    return any(marker in text_lower for marker in HOST_MARKERS)


def is_graph_seed(rel_path: str, text: str) -> bool:
    rel_lower = rel_path.lower()
    if any(marker in Path(rel_lower).name for marker in GRAPH_HEADER_MARKERS):
        return True
    text_lower = text.lower()
    return any(marker in text_lower for marker in GRAPH_SEED_MARKERS)


def prune_compile_files(file_text: dict[str, str], file_type: str) -> dict[str, str]:
    if file_type != "graph":
        return dict(file_text)

    seeds = [
        rel for rel, text in file_text.items()
        if is_graph_seed(rel, text) and not is_host_side_file(rel, text)
    ]
    if not seeds:
        return dict(file_text)

    selected: set[str] = set()
    stack = list(seeds)
    while stack:
        rel = stack.pop()
        if rel in selected or rel not in file_text:
            continue
        selected.add(rel)
        for dep in parse_local_dependencies(rel, file_text[rel]):
            target = select_dependency_target(dep, file_text)
            if target and target not in selected:
                stack.append(target)

    if not selected:
        return dict(file_text)
    return {rel: file_text[rel] for rel in sorted(selected)}


def format_marked_project(file_text: dict[str, str]) -> str:
    chunks: list[str] = []
    for rel_path in sorted(file_text):
        chunks.append(f"// FILE: {rel_path}\n{file_text[rel_path].rstrip()}\n")
    return "\n".join(chunks).strip() + "\n"


def build_unified_diff(correct_files: dict[str, str], buggy_files: dict[str, str]) -> str:
    diff_chunks: list[str] = []
    for rel_path in sorted(correct_files):
        correct = correct_files[rel_path]
        buggy = buggy_files[rel_path]
        if correct == buggy:
            continue
        diff_chunks.extend(
            difflib.unified_diff(
                buggy.splitlines(),
                correct.splitlines(),
                fromfile=rel_path,
                tofile=rel_path,
                lineterm="",
            )
        )
    return "\n".join(diff_chunks).strip() + "\n"


def choose_split(project_key: str, seed: int, validation_ratio: float) -> str:
    digest = hashlib.sha256(f"{seed}:{project_key}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return "validation" if value < validation_ratio else "train"


def serialize_toolchain(tc: Any) -> dict[str, Any]:
    payload = asdict(tc)
    payload["aietools_root"] = str(payload["aietools_root"]) if payload.get("aietools_root") else None
    payload["vitis_root"] = str(payload["vitis_root"]) if payload.get("vitis_root") else None
    payload["xchesscc"] = str(payload["xchesscc"]) if payload.get("xchesscc") else None
    payload["aiecompiler"] = str(payload["aiecompiler"]) if payload.get("aiecompiler") else None
    payload["vpp"] = str(payload["vpp"]) if payload.get("vpp") else None
    payload["include_dirs"] = [str(path) for path in payload.get("include_dirs", [])]
    payload["chess_lib_dirs"] = {key: str(value) for key, value in payload.get("chess_lib_dirs", {}).items()}
    payload["aie_platforms"] = {key: str(value) for key, value in payload.get("aie_platforms", {}).items()}
    return payload


def to_wsl_path(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/")
    if len(text) >= 3 and text[1:3] == ":/":
        return f"/mnt/{text[0].lower()}/{text[3:]}"
    return text


def format_cpp_response(code: str) -> str:
    return f"```cpp\n{code.rstrip()}\n```"


def extract_missing_headers(error_log: str) -> list[str]:
    headers: list[str] = []
    seen: set[str] = set()
    for header in MISSING_HEADER_RE.findall(error_log or ""):
        normalized = header.replace("\\", "/").strip()
        if not normalized or normalized.startswith("/"):
            continue
        normalized = posixpath.normpath(normalized).replace("\\", "/")
        while normalized.startswith("../"):
            normalized = normalized[3:]
        if normalized in {"", "."} or normalized.startswith("/"):
            continue
        if normalized.startswith(SYSTEM_HEADER_PREFIXES):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        headers.append(normalized)
    return headers


def add_stub_headers(file_text: dict[str, str], headers: list[str]) -> int:
    added = 0
    for header in headers:
        rel = Path(header).as_posix().lstrip("/")
        if rel in file_text:
            continue
        guard = re.sub(r"\W+", "_", rel.upper())
        file_text[rel] = (
            f"#ifndef __AUTO_V7_BASELINE_STUB_{guard}__\n"
            f"#define __AUTO_V7_BASELINE_STUB_{guard}__\n"
            "// Auto-generated baseline dependency stub to keep compile validation moving.\n"
            "#endif\n"
        )
        added += 1
    return added


def compile_project_wsl(
    project_code: str,
    project: CorpusProject,
    timeout_s: int,
    workdir_root: Path,
    keep_workdir: bool,
    project_key: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    validate_dir = workdir_root / "wsl_jsonl"
    validate_dir.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:10]
    input_path = validate_dir / f"{project_key.replace(':', '_')}_{job_id}.jsonl"
    out_path = validate_dir / f"{project_key.replace(':', '_')}_{job_id}_results.jsonl"
    row = {
        "instruction": "Compile validate this generated AIE project.",
        "context": "",
        "response": format_cpp_response(project_code),
        "metadata": {
            "type": project.file_type,
            "source": project.project_dir.name,
            "dataset_version": "v7_candidate",
        },
    }
    input_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    validate_script = Path(args.wsl_validate_script)
    if not validate_script.is_absolute():
        validate_script = ROOT / validate_script
    cmd = [
        "wsl",
        "-d",
        args.wsl_distro,
        "--",
        "bash",
        to_wsl_path(validate_script),
        "--input",
        to_wsl_path(input_path),
        "--out",
        to_wsl_path(out_path),
        "--workers",
        "1",
        "--scope",
        "correct",
        "--target",
        project.target,
        "--timeout",
        str(timeout_s),
        "--limit",
        "1",
    ]
    if keep_workdir:
        cmd.append("--keep-workdir")

    try:
        process = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s + 240,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "compile_ok": False,
            "return_code": -9,
            "compiler": "wsl-validator",
            "error_class": "validation_timeout",
            "stderr_tail": f"WSL validation timeout after {timeout_s + 240}s\n{exc.stderr or ''}",
            "stdout_tail": exc.stdout or "",
        }

    results: list[dict[str, Any]] = []
    if out_path.exists():
        with out_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if results:
        return results[0]

    return {
        "compile_ok": False,
        "return_code": process.returncode,
        "compiler": "wsl-validator",
        "error_class": "no_compile_result",
        "stderr_tail": process.stderr[-4000:],
        "stdout_tail": process.stdout[-4000:],
    }


def compile_project(
    tc_payload: dict[str, Any] | None,
    project_code: str,
    project: CorpusProject,
    timeout_s: int,
    workdir_root: Path,
    keep_workdir: bool,
    project_key: str,
    part: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    if args.validator_backend == "wsl":
        return compile_project_wsl(
            project_code=project_code,
            project=project,
            timeout_s=timeout_s,
            workdir_root=workdir_root,
            keep_workdir=keep_workdir,
            project_key=project_key,
            args=args,
        )
    if tc_payload is None:
        raise RuntimeError("windows validator backend requires a detected toolchain")
    job = {
        "tc": tc_payload,
        "code": project_code,
        "file_type": project.file_type,
        "target": project.target,
        "part": part,
        "timeout_s": timeout_s,
        "workdir_root": str(workdir_root),
        "keep_workdir": keep_workdir,
        "input_path": project.project_dir.as_posix(),
        "row_index": 0,
        "scope": "buggy",
        "metadata_keys": {"group_id": project_key},
        "missing_dependency_mode": "stub",
    }
    return compile_one(job)


def extract_error_log(result: dict[str, Any]) -> str:
    stderr_tail = (result.get("stderr_tail") or "").strip()
    stdout_tail = (result.get("stdout_tail") or "").strip()
    return stderr_tail or stdout_tail or "unknown compile failure"


def scan_mutations(file_text: dict[str, str]) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    for rel_path, text in file_text.items():
        candidates.extend(scan_include_mutations(rel_path, text))
        candidates.extend(scan_kernel_create_mutations(rel_path, text))
        candidates.extend(scan_connect_mutations(rel_path, text))
        candidates.extend(scan_adf_type_mutations(rel_path, text))
        candidates.extend(scan_aie_symbol_mutations(rel_path, text))
        candidates.extend(scan_runtime_mutations(rel_path, text))
    return dedupe_candidates(candidates)


def builtin_option(candidate: MutationCandidate) -> MutationOption:
    return MutationOption(
        source="builtin",
        mutator_name=candidate.bug_type,
        file_path=candidate.file_path,
        bug_type=candidate.bug_type,
        category=candidate.category,
        original=candidate.original,
        replacement=candidate.replacement,
        description=candidate.description,
        raw_candidate=candidate,
    )


def generated_option(mutator: GeneratedMutator, candidate: dict[str, object]) -> MutationOption | None:
    file_path = candidate.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return None
    bug_type = candidate.get("bug_type") or mutator.bug_type
    category = candidate.get("category") or "generated_mutator"
    original = candidate.get("original", "")
    replacement = candidate.get("replacement", "")
    description = candidate.get("description", mutator.name)
    return MutationOption(
        source="generated",
        mutator_name=mutator.name,
        file_path=file_path,
        bug_type=str(bug_type),
        category=str(category),
        original=str(original),
        replacement=str(replacement),
        description=str(description),
        raw_candidate=candidate,
        module=mutator.module,
    )


def scan_mutation_options(
    file_text: dict[str, str],
    mutation_source: str,
    generated_mutators: list[GeneratedMutator],
    stats: dict[str, int],
) -> list[MutationOption]:
    options: list[MutationOption] = []
    if mutation_source in {"builtin", "all"}:
        options.extend(builtin_option(candidate) for candidate in scan_mutations(file_text))
    if mutation_source in {"generated", "all"}:
        for mutator in generated_mutators:
            try:
                candidates = mutator.module.find_mutation_candidates(file_text)
            except Exception:  # noqa: BLE001 - count and continue with other mutators.
                stats["generated_find_failures"] += 1
                continue
            if not isinstance(candidates, list):
                stats["generated_find_failures"] += 1
                continue
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                option = generated_option(mutator, candidate)
                if option is not None:
                    options.append(option)
    return dedupe_options(options)


def dedupe_options(options: list[MutationOption]) -> list[MutationOption]:
    seen: set[tuple[str, str, str, str, str]] = set()
    deduped: list[MutationOption] = []
    for option in options:
        key = (option.mutator_name, option.file_path, option.bug_type, option.original, option.replacement)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(option)
    return deduped


def scan_include_mutations(rel_path: str, text: str) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    for match in INCLUDE_RE.finditer(text):
        inner = match.group(2)
        if "missing" in inner:
            continue
        path = Path(inner)
        if path.suffix:
            replacement = inner.replace(path.suffix, f"_missing{path.suffix}")
        else:
            replacement = inner + "_missing"
        candidates.append(
            MutationCandidate(
                file_path=rel_path,
                bug_type="missing_required_include",
                category="include",
                start=match.start(2),
                end=match.end(2),
                replacement=replacement,
                original=inner,
                description=f"Mutate include '{inner}' to a missing header.",
            )
        )
    return candidates


def scan_kernel_create_mutations(rel_path: str, text: str) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    for match in KERNEL_CREATE_RE.finditer(text):
        symbol = match.group(1)
        if symbol.endswith("_missing"):
            continue
        candidates.append(
            MutationCandidate(
                file_path=rel_path,
                bug_type="unknown_kernel_create_symbol",
                category="graph_api",
                start=match.start(1),
                end=match.end(1),
                replacement=f"{symbol}_missing",
                original=symbol,
                description=f"Rename kernel::create callee '{symbol}' to a missing symbol.",
            )
        )
    return candidates


def scan_connect_mutations(rel_path: str, text: str) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    for match in CONNECT_RE.finditer(text):
        symbol = match.group(0)
        replacement = symbol.replace("connect", "conect", 1)
        candidates.append(
            MutationCandidate(
                file_path=rel_path,
                bug_type="misspelled_connect_api",
                category="graph_api",
                start=match.start(0),
                end=match.end(0),
                replacement=replacement,
                original=symbol,
                description="Misspell connect<> to force a graph API compile error.",
            )
        )
    return candidates


def scan_adf_type_mutations(rel_path: str, text: str) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    for match in ADF_TYPE_RE.finditer(text):
        symbol = match.group(1)
        candidates.append(
            MutationCandidate(
                file_path=rel_path,
                bug_type="misspelled_adf_type",
                category="adf_type",
                start=match.start(1),
                end=match.end(1),
                replacement=symbol + "x",
                original=symbol,
                description=f"Misspell ADF type '{symbol}'.",
            )
        )
    return candidates


def scan_aie_symbol_mutations(rel_path: str, text: str) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    for match in AIE_SYMBOL_RE.finditer(text):
        symbol = match.group(1)
        candidates.append(
            MutationCandidate(
                file_path=rel_path,
                bug_type="misspelled_aie_intrinsic",
                category="aie_api",
                start=match.start(1),
                end=match.end(1),
                replacement=symbol + "x",
                original=symbol,
                description=f"Misspell AIE API '{symbol}'.",
            )
        )
    return candidates


def scan_runtime_mutations(rel_path: str, text: str) -> list[MutationCandidate]:
    candidates: list[MutationCandidate] = []
    for match in RUNTIME_RE.finditer(text):
        symbol = match.group(0)
        candidates.append(
            MutationCandidate(
                file_path=rel_path,
                bug_type="misspelled_runtime_api",
                category="graph_api",
                start=match.start(0),
                end=match.end(0),
                replacement=symbol.replace("runtime", "runtim", 1),
                original=symbol,
                description="Misspell runtime constraint API.",
            )
        )
    return candidates


def dedupe_candidates(candidates: list[MutationCandidate]) -> list[MutationCandidate]:
    seen: set[tuple[str, int, int, str]] = set()
    deduped: list[MutationCandidate] = []
    for candidate in candidates:
        key = (candidate.file_path, candidate.start, candidate.end, candidate.replacement)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def choose_mutations(
    candidates: list[MutationCandidate],
    rng: random.Random,
    min_bugs: int,
    max_bugs: int,
    use_all_mutations: bool,
) -> list[MutationCandidate]:
    by_file: dict[str, list[MutationCandidate]] = {}
    for candidate in candidates:
        by_file.setdefault(candidate.file_path, []).append(candidate)
    if not by_file:
        return []
    if use_all_mutations:
        selected: list[MutationCandidate] = []
        for file_path in sorted(by_file):
            selected.append(rng.choice(by_file[file_path]))
        return selected
    file_paths = list(by_file)
    rng.shuffle(file_paths)
    desired = rng.randint(min_bugs, max_bugs)
    selected_files = file_paths[: min(desired, len(file_paths))]
    selected: list[MutationCandidate] = []
    for file_path in selected_files:
        selected.append(rng.choice(by_file[file_path]))
    return selected


def choose_mutation_options(
    options: list[MutationOption],
    rng: random.Random,
    min_bugs: int,
    max_bugs: int,
    use_all_mutations: bool,
) -> list[MutationOption]:
    by_file: dict[str, list[MutationOption]] = {}
    for option in options:
        by_file.setdefault(option.file_path, []).append(option)
    if not by_file:
        return []
    if use_all_mutations:
        selected: list[MutationOption] = []
        used_mutators: set[str] = set()
        for file_path in sorted(by_file):
            choices = by_file[file_path]
            rng.shuffle(choices)
            choice = next((item for item in choices if item.mutator_name not in used_mutators), choices[0])
            selected.append(choice)
            used_mutators.add(choice.mutator_name)
        return selected
    file_paths = list(by_file)
    rng.shuffle(file_paths)
    desired = rng.randint(min_bugs, max_bugs)
    selected_files = file_paths[: min(desired, len(file_paths))]
    selected: list[MutationOption] = []
    used_mutators: set[str] = set()
    for file_path in selected_files:
        choices = by_file[file_path]
        rng.shuffle(choices)
        choice = next((item for item in choices if item.mutator_name not in used_mutators), choices[0])
        selected.append(choice)
        used_mutators.add(choice.mutator_name)
    return selected


def mutation_signature(selected: list[MutationOption]) -> tuple[tuple[str, str, str, str], ...]:
    return tuple(sorted((item.mutator_name, item.file_path, item.original, item.replacement) for item in selected))


def apply_mutations(file_text: dict[str, str], selected: list[MutationCandidate]) -> dict[str, str]:
    mutated = dict(file_text)
    for candidate in selected:
        original_text = mutated[candidate.file_path]
        mutated[candidate.file_path] = (
            original_text[: candidate.start]
            + candidate.replacement
            + original_text[candidate.end :]
        )
    return mutated


def apply_mutation_options(file_text: dict[str, str], selected: list[MutationOption]) -> dict[str, str]:
    mutated = dict(file_text)
    for option in selected:
        before = dict(mutated)
        if option.source == "builtin":
            candidate = option.raw_candidate
            original_text = mutated[candidate.file_path]
            mutated[candidate.file_path] = (
                original_text[: candidate.start]
                + candidate.replacement
                + original_text[candidate.end :]
            )
        else:
            if option.module is None:
                raise RuntimeError(f"generated mutator missing module: {option.mutator_name}")
            mutated = option.module.apply_mutation(mutated, option.raw_candidate)
            if not isinstance(mutated, dict):
                raise TypeError(f"generated mutator did not return dict: {option.mutator_name}")
        if mutated == before:
            raise RuntimeError(f"mutation made no change: {option.mutator_name}")
    return mutated


def build_context(buggy_files: dict[str, str], changed_files: list[str], error_log: str) -> str:
    focused_files = {file_path: buggy_files[file_path] for file_path in changed_files if file_path in buggy_files}
    return f"Buggy files:\n{format_marked_project(focused_files)}{ERROR_LOG_SEPARATOR}{error_log.strip()}"


def make_row(
    project: CorpusProject,
    variant_index: int,
    buggy_files: dict[str, str],
    correct_files: dict[str, str],
    selected: list[MutationOption],
    error_log: str,
    seed: int,
    validation_ratio: float,
    baseline_validated: bool,
    baseline_error_class: str | None,
    compile_error_class: str | None,
) -> dict[str, Any]:
    bug_types = [candidate.bug_type for candidate in selected]
    categories = sorted({candidate.category for candidate in selected})
    changed_files = sorted({candidate.file_path for candidate in selected})
    response = build_unified_diff(correct_files, buggy_files)
    project_key = f"{project.project_dir.name}::v{variant_index}"
    split = choose_split(project_key, seed, validation_ratio)
    return {
        "instruction": DEFAULT_INSTRUCTION,
        "context": build_context(buggy_files, changed_files, error_log),
        "response": response,
        "metadata": {
            "split": split,
            "bug_type": bug_types[0] if len(bug_types) == 1 else "multi_bug_random_compile_mutation",
            "bug_types": bug_types,
            "bug_count": len(bug_types),
            "category": categories[0] if len(categories) == 1 else "multi_category_compile_mutation",
            "categories": categories,
            "response_format": "unified_diff",
            "has_real_error_log": True,
            "baseline_validated": baseline_validated,
            "baseline_error_class": baseline_error_class,
            "compile_error_class": compile_error_class,
            "dataset_version": "v7",
            "variant": "golden_corpus_random_mutation",
            "variant_index": variant_index,
            "group_id": project.project_dir.name,
            "source": project.project_dir.name,
            "corpus_source": "golden_file_generation",
            "changed_files": changed_files,
            "correct_files": sorted(correct_files),
            "target": project.target,
            "synthetic": False,
            "mutation_details": [
                {
                    "file_path": candidate.file_path,
                    "bug_type": candidate.bug_type,
                    "category": candidate.category,
                    "mutator": candidate.mutator_name,
                    "source": candidate.source,
                    "description": candidate.description,
                    "original": candidate.original,
                    "replacement": candidate.replacement,
                }
                for candidate in selected
            ],
        },
    }


def compile_mutated_variant(
    project: CorpusProject,
    variant_index: int,
    buggy_files: dict[str, str],
    correct_files: dict[str, str],
    selected: list[MutationOption],
    tc_payload: dict[str, Any] | None,
    timeout_s: int,
    workdir_root: Path,
    keep_workdir: bool,
    part: str,
    args: argparse.Namespace,
    baseline_validated: bool,
    baseline_error_class: str | None,
) -> dict[str, Any]:
    buggy_code = format_marked_project(buggy_files)
    result = compile_project(
        tc_payload=tc_payload,
        project_code=buggy_code,
        project=project,
        timeout_s=timeout_s,
        workdir_root=workdir_root,
        keep_workdir=keep_workdir,
        project_key=f"{project.project_dir.name}:{variant_index}",
        part=part,
        args=args,
    )
    if result.get("compile_ok"):
        return {"status": "compile_passed", "variant_index": variant_index}
    error_log = extract_error_log(result)
    row = make_row(
        project=project,
        variant_index=variant_index,
        buggy_files=buggy_files,
        correct_files=correct_files,
        selected=selected,
        error_log=error_log,
        seed=args.seed,
        validation_ratio=args.validation_ratio,
        baseline_validated=baseline_validated,
        baseline_error_class=baseline_error_class,
        compile_error_class=str(result.get("error_class") or "compile_error"),
    )
    return {
        "status": "row",
        "variant_index": variant_index,
        "row": row,
        "compile_error_class": str(result.get("error_class") or "compile_error"),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def completed_counts_by_project(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        group_id = metadata.get("group_id") or metadata.get("source")
        if not isinstance(group_id, str) or not group_id:
            continue
        counts[group_id] = counts.get(group_id, 0) + 1
    return counts


def build_summary(
    rows: list[dict[str, Any]],
    stats: dict[str, int],
    out_dir: Path,
) -> None:
    summary = {
        "total_rows": len(rows),
        "train_rows": sum(1 for row in rows if (row.get("metadata") or {}).get("split") == "train"),
        "validation_rows": sum(1 for row in rows if (row.get("metadata") or {}).get("split") == "validation"),
        "unique_bug_types": sorted({bug for row in rows for bug in (row.get("metadata") or {}).get("bug_types", [])}),
        "stats": stats,
    }
    summary_path = out_dir / "manifest_summary.json"
    tmp_path = summary_path.with_name(f".{summary_path.name}.tmp")
    tmp_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    tmp_path.replace(summary_path)


def main() -> int:
    args = parse_args()
    if args.min_bugs < 1 or args.max_bugs < args.min_bugs:
        raise SystemExit("invalid bug-count range")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.project_shards < 1:
        raise SystemExit("--project-shards must be >= 1")
    if args.project_shard_index < 0 or args.project_shard_index >= args.project_shards:
        raise SystemExit("--project-shard-index must be in [0, --project-shards)")

    corpus_root = Path(args.corpus_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir_root = Path(args.workdir) if args.workdir else Path(tempfile.gettempdir()) / "aie_v7_build"
    workdir_root.mkdir(parents=True, exist_ok=True)

    tc_payload = None
    if args.validator_backend == "windows":
        tc = detect_toolchain(args.aietools, args.vitis)
        tc_payload = serialize_toolchain(tc)
    generated_mutators: list[GeneratedMutator] = []
    generated_import_failures: list[str] = []
    if args.mutation_source in {"generated", "all"}:
        generated_mutators, generated_import_failures = load_generated_mutators(Path(args.generated_mutator_dir))
        if not generated_mutators:
            raise SystemExit("no generated mutators loaded")
    projects = iter_corpus_projects(corpus_root, args.target)
    if args.project_list:
        project_names = load_project_name_filter(Path(args.project_list))
        projects = [project for project in projects if project.project_dir.name in project_names]
    if args.project_shards > 1:
        projects = [
            project
            for index, project in enumerate(projects)
            if index % args.project_shards == args.project_shard_index
        ]
    if args.limit is not None:
        projects = projects[: args.limit]

    all_path = out_dir / "aie_instruction_v7_all.jsonl"
    train_path = out_dir / "aie_instruction_v7_train.jsonl"
    validation_path = out_dir / "aie_instruction_v7_validation.jsonl"
    rows: list[dict[str, Any]] = [] if args.no_resume else read_jsonl(all_path)
    completed_by_project = completed_counts_by_project(rows)
    stats = {
        "resumed_rows": len(rows),
        "workers": args.workers,
        "project_shards": args.project_shards,
        "project_shard_index": args.project_shard_index,
        "projects_seen": 0,
        "projects_baseline_failed": 0,
        "projects_baseline_skipped": 0,
        "projects_without_mutations": 0,
        "mutation_attempts": 0,
        "mutation_compile_passed": 0,
        "mutation_apply_failed": 0,
        "duplicate_mutation_variants": 0,
        "generated_mutators_loaded": len(generated_mutators),
        "generated_import_failures": len(generated_import_failures),
        "generated_find_failures": 0,
        "baseline_dependency_retries": 0,
        "baseline_stub_headers_added": 0,
        "rows_written": len(rows),
    }
    if rows:
        print(f"[v7] resumed rows : {len(rows)}", flush=True)

    for project in projects:
        stats["projects_seen"] += 1
        if args.progress_every > 0 and stats["projects_seen"] % args.progress_every == 1:
            print(
                f"[v7] progress projects={stats['projects_seen']}/{len(projects)} rows={stats['rows_written']}",
                flush=True,
            )
        part = args.aie_part if project.target == "AIE" else args.aieml_part
        project_key = project.project_dir.name
        baseline_files = dict(project.file_text)
        correct_code = format_marked_project(baseline_files)
        baseline_validated = False
        baseline_error_class: str | None = None
        if args.skip_baseline_validation:
            stats["projects_baseline_skipped"] += 1
        else:
            seen_missing_headers: set[str] = set()
            baseline = None
            for retry_index in range(max(0, args.baseline_max_dependency_retries) + 1):
                baseline = compile_project(
                    tc_payload=tc_payload,
                    project_code=correct_code,
                    project=project,
                    timeout_s=args.timeout,
                    workdir_root=workdir_root,
                    keep_workdir=args.keep_workdir,
                    project_key=project_key,
                    part=part,
                    args=args,
                )
                if baseline.get("compile_ok"):
                    break

                baseline_error_class = str(baseline.get("error_class") or "compile_error")
                if baseline_error_class not in {"missing_dependency", "missing_dependency_after_stub"}:
                    break
                if retry_index >= args.baseline_max_dependency_retries:
                    break

                missing_headers = [
                    header
                    for header in extract_missing_headers(extract_error_log(baseline))
                    if header not in seen_missing_headers
                ]
                if not missing_headers:
                    break

                added_count = add_stub_headers(baseline_files, missing_headers)
                if added_count <= 0:
                    break
                seen_missing_headers.update(missing_headers)
                stats["baseline_dependency_retries"] += 1
                stats["baseline_stub_headers_added"] += added_count
                correct_code = format_marked_project(baseline_files)

            if baseline is None:
                baseline = {"compile_ok": False, "error_class": "compile_error"}

            if not baseline.get("compile_ok"):
                baseline_error_class = str(baseline.get("error_class") or "compile_error")
                stats["projects_baseline_failed"] += 1
                if not (
                    args.keep_baseline_dependency_failures
                    and baseline_error_class in {"missing_dependency", "missing_dependency_after_stub"}
                ):
                    continue
            baseline_validated = True

        options = scan_mutation_options(baseline_files, args.mutation_source, generated_mutators, stats)
        if not options:
            stats["projects_without_mutations"] += 1
            continue

        start_variant = min(completed_by_project.get(project_key, 0), args.variants_per_project)
        if start_variant >= args.variants_per_project:
            continue
        seen_variants: set[tuple[tuple[str, str, str, str], ...]] = set()
        variant_jobs: list[tuple[int, dict[str, str], list[MutationOption]]] = []
        for variant_index in range(start_variant, args.variants_per_project):
            variant_rng = random.Random(f"{args.seed}:{project_key}:{variant_index}")
            for _ in range(args.max_attempts):
                stats["mutation_attempts"] += 1
                selected = choose_mutation_options(options, variant_rng, args.min_bugs, args.max_bugs, args.use_all_mutations)
                if not selected:
                    break
                signature = mutation_signature(selected)
                if signature in seen_variants:
                    stats["duplicate_mutation_variants"] += 1
                    continue
                try:
                    buggy_files = apply_mutation_options(baseline_files, selected)
                except Exception:  # noqa: BLE001 - generated mutator failed this candidate; try another.
                    stats["mutation_apply_failed"] += 1
                    continue
                if buggy_files == baseline_files:
                    continue
                seen_variants.add(signature)
                variant_jobs.append((variant_index, buggy_files, selected))
                break

        def handle_variant_result(payload: dict[str, Any]) -> None:
            if payload.get("status") == "compile_passed":
                stats["mutation_compile_passed"] += 1
                return
            if payload.get("status") != "row":
                stats["mutation_apply_failed"] += 1
                return
            rows.append(payload["row"])
            stats["rows_written"] += 1
            if args.progress_every > 0 and stats["rows_written"] % args.progress_every == 0:
                print(
                    f"[v7] progress projects={stats['projects_seen']}/{len(projects)} rows={stats['rows_written']}",
                    flush=True,
                )
            if args.checkpoint_every > 0 and stats["rows_written"] % args.checkpoint_every == 0:
                write_jsonl(all_path, rows)
                write_jsonl(train_path, [item for item in rows if (item.get("metadata") or {}).get("split") == "train"])
                write_jsonl(validation_path, [item for item in rows if (item.get("metadata") or {}).get("split") == "validation"])
                build_summary(rows, stats, out_dir)

        if args.workers == 1 or len(variant_jobs) <= 1:
            for variant_index, buggy_files, selected in variant_jobs:
                payload = compile_mutated_variant(
                    project=project,
                    variant_index=variant_index,
                    buggy_files=buggy_files,
                    correct_files=baseline_files,
                    selected=selected,
                    tc_payload=tc_payload,
                    timeout_s=args.timeout,
                    workdir_root=workdir_root,
                    keep_workdir=args.keep_workdir,
                    part=part,
                    args=args,
                    baseline_validated=baseline_validated,
                    baseline_error_class=baseline_error_class,
                )
                handle_variant_result(payload)
        else:
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [
                    executor.submit(
                        compile_mutated_variant,
                        project,
                        variant_index,
                        buggy_files,
                        baseline_files,
                        selected,
                        tc_payload,
                        args.timeout,
                        workdir_root,
                        args.keep_workdir,
                        part,
                        args,
                        baseline_validated,
                        baseline_error_class,
                    )
                    for variant_index, buggy_files, selected in variant_jobs
                ]
                for future in as_completed(futures):
                    try:
                        handle_variant_result(future.result())
                    except Exception:  # noqa: BLE001 - keep the long corpus run moving.
                        stats["mutation_apply_failed"] += 1

    write_jsonl(all_path, rows)
    write_jsonl(train_path, [row for row in rows if (row.get("metadata") or {}).get("split") == "train"])
    write_jsonl(validation_path, [row for row in rows if (row.get("metadata") or {}).get("split") == "validation"])
    build_summary(rows, stats, out_dir)

    print(f"[v7] projects seen: {stats['projects_seen']}")
    print(f"[v7] resumed rows : {stats['resumed_rows']}")
    print(f"[v7] workers      : {stats['workers']}")
    print(f"[v7] shard        : {stats['project_shard_index'] + 1}/{stats['project_shards']}")
    print(f"[v7] rows written : {stats['rows_written']}")
    print(f"[v7] baseline skip: {stats['projects_baseline_failed']}")
    print(f"[v7] baseline off : {stats['projects_baseline_skipped']}")
    print(f"[v7] no mutations  : {stats['projects_without_mutations']}")
    print(f"[v7] retry misses  : {stats['mutation_compile_passed']}")
    print(f"[v7] apply misses  : {stats['mutation_apply_failed']}")
    print(f"[v7] duplicate vars: {stats['duplicate_mutation_variants']}")
    print(f"[v7] gen mutators  : {stats['generated_mutators_loaded']}")
    print(f"[v7] gen import err: {stats['generated_import_failures']}")
    print(f"[v7] gen find err  : {stats['generated_find_failures']}")
    print(f"[v7] base retries  : {stats['baseline_dependency_retries']}")
    print(f"[v7] base stubs add: {stats['baseline_stub_headers_added']}")
    print(f"[v7] wrote         : {all_path}")
    if generated_import_failures:
        print("[v7] generated mutator import failures:")
        for failure in generated_import_failures[:20]:
            print(f"[v7]   {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
