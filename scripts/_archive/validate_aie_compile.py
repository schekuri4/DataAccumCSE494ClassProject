#!/usr/bin/env python3
"""
validate_aie_compile.py
=======================

Compile every code sample in an AIE instruction-tuning JSONL and log per-row
results (compile_ok, stderr, duration). Designed to plug into the V2 dataset:

  data/processed/split up/aie_instruction_v2_all_part_*.jsonl

Each row is shaped (built by scripts/build_aie_instruction_dataset.py):

  {"instruction": "...",
   "context":     "Buggy version:\\n...\\n\\nCorrect version:\\n...",
   "response":    "```cpp\\n<final code>\\n```",
   "metadata":    {"type": "kernel_type"|"graph_type", "source": "...", ...}}

Strategy
--------
* For each row we extract one or more code samples to compile (controlled by
  `--scope`):
    - "correct"  -> the code inside the ```cpp fence in `response`
    - "buggy"    -> the buggy code parsed out of `context`
    - "both"
* We classify each sample as a *graph* file (uses `adf.h` / `adf::graph`) or a
  *kernel* file (everything else).
* Kernels are compiled with `xchesscc -p me` plus the target-specific `-P` lib.
* Graphs are compiled with `aiecompiler --target=x86sim --platform=<xpfm>` so
    we avoid hardware place-and-route while still getting real ADF front-end checks.
* Rows are dispatched across `--workers` processes; each process gets its own
  scratch directory under `--workdir` to avoid cross-talk.
* Output: one JSONL line per (row_index, scope) pair into `--out`. Resumable
  via `--resume` (skips rows whose (input_path, row_index, scope) already
  appears in the output file).

Tool detection
--------------
Tries (in order):
  1. --aietools-bin / --vitis (CLI overrides)
  2. AIETOOLS env var (root with bin/, include/, ...)
  3. XILINX_VITIS env var pointing to a Vitis dir, then look for sibling aietools
    4. Common Windows installs: C:\\AMDDesignTools\\<version>, D:\\fpga\\<version>,
         C:\\Xilinx\\<version> (and Vitis)
  5. Common Linux paths (/opt/Xilinx/... ) for completeness

If neither xchesscc nor aiecompiler is found, prints clear instructions and
exits with code 2 (no rows attempted).

Usage
-----
  python scripts/validate_aie_compile.py \
      --input "data/processed/split up/aie_instruction_v2_all_part_0001.jsonl" \
      --out   "data/processed/compile_results/part_0001.jsonl" \
      --workers 8 --scope correct --limit 50

    # Convenience path for the current V2 all artifact, plus row split outputs:
    python scripts/validate_aie_compile.py --v2-all --workers 8 --split-rows

Repeat per chunk; results are append-only & resumable.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, as_completed, wait
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable, Iterator


DEFAULT_V2_ARCHIVE_GLOB = "data/processed/archive_v2_and_synthpairs_*/aie_instruction_v2_all.jsonl"
DEFAULT_V2_RESULTS = Path("data/processed/compile_results/all_v2.jsonl")
VISIBLE_CPUS = max(1, os.cpu_count() or 1)
# xchesscc/aiecompiler are heavy native tools; on this WSL setup 12 concurrent
# launches can terminate the Python parent before any result is written.  Eight
# keeps the machine busy while avoiding that startup cliff.  Users can still
# pass an explicit higher --workers value to experiment.
DEFAULT_WORKERS = min(VISIBLE_CPUS, 8)


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------


WINDOWS = platform.system().lower().startswith("win")
EXE = ".exe" if WINDOWS else ""
BAT = ".bat" if WINDOWS else ""


@dataclass
class Toolchain:
    aietools_root: Path
    vitis_root: Path | None
    xchesscc: Path | None
    aiecompiler: Path | None
    vpp: Path | None
    include_dirs: list[Path]
    # xchesscc -P library dirs: keyed by target ("AIE" / "AIE-ML")
    chess_lib_dirs: dict[str, Path] = None  # type: ignore[assignment]
    # Platform .xpfm paths for aiecompiler --platform: keyed by target
    aie_platforms: dict[str, Path] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.chess_lib_dirs is None:
            self.chess_lib_dirs = {}
        if self.aie_platforms is None:
            self.aie_platforms = {}

    def have_kernel_compiler(self) -> bool:
        return self.xchesscc is not None and self.xchesscc.exists()

    def have_graph_compiler(self) -> bool:
        return ((self.aiecompiler is not None and self.aiecompiler.exists()) or
                (self.vpp is not None and self.vpp.exists()))

    def have_vpp_aie(self) -> bool:
        return self.vpp is not None and self.vpp.exists()


def _candidate_aietools_roots(cli_aietools: str | None, cli_vitis: str | None) -> list[Path]:
    cands: list[Path] = []
    if cli_aietools:
        cands.append(Path(cli_aietools))
    env_aie = os.environ.get("AIETOOLS") or os.environ.get("XILINX_AIETOOLS")
    if env_aie:
        cands.append(Path(env_aie))
    if cli_vitis:
        v = Path(cli_vitis)
        cands.append(v.parent / "aietools")
        cands.append(v / "aietools")
    env_vitis = os.environ.get("XILINX_VITIS")
    if env_vitis:
        v = Path(env_vitis)
        cands.append(v.parent / "aietools")
        cands.append(v / "aietools")
    # Common Windows location for this user
    cands += [
        Path(r"C:\AMDDesignTools\2025.2\aietools"),
        Path(r"C:\AMDDesignTools\2025.1\aietools"),
        Path(r"C:\AMDDesignTools\2024.2\aietools"),
        Path(r"D:\fpga\2025.2\aietools"),
        Path(r"D:\fpga\2025.1\aietools"),
        Path(r"D:\fpga\2024.2\aietools"),
        Path(r"C:\Xilinx\2025.2\aietools"),
        Path(r"C:\Xilinx\2024.2\aietools"),
    ]
    # Linux fallbacks (including WSL installs)
    cands += [
        Path("/vitis/2025.2/Vitis/aietools"),
        Path("/vitis/2025.1/Vitis/aietools"),
        Path("/vitis/2024.2/Vitis/aietools"),
        Path("/opt/Xilinx/2025.2/aietools"),
        Path("/opt/Xilinx/2024.2/aietools"),
        Path("/tools/Xilinx/2025.2/aietools"),
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for c in cands:
        rc = c.resolve() if c.exists() else c
        if rc in seen:
            continue
        seen.add(rc)
        out.append(c)
    return out


def detect_toolchain(cli_aietools: str | None, cli_vitis: str | None) -> Toolchain:
    aietools_root: Path | None = None
    for cand in _candidate_aietools_roots(cli_aietools, cli_vitis):
        if cand.is_dir() and (cand / "bin").is_dir():
            aietools_root = cand
            break

    xchesscc: Path | None = None
    aiecompiler: Path | None = None
    vpp: Path | None = None
    include_dirs: list[Path] = []
    vitis_root: Path | None = None

    if aietools_root is not None:
        bin_dir = aietools_root / "bin"
        # xchesscc[.exe] lives directly in bin/, or in tps/win64/xchesscc/bin/
        for c in [
            bin_dir / f"xchesscc{EXE}",
            aietools_root / "tps" / "win64" / "xchesscc" / "bin" / "xchesscc",
            bin_dir / "unwrapped" / f"xchesscc{EXE}",
        ]:
            if c.exists():
                xchesscc = c
                break
        for c in [
            bin_dir / f"aiecompiler{BAT}",
            bin_dir / "aiecompiler",
        ]:
            if c.exists():
                aiecompiler = c
                break
        # Headers
        for inc in [
            aietools_root / "include",
            aietools_root / "include" / "aie_api",
            aietools_root / "data" / "aie_runtime_lib" / "AIE",
            aietools_root / "data" / "aie_runtime_lib" / "AIE2",
        ]:
            if inc.exists():
                include_dirs.append(inc)
        # Sibling Vitis
        sib_vitis = aietools_root.parent / "Vitis"
        if sib_vitis.exists():
            vitis_root = sib_vitis

    # Chess library paths (used with xchesscc -P)
    chess_lib_dirs: dict[str, Path] = {}
    if aietools_root is not None:
        for target_key, sub in [("AIE", "versal_prod"), ("AIE-ML", "aie_ml")]:
            lib_dir = aietools_root / "data" / sub / "lib"
            if lib_dir.is_dir():
                chess_lib_dirs[target_key] = lib_dir

    vitis_candidates = []
    if cli_vitis:
        vitis_candidates.append(Path(cli_vitis))
    env_vitis = os.environ.get("XILINX_VITIS")
    if env_vitis:
        vitis_candidates.append(Path(env_vitis))
    vitis_candidates += [
        Path(r"C:\AMDDesignTools\2025.2\Vitis"),
        Path(r"C:\AMDDesignTools\2025.1\Vitis"),
        Path(r"C:\AMDDesignTools\2024.2\Vitis"),
        Path(r"D:\fpga\2025.2\Vitis"),
        Path(r"D:\fpga\2025.1\Vitis"),
        Path(r"D:\fpga\2024.2\Vitis"),
        Path(r"C:\Xilinx\2025.2\Vitis"),
        Path(r"C:\Xilinx\2024.2\Vitis"),
    ]
    if vitis_root is None:
        for cand in vitis_candidates:
            if cand.is_dir() and (cand / "bin").is_dir():
                vitis_root = cand
                break
    for cand in vitis_candidates:
        for c in [cand / "bin" / f"v++{BAT}", cand / "bin" / "v++"]:
            if c.exists():
                vpp = c
                if vitis_root is None:
                    vitis_root = cand
                break
        if vpp is not None:
            break

    # Fallback: PATH
    if xchesscc is None:
        on_path = shutil.which("xchesscc")
        if on_path:
            xchesscc = Path(on_path)
    if aiecompiler is None:
        on_path = shutil.which("aiecompiler") or shutil.which("aiecompiler.bat")
        if on_path:
            aiecompiler = Path(on_path)
    if vpp is None:
        on_path = shutil.which("v++") or shutil.which("v++.bat")
        if on_path:
            vpp = Path(on_path)

    # Platform files for aiecompiler (prefer vck190 for AIE, vek280 for AIE-ML)
    aie_platforms: dict[str, Path] = {}
    _plat_roots = []
    if vitis_root:
        _plat_roots.append(vitis_root / "base_platforms")
    if aietools_root and aietools_root.parent:
        _plat_roots.append(aietools_root.parent / "base_platforms")
    _plat_roots += [
        Path(r"C:\AMDDesignTools\2025.2\platforms"),
        Path("/vitis/2025.2/Vitis/base_platforms"),
        Path("/vitis/2025.1/Vitis/base_platforms"),
    ]
    _plat_prefs = [
        ("AIE",    ["xilinx_vck190_base", "xilinx_vmk180_base", "vck190"]),
        ("AIE-ML", ["xilinx_vek280_base", "vek385_base", "xilinx_vek285"]),
    ]
    for _plat_root in _plat_roots:
        if not _plat_root.is_dir():
            continue
        for _tgt, _prefs in _plat_prefs:
            if _tgt in aie_platforms:
                continue
            for _entry in sorted(_plat_root.iterdir()):
                _xpfm = next(_entry.glob("*.xpfm"), None) if _entry.is_dir() else None
                if _xpfm is None:
                    continue
                if any(_p in _entry.name for _p in _prefs):
                    aie_platforms[_tgt] = _xpfm
                    break
        if len(aie_platforms) == 2:
            break
    # If AIE-ML platform not found, reuse AIE platform as fallback
    if "AIE" in aie_platforms and "AIE-ML" not in aie_platforms:
        aie_platforms["AIE-ML"] = aie_platforms["AIE"]

    return Toolchain(
        aietools_root=aietools_root or Path(""),
        vitis_root=vitis_root,
        xchesscc=xchesscc,
        aiecompiler=aiecompiler,
        vpp=vpp,
        include_dirs=include_dirs,
        chess_lib_dirs=chess_lib_dirs,
        aie_platforms=aie_platforms,
    )


def print_tool_diagnostics(tc: Toolchain) -> None:
    aroot = str(tc.aietools_root) if tc.aietools_root and str(tc.aietools_root) not in ("", ".") else "<not found>"
    print(f"[validate] aietools_root : {aroot}")
    print(f"[validate] vitis_root    : {tc.vitis_root or '<not found>'}")
    print(f"[validate] xchesscc      : {tc.xchesscc or '<MISSING>'}")
    print(f"[validate] aiecompiler   : {tc.aiecompiler or '<MISSING>'}")
    print(f"[validate] v++ AIE mode  : {tc.vpp or '<MISSING>'}")
    lic = os.environ.get("XILINXD_LICENSE_FILE") or os.environ.get("LM_LICENSE_FILE")
    print(f"[validate] license var   : {'set' if lic else '<MISSING>'}")
    if tc.include_dirs:
        print(f"[validate] include dirs  :")
        for inc in tc.include_dirs:
            print(f"               {inc}")
    if tc.chess_lib_dirs:
        print(f"[validate] chess lib dirs:")
        for tgt, lib in tc.chess_lib_dirs.items():
            print(f"               {tgt}: {lib}")
    if tc.aie_platforms:
        print(f"[validate] aie platforms :")
        for tgt, plat in tc.aie_platforms.items():
            print(f"               {tgt}: {plat}")
    if not tc.have_kernel_compiler() and not tc.have_graph_compiler():
        print()
        print("[validate] No AIE compiler path found. Source settings64.bat")
        print("           or pass --vitis <path-to-Vitis> / --aietools-bin <path>.")
    if WINDOWS and tc.vitis_root:
        settings_bat = tc.vitis_root / "settings64.bat"
        if settings_bat.exists():
            print(f"[validate] settings64    : {settings_bat}")


# ---------------------------------------------------------------------------
# Row -> code sample extraction
# ---------------------------------------------------------------------------


_CODE_FENCE = re.compile(r"```(?:cpp|c\+\+|c)?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_BUGGY_RE = re.compile(r"buggy version:\s*\n(.*?)(?:\n\s*correct version:|\Z)",
                       re.IGNORECASE | re.DOTALL)
_CORRECT_RE = re.compile(r"correct version:\s*\n(.*)", re.IGNORECASE | re.DOTALL)
_FILE_MARKER_RE = re.compile(r"^\s*//\s*FILE:\s*(.+?)\s*$", re.MULTILINE)
_FREE_FUNCTION_RE = re.compile(
    r"(?:^|\n)\s*(?:template\s*<[^>]+>\s*)?"
    r"(?:void|int|float|double|auto|int16|int32|uint16|uint32)\s+"
    r"([A-Za-z_]\w*)\s*\(",
    re.MULTILINE,
)


def _strip_fences(text: str) -> str:
    m = _CODE_FENCE.search(text)
    if m:
        return m.group(1).strip()
    return text.strip()


def extract_correct_code(row: dict) -> str | None:
    resp = (row.get("response") or "").strip()
    if not resp:
        return None
    return _strip_fences(resp) or None


def extract_buggy_code(row: dict) -> str | None:
    ctx = row.get("context") or ""
    m = _BUGGY_RE.search(ctx)
    if not m:
        return None
    code = m.group(1).strip()
    return code or None


def _split_marked_project_files(code: str) -> dict[str, str]:
    matches = list(_FILE_MARKER_RE.finditer(code))
    if not matches:
        return {}
    files: dict[str, str] = {}
    for idx, match in enumerate(matches):
        rel_path = match.group(1).strip().replace("\\", "/")
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(code)
        body = code[start:end].lstrip("\n").rstrip()
        if body:
            files[rel_path] = body + "\n"
    return files


def _materialize_marked_project_files(workdir: Path, code: str) -> dict[str, Path]:
    files = _split_marked_project_files(code)
    materialized: dict[str, Path] = {}
    for rel_path, body in files.items():
        target = workdir / Path(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        materialized[rel_path] = target
    return materialized


def _merge_include_dirs(*dir_groups: list[Path] | None) -> list[Path]:
    merged: list[Path] = []
    seen: set[str] = set()
    for group in dir_groups:
        if not group:
            continue
        for path in group:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            merged.append(path)
    return merged


def _linux_system_header_shim_dir(workdir: Path) -> Path | None:
    """Expose only the Linux asm headers aiecompiler may request indirectly."""
    sources = {
        "asm": Path("/usr/include/x86_64-linux-gnu/asm"),
        "asm-generic": Path("/usr/include/asm-generic"),
    }
    if not any(src.exists() for src in sources.values()):
        return None

    shim_dir = workdir / "system_header_shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    created = False
    for name, source in sources.items():
        if not source.exists():
            continue
        target = shim_dir / name
        if not target.exists():
            try:
                target.symlink_to(source, target_is_directory=True)
            except OSError:
                shutil.copytree(source, target)
        created = True
    return shim_dir if created else None


# ---------------------------------------------------------------------------
# Error classification & include injection
# ---------------------------------------------------------------------------

# AIE scalar types that must come from <aie_api/aie.hpp> or similar headers.
_AIE_SCALAR_TYPES = frozenset([
    "int4", "uint4", "int8", "uint8",
    "int16", "uint16", "int32", "uint32", "int64", "uint64",
    "float16", "bfloat16", "float32",
    "cint16", "cint32", "cfloat",
])

# Preamble that makes AIE scalar/vector types available in standalone files.
_AIE_PREAMBLE = (
    "#ifndef __AIE_PREAMBLE_INJECTED__\n"
    "#define __AIE_PREAMBLE_INJECTED__\n"
    "#include <adf.h>\n"
    "#include <aie_api/aie.hpp>\n"
    "#include <aie_api/aie_adf.hpp>\n"
    "#endif  // __AIE_PREAMBLE_INJECTED__\n"
    "\n"
)


def _classify_error(stderr: str) -> str:
    """Return a short error class label from compiler stderr."""
    if not stderr:
        return "ok"
    # Missing project-specific header (not a standard AIE/ADF header).
    # Matches both clang "file.h: No such file or directory" and
    # xchesscc "'file.h' file not found" variants.
    m_missing = re.search(
        r"fatal error: '?([^\s':]+\.h(?:pp)?)'?(?::|:?\s+file not found|:?\s+No such file or directory)",
        stderr,
    )
    if m_missing:
        hdr = m_missing.group(1)
        # Standard Xilinx headers are expected to be present; anything else is
        # a project-local dependency we can't satisfy.
        if not re.search(r"^(adf|aie_api|aie|hls|ap_)", hdr, re.IGNORECASE):
            return "missing_dependency"
    # Undeclared AIE scalar/vector types -> missing #include <aie_api/aie.hpp>
    m_undecl = re.search(r"use of undeclared identifier '([^']+)'", stderr)
    if m_undecl:
        bare = re.sub(r"v\d+$", "", m_undecl.group(1))  # strip v8, v16 suffix
        if bare in _AIE_SCALAR_TYPES:
            return "missing_aie_types"
    # Hard AIE API compile errors. Vitis 2025.2 deprecation notices are warnings
    # and still count as compile_ok when the compiler returns success.
    if re.search(r"no member named '[^']+' in namespace 'aie'", stderr):
        return "aie_api_compile_error"
    return "compile_error"


def _inject_aie_includes(code: str) -> str:
    """Prepend the AIE standard preamble if not already present."""
    if (
        "#include <aie_api/aie.hpp>" in code
        or "#include <adf.h>" in code
        or "__AIE_PREAMBLE_INJECTED__" in code
    ):
        return code
    return _AIE_PREAMBLE + code


_MISSING_HEADER_RE = re.compile(
    r"fatal error: '?([^\s':]+\.h(?:pp)?)'?(?::|:?\s+file not found|:?\s+No such file or directory)"
)
_SYSTEM_HEADER_PREFIXES = ("asm/", "bits/", "gnu/", "linux/", "sys/")


def _extract_missing_headers(stderr: str) -> list[str]:
    headers: list[str] = []
    seen: set[str] = set()
    for hdr in _MISSING_HEADER_RE.findall(stderr or ""):
        # Keep path-like include names, but avoid obviously unsafe paths.
        if hdr.startswith("/") or ".." in hdr.replace("\\", "/"):
            continue
        key = hdr.strip()
        if key.startswith(_SYSTEM_HEADER_PREFIXES):
            continue
        if not key or key in seen:
            continue
        seen.add(key)
        headers.append(key)
    return headers


def _create_stub_headers(base_dir: Path, headers: list[str]) -> tuple[Path, list[str]]:
    """Materialize missing include headers under base_dir and return include dir.

    For include statements like "foo/bar.h", we create base_dir/foo/bar.h so the
    compiler can resolve project-local dependencies well enough to keep parsing.
    """
    include_dir = base_dir / "auto_stub_includes"
    include_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    for hdr in headers:
        rel = Path(hdr.replace("\\", "/"))
        target = include_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            guard = re.sub(r"\W+", "_", str(rel).upper())
            target.write_text(
                f"#ifndef __AUTO_STUB_{guard}__\n"
                f"#define __AUTO_STUB_{guard}__\n"
                "// Auto-generated by validate_aie_compile.py to tolerate\n"
                "// missing project-local includes during parse-level validation.\n"
                "#endif\n",
                encoding="utf-8",
            )
        created.append(str(rel))
    return include_dir, created


def is_graph_file(code: str, metadata: dict | None) -> bool:
    if metadata:
        t = (metadata.get("type") or "").lower()
        if "graph" in t:
            return True
    # heuristic
    if "adf::graph" in code:
        return True
    if re.search(r"\bgraph\s+\w+\s*;", code) and "connect<" in code:
        return True
    if re.search(r"class\s+\w+\s*:\s*public\s+(adf::)?graph\b", code):
        return True
    if "connect<" in code and "kernel::create" in code:
        return True
    return False


def infer_kernel_function(code: str) -> str | None:
    skip = {"main", "round", "floor", "ceil", "min", "max"}
    for m in _FREE_FUNCTION_RE.finditer(code):
        name = m.group(1)
        prefix = code[max(0, m.start() - 3):m.start()]
        if "::" in prefix:
            continue
        if name not in skip:
            return name
    return None


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


@dataclass
class CompileResult:
    input_path: str
    row_index: int
    scope: str           # "buggy" | "correct"
    file_type: str       # "graph" | "kernel"
    target: str          # "AIE" | "AIE-ML"
    compiler: str        # "xchesscc" | "aiecompiler" | "skipped"
    compile_ok: bool
    return_code: int
    duration_s: float
    stderr_tail: str
    stdout_tail: str
    metadata_keys: dict[str, Any]
    # Error classification (set even on success for consistency)
    error_class: str     # "ok" | "missing_dependency" | "missing_aie_types" |
                         # "aie_api_compile_error" | "compile_error"
    include_injected: bool  # True if AIE preamble was prepended before retry
    dependency_stubbed: bool  # True if missing headers were auto-stubbed for retry
    stubbed_headers: list[str]


def _tail(s: str, n: int = 4000) -> str:
    if len(s) <= n:
        return s
    return s[-n:]


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _progress_line(done: int, total: int, n_ok: int, n_fail: int, elapsed: float, width: int = 32) -> str:
    pct = (done / total) if total else 1.0
    filled = min(width, int(round(width * pct)))
    bar = "#" * filled + "-" * (width - filled)
    rate = done / max(elapsed, 1e-3)
    eta_text = "pending" if done == 0 else _format_eta((total - done) / max(rate, 1e-3))
    return (
        f"[validate] [{bar}] {done:,}/{total:,} ({pct * 100:5.1f}%) "
        f"ok={n_ok:,} fail={n_fail:,} rate={rate:.1f}/s eta={eta_text}"
    )


def _make_workdir(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    sub = base / f"job_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    sub.mkdir(parents=True, exist_ok=True)
    return sub


def _compile_kernel(
    tc: Toolchain,
    code: str,
    workdir: Path,
    target: str,
    timeout_s: int,
    part: str,
    extra_include_dirs: list[Path] | None = None,
) -> tuple[bool, int, str, str]:
    materialized = _materialize_marked_project_files(workdir, code)
    include_dirs = _merge_include_dirs(extra_include_dirs, [workdir] if materialized else None)
    if materialized:
        kernel_candidates = [
            path for rel_path, path in materialized.items()
            if rel_path.lower().startswith("kernels/") and path.suffix.lower() in {".cc", ".cpp", ".cxx"}
        ]
        src = kernel_candidates[0] if kernel_candidates else next(iter(materialized.values()))
    else:
        src = workdir / "kernel.cc"
        src.write_text(code, encoding="utf-8")
    if tc.xchesscc is None:
        return _compile_kernel_with_vpp(tc, src.read_text(encoding="utf-8"), workdir, target, timeout_s, part, include_dirs)

    # Both AIE-1 and AIE-ML use proc "me"; the library path (-P) sets the target
    proc = "me"
    args: list[str] = [str(tc.xchesscc), "-p", proc, "-c", str(src), "-d"]
    # -P sets the chess library path; must match the target architecture
    lib_dir = tc.chess_lib_dirs.get(target) or tc.chess_lib_dirs.get("AIE")
    if lib_dir is not None:
        args += ["-P", str(lib_dir)]
    # __AIENGINE__ must be defined so adf.h takes the kernel include path
    # (without it adf.h falls through to new_frontend/adf.h which includes
    # <iostream>/<iosfwd> — not supported in AIE libc++-lite)
    args += ["-D__AIENGINE__"]
    # Chess loop-pragma macros (chess_prepare_for_pipelining etc.) expand to
    # _Pragma("chessafterloop ...") which xchesscc 2025.2's LLVM frontend rejects
    # as "expected expression".  They are optimization hints only.
    #
    # -D__chess_pragma(x)= is overridden by me_chess_llvm.h (included by the
    # xchesscc built-in lib before -D flags are visible).  Instead we write a
    # small force-include header that redefines the macros AFTER the built-in
    # includes have run.
    chess_noops = workdir / "chess_noops.h"
    chess_noops.write_text(
        "/* chess_noops.h — force-included to neutralise chess loop-pragma macros */\n"
        "#ifdef __chess_pragma\n# undef __chess_pragma\n#endif\n"
        "#define __chess_pragma(x) /* neutralised */\n"
        "#ifdef chess_loop_count\n# undef chess_loop_count\n#endif\n"
        "#define chess_loop_count(...) /* neutralised */\n"
        "#ifdef chess_loop_range\n# undef chess_loop_range\n#endif\n"
        "#define chess_loop_range(...) /* neutralised */\n",
        encoding="utf-8",
    )
    args += ["-include", str(chess_noops)]
    for inc in tc.include_dirs:
        args += ["-I", str(inc)]
    if include_dirs:
        for inc in include_dirs:
            args += ["-I", str(inc)]
    # -d = stop after parsing/code-gen w/o linking; saves time vs. full elaboration
    try:
        cp = subprocess.run(
            args, cwd=str(workdir), capture_output=True, text=True,
            timeout=timeout_s, check=False,
        )
        return cp.returncode == 0, cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as e:
        return False, -9, _as_text(e.stdout), f"TIMEOUT after {timeout_s}s\n{_as_text(e.stderr)}"
    except FileNotFoundError as e:
        return False, -2, "", f"compiler not found: {e}"


def _compile_graph(
    tc: Toolchain,
    code: str,
    workdir: Path,
    target: str,
    timeout_s: int,
    part: str,
    extra_include_dirs: list[Path] | None = None,
) -> tuple[bool, int, str, str]:
    materialized = _materialize_marked_project_files(workdir, code)
    include_dirs = _merge_include_dirs(extra_include_dirs, [workdir] if materialized else None)
    if materialized:
        graph_header_text = ""
        graph_sources = [
            path for rel_path, path in materialized.items()
            if rel_path.lower().endswith(("graph.cpp", "graph.cc", "graph.cxx"))
        ]
        if graph_sources:
            src = graph_sources[0]
        else:
            graph_headers = [
                rel_path for rel_path in materialized
                if rel_path.lower().endswith("graph.h")
            ]
            header_rel = graph_headers[0] if graph_headers else next(iter(materialized.keys()))
            header_path = materialized.get(header_rel)
            if header_path is not None and header_path.exists():
                graph_header_text = header_path.read_text(encoding="utf-8")
            graph_instance = ""
            graph_instance_name = ""
            graph_match = re.search(r"class\s+(\w+)\s*:\s*public\s+(?:adf::)?graph", graph_header_text)
            if graph_match:
                graph_class = graph_match.group(1)
                instance_match = re.search(rf"\b{re.escape(graph_class)}\s+(\w+)\s*;", graph_header_text)
                if instance_match:
                    graph_instance_name = instance_match.group(1)
                else:
                    graph_instance_name = "auto_graph_instance"
                    graph_instance = f"\n{graph_class} {graph_instance_name};\n"
            graph_main = ""
            if graph_instance_name and not re.search(r"\bint\s+main\s*\(", graph_header_text):
                graph_main = (
                    f"\nint main(void) {{\n"
                    f"    {graph_instance_name}.init();\n"
                    f"    {graph_instance_name}.run(1);\n"
                    f"    {graph_instance_name}.end();\n"
                    f"    return 0;\n"
                    f"}}\n"
                )
            src = workdir / "graph.cpp"
            src.write_text(f'#include "{header_rel}"\n{graph_instance}{graph_main}', encoding="utf-8")
    else:
        src = workdir / "graph.cpp"
        src.write_text(code, encoding="utf-8")
    if tc.aiecompiler is None:
        return _compile_graph_with_vpp(tc, src, workdir, target, timeout_s, part, include_dirs)
    # Use x86sim target for syntax/type-checking — no hardware P&R needed.
    # Requires a platform .xpfm; prefer vck190 (AIE) or vek280 (AIE-ML).
    platform = tc.aie_platforms.get(target) or tc.aie_platforms.get("AIE")
    args: list[str] = [str(tc.aiecompiler), "--target=x86sim"]
    if platform:
        args += [f"--platform={platform}"]
    else:
        # No platform found — fall back to a part number for basic parsing
        args += ["--part=xcvc1902-vsva2197-2MP-e-S"]
    args += ["--workdir", str(workdir / "aie_work"), str(src)]
    shim_dir = _linux_system_header_shim_dir(workdir)
    if shim_dir is not None:
        args += ["-I", str(shim_dir)]
    if include_dirs:
        for inc in include_dirs:
            args += ["-I", str(inc)]
    try:
        cp = subprocess.run(
            args, cwd=str(workdir), capture_output=True, text=True,
            timeout=timeout_s, check=False, shell=WINDOWS,  # .bat shim on win
        )
        return cp.returncode == 0, cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as e:
        return False, -9, _as_text(e.stdout), f"TIMEOUT after {timeout_s}s\n{_as_text(e.stderr)}"
    except FileNotFoundError as e:
        return False, -2, "", f"compiler not found: {e}"


def _run_vpp_aie(
    tc: Toolchain,
    source: Path,
    workdir: Path,
    timeout_s: int,
    part: str,
    extra_include_dirs: list[Path] | None = None,
) -> tuple[bool, int, str, str]:
    if tc.vpp is None:
        return False, -1, "", "v++ not available for --compile --mode aie"
    args: list[str] = [
        str(tc.vpp),
        "--compile",
        "--mode", "aie",
        "--target", "hw",
        "--part", part,
        "--work_dir", str(workdir / "vpp_work"),
        "--aie.output-archive", "libadf.a",
        "-o", str(workdir / "libadf.a"),
        str(source),
    ]
    for inc in tc.include_dirs:
        args += ["-I", str(inc)]
    if extra_include_dirs:
        for inc in extra_include_dirs:
            args += ["-I", str(inc)]
    try:
        cp = subprocess.run(
            args, cwd=str(workdir), capture_output=True, text=True,
            timeout=timeout_s, check=False, shell=WINDOWS,
        )
        return cp.returncode == 0, cp.returncode, cp.stdout, cp.stderr
    except subprocess.TimeoutExpired as e:
        return False, -9, _as_text(e.stdout), f"TIMEOUT after {timeout_s}s\n{_as_text(e.stderr)}"
    except FileNotFoundError as e:
        return False, -2, "", f"compiler not found: {e}"


def _compile_graph_with_vpp(
    tc: Toolchain,
    src: Path,
    workdir: Path,
    target: str,
    timeout_s: int,
    part: str,
    extra_include_dirs: list[Path] | None = None,
) -> tuple[bool, int, str, str]:
    return _run_vpp_aie(tc, src, workdir, timeout_s, part, extra_include_dirs)


def _compile_kernel_with_vpp(
    tc: Toolchain,
    code: str,
    workdir: Path,
    target: str,
    timeout_s: int,
    part: str,
    extra_include_dirs: list[Path] | None = None,
) -> tuple[bool, int, str, str]:
    kernel_name = infer_kernel_function(code)
    if not kernel_name:
        return False, -5, "", "could not infer a free kernel function for v++ graph wrapper"
    (workdir / "kernel.cc").write_text(code, encoding="utf-8")
    graph_src = workdir / "graph.cpp"
    graph_src.write_text(
        "#include <adf.h>\n"
        "using namespace adf;\n"
        f"void {kernel_name}();\n"
        "class validate_graph : public graph {\n"
        "public:\n"
        "  kernel k;\n"
        "  validate_graph() {\n"
        f"    k = kernel::create({kernel_name});\n"
        "    source(k) = \"kernel.cc\";\n"
        "    runtime<ratio>(k) = 0.9;\n"
        "  }\n"
        "};\n"
        "validate_graph g;\n"
        "int main() { return 0; }\n",
        encoding="utf-8",
    )
    ok, rc, so, se = _run_vpp_aie(tc, graph_src, workdir, timeout_s, part, extra_include_dirs)
    if not ok:
        se = f"v++ kernel wrapper inferred function '{kernel_name}'\n{se}"
    return ok, rc, so, se


def compile_one(
    job: dict,
) -> dict:
    """Worker entry point. `job` is JSON-pickleable input."""
    tc: Toolchain = Toolchain(**{
        **job["tc"],
        "aietools_root": Path(job["tc"]["aietools_root"]) if job["tc"]["aietools_root"] else Path(""),
        "vitis_root": Path(job["tc"]["vitis_root"]) if job["tc"].get("vitis_root") else None,
        "xchesscc": Path(job["tc"]["xchesscc"]) if job["tc"].get("xchesscc") else None,
        "aiecompiler": Path(job["tc"]["aiecompiler"]) if job["tc"].get("aiecompiler") else None,
        "vpp": Path(job["tc"]["vpp"]) if job["tc"].get("vpp") else None,
        "include_dirs": [Path(p) for p in job["tc"].get("include_dirs", [])],
        "chess_lib_dirs": {k: Path(v) for k, v in job["tc"].get("chess_lib_dirs", {}).items()},
        "aie_platforms": {k: Path(v) for k, v in job["tc"].get("aie_platforms", {}).items()},
    })
    code: str = job["code"]
    file_type: str = job["file_type"]
    target: str = job["target"]
    part: str = job["part"]
    timeout_s: int = job["timeout_s"]
    missing_dep_mode: str = job.get("missing_dependency_mode", "stub")
    workdir_root = Path(job["workdir_root"])
    keep = bool(job["keep_workdir"])

    wd = _make_workdir(workdir_root)
    t0 = time.time()
    error_class = "compile_error"
    include_injected = False
    dependency_stubbed = False
    stubbed_headers: list[str] = []
    try:
        if file_type == "graph":
            ok, rc, so, se = _compile_graph(tc, code, wd, target, timeout_s, part)
            compiler = "aiecompiler" if tc.aiecompiler else "v++ --mode aie"
        else:
            ok, rc, so, se = _compile_kernel(tc, code, wd, target, timeout_s, part)
            compiler = "xchesscc" if tc.xchesscc else "v++ --mode aie wrapper"

        error_class = "ok" if ok else _classify_error(se)
        # When stderr is empty, aiecompiler often writes errors to stdout.
        # _classify_error returns "ok" for empty input, which is misleading.
        if not ok and error_class == "ok":
            error_class = _classify_error(so) if so and so.strip() else "compile_error"
            if error_class == "ok":
                error_class = "compile_error"

        # Retry with AIE preamble injected if the failure was due to missing
        # AIE type declarations (e.g. `int16`, `cint32` undeclared).
        if not ok and error_class == "missing_aie_types":
            injected_code = _inject_aie_includes(code)
            if injected_code != code:
                include_injected = True
                if file_type == "graph":
                    ok2, rc2, so2, se2 = _compile_graph(
                        tc, injected_code, wd, target, timeout_s, part
                    )
                else:
                    ok2, rc2, so2, se2 = _compile_kernel(
                        tc, injected_code, wd, target, timeout_s, part
                    )
                if ok2:
                    ok, rc, so, se = ok2, rc2, so2, se2
                    error_class = "ok"
                else:
                    # Keep original stderr but reclassify with retry output
                    error_class = _classify_error(se2) or "compile_error"

        # Retry with auto-generated project header stubs for missing includes.
        if not ok and error_class == "missing_dependency" and missing_dep_mode == "stub":
            missing_headers = _extract_missing_headers(se)
            if missing_headers:
                stub_dir, created = _create_stub_headers(wd, missing_headers)
                stubbed_headers = created
                dependency_stubbed = True
                if file_type == "graph":
                    ok2, rc2, so2, se2 = _compile_graph(
                        tc, code, wd, target, timeout_s, part,
                        extra_include_dirs=[stub_dir],
                    )
                else:
                    ok2, rc2, so2, se2 = _compile_kernel(
                        tc, code, wd, target, timeout_s, part,
                        extra_include_dirs=[stub_dir],
                    )
                if ok2:
                    ok, rc, so, se = ok2, rc2, so2, se2
                    error_class = "ok"
                else:
                    ok, rc, so, se = ok2, rc2, so2, se2
                    cls2 = _classify_error(se2)
                    if cls2 == "missing_dependency":
                        error_class = "missing_dependency_after_stub"
                    else:
                        error_class = cls2 or "compile_error"
    except Exception:
        ok, rc, so, se = False, -3, "", traceback.format_exc()
        compiler = "exception"
        error_class = "exception"
    dur = time.time() - t0

    if not keep:
        try:
            shutil.rmtree(wd, ignore_errors=True)
        except Exception:
            pass

    res = CompileResult(
        input_path=job["input_path"],
        row_index=job["row_index"],
        scope=job["scope"],
        file_type=file_type,
        target=target,
        compiler=compiler,
        compile_ok=ok,
        return_code=rc,
        duration_s=round(dur, 3),
        stderr_tail=_tail(se or "", 4000),
        stdout_tail=_tail(so or "", 1000),
        metadata_keys=job["metadata_keys"],
        error_class=error_class,
        include_injected=include_injected,
        dependency_stubbed=dependency_stubbed,
        stubbed_headers=stubbed_headers,
    )
    return asdict(res)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def iter_input_rows(path: Path) -> Iterator[tuple[int, dict]]:
    with path.open("r", encoding="utf-8") as fp:
        for i, line in enumerate(fp):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def _count_jsonl_rows(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as fp:
            return sum(1 for line in fp if line.strip())
    except OSError:
        return 0


def find_v2_all_inputs() -> list[Path] | None:
    """Return input path(s) for the latest V2 all dataset.

    Some archive snapshots accidentally contain an aie_instruction_v2_all.jsonl
    that is byte-for-byte the train split.  When that happens, use train +
    validation directly so --v2-all really validates the full V2 corpus.
    """
    matches = sorted(Path(".").glob(DEFAULT_V2_ARCHIVE_GLOB))
    if not matches:
        return None

    all_path = matches[-1]
    archive_dir = all_path.parent
    train_path = archive_dir / "aie_instruction_v2_train.jsonl"
    validation_path = archive_dir / "aie_instruction_v2_validation.jsonl"
    if train_path.exists() and validation_path.exists():
        n_all = _count_jsonl_rows(all_path)
        n_train = _count_jsonl_rows(train_path)
        n_validation = _count_jsonl_rows(validation_path)
        if n_all <= n_train and n_validation > 0:
            print(
                f"[validate] WARNING: {all_path} has {n_all:,} rows, same/less than train "
                f"({n_train:,}); using train+validation ({n_train + n_validation:,}) for --v2-all."
            )
            return [train_path, validation_path]
    return [all_path]


def already_done_keys(out_path: Path) -> set[tuple[str, int, str, str]]:
    keys: set[tuple[str, int, str, str]] = set()
    if not out_path.exists():
        return keys
    with out_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            keys.add((r.get("input_path", ""),
                      int(r.get("row_index", -1)),
                      r.get("scope", ""),
                      r.get("target", "")))
    return keys


def _result_error_summary(result: dict[str, Any]) -> dict[str, Any]:
    stderr_tail = result.get("stderr_tail") or ""
    stdout_tail = result.get("stdout_tail") or ""
    first_error = ""
    for line in (stderr_tail + "\n" + stdout_tail).splitlines():
        if "error:" in line or "ERROR:" in line or "fatal error:" in line:
            first_error = line.strip()
            break
    if not first_error:
        for line in (stderr_tail + "\n" + stdout_tail).splitlines():
            if line.strip():
                first_error = line.strip()
                break

    return {
        "compile_ok": bool(result.get("compile_ok")),
        "error_class": result.get("error_class") or ("ok" if result.get("compile_ok") else "compile_error"),
        "error_reason": first_error,
        "compiler": result.get("compiler"),
        "return_code": result.get("return_code"),
        "file_type": result.get("file_type"),
        "target": result.get("target"),
        "scope": result.get("scope"),
        "duration_s": result.get("duration_s"),
        "include_injected": bool(result.get("include_injected")),
        "dependency_stubbed": bool(result.get("dependency_stubbed")),
        "stubbed_headers": result.get("stubbed_headers") or [],
        "stderr_tail": stderr_tail,
        "stdout_tail": stdout_tail,
    }


def _choose_row_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    for result in results:
        if result.get("compile_ok"):
            return result
    # Prefer the shortest/most direct failing compile; otherwise first result.
    return sorted(results, key=lambda r: (r.get("duration_s") or 0.0, r.get("error_class") or ""))[0]


def split_rows_by_compile_results(
    input_paths: list[Path],
    results_path: Path,
    working_out: Path,
    not_working_out: Path,
    include_missing_results: bool = True,
) -> tuple[int, int, int]:
    """Write original dataset rows into working/not-working JSONL files.

    Each emitted row is the original instruction row with
    metadata.compile_validation attached.  Non-working rows keep error_class,
    error_reason, compiler, return_code, and output tails for curation.
    """
    results_by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    with results_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                row_index = int(result.get("row_index", -1))
            except (TypeError, ValueError):
                continue
            input_path = str(Path(result.get("input_path", "")))
            results_by_key.setdefault((input_path, row_index), []).append(result)

    working_out.parent.mkdir(parents=True, exist_ok=True)
    not_working_out.parent.mkdir(parents=True, exist_ok=True)
    n_working = 0
    n_not_working = 0
    n_missing_result = 0

    with working_out.open("w", encoding="utf-8") as wok, \
         not_working_out.open("w", encoding="utf-8") as nok:
        for input_path in input_paths:
            input_key = str(input_path)
            for row_index, row in iter_input_rows(input_path):
                result_options = results_by_key.get((input_key, row_index), [])
                if not result_options:
                    if not include_missing_results:
                        continue
                    n_missing_result += 1
                    validation = {
                        "compile_ok": False,
                        "error_class": "not_validated",
                        "error_reason": "No compile result was produced for this row.",
                    }
                    out_fp = nok
                    n_not_working += 1
                else:
                    chosen = _choose_row_result(result_options)
                    validation = _result_error_summary(chosen)
                    if validation["compile_ok"]:
                        out_fp = wok
                        n_working += 1
                    else:
                        out_fp = nok
                        n_not_working += 1

                row_out = dict(row)
                metadata = dict(row_out.get("metadata") or {})
                metadata["compile_validation"] = validation
                row_out["metadata"] = metadata
                out_fp.write(json.dumps(row_out, ensure_ascii=False) + "\n")

    return n_working, n_not_working, n_missing_result


def build_jobs(
    input_paths: list[Path],
    scope: str,
    targets: list[str],
    target_parts: dict[str, str],
    tc: Toolchain,
    workdir_root: Path,
    timeout_s: int,
    keep_workdir: bool,
    missing_dependency_mode: str,
    limit: int | None,
    skip_keys: set[tuple[str, int, str, str]],
) -> Iterator[dict]:
    tc_ser = {
        "aietools_root": str(tc.aietools_root) if tc.aietools_root else "",
        "vitis_root": str(tc.vitis_root) if tc.vitis_root else None,
        "xchesscc": str(tc.xchesscc) if tc.xchesscc else None,
        "aiecompiler": str(tc.aiecompiler) if tc.aiecompiler else None,
        "vpp": str(tc.vpp) if tc.vpp else None,
        "include_dirs": [str(p) for p in tc.include_dirs],
        "chess_lib_dirs": {k: str(v) for k, v in tc.chess_lib_dirs.items()},
        "aie_platforms": {k: str(v) for k, v in tc.aie_platforms.items()},
    }
    scopes = ["buggy", "correct"] if scope == "both" else [scope]
    emitted = 0
    for ip in input_paths:
        ip_str = str(ip)
        for ridx, row in iter_input_rows(ip):
            md = row.get("metadata") or {}
            md_keys = {k: md.get(k) for k in (
                "type", "category", "bug_type", "difficulty_tier",
                "source", "synthetic", "split",
            )}
            for sc in scopes:
                code = (extract_correct_code(row) if sc == "correct"
                        else extract_buggy_code(row))
                if not code or len(code) < 30:
                    continue
                ftype = "graph" if is_graph_file(code, md) else "kernel"
                if ftype == "graph" and not tc.have_graph_compiler():
                    # Skip rather than fail; logged later.
                    continue
                if ftype == "kernel" and not tc.have_kernel_compiler():
                    continue
                for tgt in targets:
                    if (ip_str, ridx, sc, tgt) in skip_keys:
                        continue
                    yield {
                        "tc": tc_ser,
                        "input_path": ip_str,
                        "row_index": ridx,
                        "scope": sc,
                        "code": code,
                        "file_type": ftype,
                        "target": tgt,
                        "part": target_parts[tgt],
                        "timeout_s": timeout_s,
                        "missing_dependency_mode": missing_dependency_mode,
                        "workdir_root": str(workdir_root),
                        "keep_workdir": keep_workdir,
                        "metadata_keys": md_keys,
                    }
                    emitted += 1
                    if limit is not None and emitted >= limit:
                        return


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", nargs="+", required=False,
                    help="One or more JSONL files (e.g. data/processed/split up/*.jsonl).")
    ap.add_argument("--out", required=False, help="Output JSONL of compile results.")
    ap.add_argument("--v2-all", action="store_true",
                    help=(
                        "Use the latest data/processed/archive_v2_and_synthpairs_*/"
                        "aie_instruction_v2_all.jsonl and default output "
                        "data/processed/compile_results/all_v2.jsonl."
                    ))
    ap.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            "Parallel compile workers. Defaults to a Vitis-safe high-concurrency value. "
            "Use --workers 0 to auto-select the Vitis-safe default."
        ),
    )
    ap.add_argument("--scope", choices=["correct", "buggy", "both"], default="correct")
    ap.add_argument("--target", choices=["AIE", "AIE-ML", "both"], default="AIE",
                    help="Chess processor target. 'both' compiles each sample twice.")
    ap.add_argument("--aie-part", default="xcvc1902-vsva2197-2MP-e-S",
                    help="Vitis part for --target AIE when using v++ fallback.")
    ap.add_argument("--aieml-part", default="xcve2802-vsvh1760-2MP-e-S",
                    help="Vitis part for --target AIE-ML when using v++ fallback.")
    ap.add_argument("--timeout", type=int, default=60, help="Per-compile timeout (seconds).")
    ap.add_argument("--limit", type=int, default=None, help="Cap total compile jobs.")
    ap.add_argument("--workdir", default=None, help="Scratch dir root (default: %TEMP%/aie_validate)")
    ap.add_argument("--keep-workdir", action="store_true", help="Don't delete per-job dirs.")
    ap.add_argument(
        "--missing-dependency-mode",
        choices=["classify", "stub"],
        default="stub",
        help=(
            "How to handle missing project-local headers: "
            "'classify' keeps them as missing_dependency; "
            "'stub' auto-generates empty headers and retries to reduce unknowns."
        ),
    )
    ap.add_argument("--resume", action="store_true",
                    help="Skip rows already present in --out.")
    ap.add_argument("--aietools-bin", default=None, help="Path to <vitis>/aietools.")
    ap.add_argument("--vitis", default=None, help="Path to <root>/Vitis (sibling of aietools).")
    ap.add_argument("--diagnose", action="store_true",
                    help="Just print detected tool paths and exit.")
    ap.add_argument("--split-rows", action="store_true",
                    help="After validation, split original dataset rows into working/not-working JSONL files.")
    ap.add_argument("--working-out", default=None,
                    help="Working-row JSONL path for --split-rows (default: <out stem>_working.jsonl).")
    ap.add_argument("--not-working-out", default=None,
                    help="Not-working-row JSONL path for --split-rows (default: <out stem>_not_working.jsonl).")
    args = ap.parse_args(argv)

    if args.v2_all:
        v2_inputs = find_v2_all_inputs()
        if v2_inputs is None:
            print(f"[validate] ERROR: no V2 all JSONL matched {DEFAULT_V2_ARCHIVE_GLOB}", file=sys.stderr)
            return 2
        if not args.input:
            args.input = [str(p) for p in v2_inputs]
        if not args.out:
            args.out = str(DEFAULT_V2_RESULTS)

    if not args.input:
        print("[validate] ERROR: --input is required unless --v2-all is used.", file=sys.stderr)
        return 2
    if not args.out:
        print("[validate] ERROR: --out is required unless --v2-all is used.", file=sys.stderr)
        return 2

    if args.workers <= 0:
        args.workers = DEFAULT_WORKERS
    if args.workers > DEFAULT_WORKERS:
        print(
            f"[validate] WARNING: workers={args.workers} exceeds Vitis-safe auto default "
            f"{DEFAULT_WORKERS}; high concurrency can crash xchesscc/aiecompiler on WSL."
        )

    tc = detect_toolchain(args.aietools_bin, args.vitis)
    print_tool_diagnostics(tc)

    if args.diagnose:
        return 0

    if not (tc.have_kernel_compiler() or tc.have_graph_compiler()):
        return 2

    input_paths = [Path(p) for p in args.input]
    for ip in input_paths:
        if not ip.exists():
            print(f"[validate] ERROR: input not found: {ip}", file=sys.stderr)
            return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    skip_keys = already_done_keys(out_path) if args.resume else set()
    if skip_keys:
        print(f"[validate] resume: {len(skip_keys):,} prior results, skipping those.")

    workdir_root = Path(args.workdir) if args.workdir else Path(tempfile.gettempdir()) / "aie_validate"
    workdir_root.mkdir(parents=True, exist_ok=True)

    targets = ["AIE", "AIE-ML"] if args.target == "both" else [args.target]
    target_parts = {"AIE": args.aie_part, "AIE-ML": args.aieml_part}

    jobs = list(build_jobs(
        input_paths=input_paths,
        scope=args.scope,
        targets=targets,
        target_parts=target_parts,
        tc=tc,
        workdir_root=workdir_root,
        timeout_s=args.timeout,
        keep_workdir=args.keep_workdir,
        missing_dependency_mode=args.missing_dependency_mode,
        limit=args.limit,
        skip_keys=skip_keys,
    ))
    print(f"[validate] {len(jobs):,} compile jobs queued | workers={args.workers} | "
          f"scope={args.scope} | target={args.target} | timeout={args.timeout}s")

    if not jobs:
        print("[validate] nothing to do.")
        return 0

    n_ok = 0
    n_fail = 0
    t_start = time.time()
    print(_progress_line(0, len(jobs), n_ok, n_fail, 0.0))

    with out_path.open("a", encoding="utf-8") as ofp, \
         ProcessPoolExecutor(max_workers=args.workers) as pool:
        job_iter = iter(jobs)
        in_flight = set()
        max_in_flight = max(args.workers, args.workers * 2)
        progress_every = max(1, min(25, len(jobs) // 100 or 1))

        def submit_more() -> None:
            while len(in_flight) < max_in_flight:
                try:
                    job = next(job_iter)
                except StopIteration:
                    return
                in_flight.add(pool.submit(compile_one, job))

        submit_more()
        i = 0
        while in_flight:
            done, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in done:
                i += 1
                try:
                    res = fut.result()
                except Exception as e:
                    res = {
                        "input_path": "?", "row_index": -1, "scope": "?",
                        "file_type": "?", "target": "?", "compiler": "exception",
                        "compile_ok": False, "return_code": -4, "duration_s": 0.0,
                        "stderr_tail": f"worker exception: {e}\n{traceback.format_exc()}",
                        "stdout_tail": "", "metadata_keys": {},
                    }
                ofp.write(json.dumps(res, ensure_ascii=False) + "\n")
                ofp.flush()
                if res["compile_ok"]:
                    n_ok += 1
                else:
                    n_fail += 1
                if i % progress_every == 0 or i == len(jobs):
                    elapsed = time.time() - t_start
                    print(_progress_line(i, len(jobs), n_ok, n_fail, elapsed))
            submit_more()

    elapsed = time.time() - t_start
    print(f"[validate] done in {elapsed:.1f}s | ok={n_ok} fail={n_fail} | -> {out_path}")

    if args.split_rows:
        working_out = Path(args.working_out) if args.working_out else out_path.with_name(f"{out_path.stem}_working.jsonl")
        not_working_out = Path(args.not_working_out) if args.not_working_out else out_path.with_name(f"{out_path.stem}_not_working.jsonl")
        n_working, n_not_working, n_missing = split_rows_by_compile_results(
            input_paths=input_paths,
            results_path=out_path,
            working_out=working_out,
            not_working_out=not_working_out,
            include_missing_results=args.limit is None,
        )
        print(
            f"[validate] split rows: working={n_working:,} not_working={n_not_working:,} "
            f"missing_results={n_missing:,} | working -> {working_out} | not_working -> {not_working_out}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
