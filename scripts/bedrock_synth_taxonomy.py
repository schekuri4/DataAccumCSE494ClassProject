#!/usr/bin/env python3
"""Generate LLM-quality buggy/correct AIE full-project pairs via AWS Bedrock.

For each taxonomy slug that currently relies on template synthesis, ask the
model to write a realistic, complete AIE mini-project pair that faithfully
exhibits the bug.

Output: data/processed/v3/bedrock_synth_pairs_v3.jsonl
  One JSON object per line:
        {slug, label, tier, variant_idx, buggy, correct, model, ts, input_tokens, output_tokens,
         full_code_ok, full_code_reasons}

After running, rebuild the dataset:
  python scripts/build_aie_instruction_dataset.py --v2

Usage examples:
  # All slugs, 3 variants each, resume skipping already-done
  python scripts/bedrock_synth_taxonomy.py --resume

  # Specific slugs only
  python scripts/bedrock_synth_taxonomy.py --slugs rtp_update_race_condition buffer_aliasing_incorrect_restrict

  # See what prompt would be sent without calling the API
  python scripts/bedrock_synth_taxonomy.py --dry-run --slugs rtp_update_race_condition

  # More variants, different model
  python scripts/bedrock_synth_taxonomy.py --variants 5 --model anthropic.claude-3-5-sonnet-20241022-v2:0

Env vars:
  AWS_BEARER_TOKEN_BEDROCK   Bedrock API key (base64-encoded bearer token)
  AWS_DEFAULT_REGION         defaults to us-east-1
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

OUTPUT_FILE = ROOT / "data" / "processed" / "v3" / "bedrock_synth_pairs_v3.jsonl"
COST_HISTORY_FILE = ROOT / "data" / "processed" / "v3" / "bedrock_synth_cost_history.jsonl"
COST_TOTALS_FILE = ROOT / "data" / "processed" / "v3" / "bedrock_synth_cost_totals.json"
DEFAULT_MODEL = "deepseek.v3.2"
DEFAULT_REGION = "us-east-1"
DEFAULT_VARIANTS = 3
DEFAULT_MAX_TOKENS = 8192
DEFAULT_WORKERS = 7
DEFAULT_TOPICS_TIER = "extra_hard"
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2.0  # seconds, doubled each retry

# Patterns that indicate fabricated/incorrect API calls or leaked bug hints
_BAD_API_PATTERNS = [
    "aie::read_v",
    "aie::write_stream",
    "acc.push",
    "aie::mul(v_",
    "aie::add(acc",
    "vector_cast",
    "aie::filter_sym",
    "aie::fir_taps",
    "aie::extract_v<",
    "aie::reduce_add",
    "adf::kernel::create<",
]
_HINT_WORDS = ["// bug", "// error", "// wrong", "// broken", "bug sets", "bug uses",
               "// incorrect", "// bad", "// fix", "// issue", "// problem",
               "// hack", "// workaround", "// todo", "// expected", "// should be"]


def _is_quality_ok(buggy: str, correct: str) -> bool:
    """Return False if the pair contains known bad API patterns or hint comments."""
    for api in _BAD_API_PATTERNS:
        if api in buggy or api in correct:
            return False
    for line in buggy.splitlines():
        if any(w in line.lower() for w in _HINT_WORDS):
            return False
    return True


def _split_marked_project_files(code: str) -> dict[str, str]:
    files: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in code.splitlines():
        match = re.match(r"^\s*//\s*FILE:\s*(.+?)\s*$", line)
        if match:
            if current_name is not None:
                files[current_name] = "\n".join(current_lines).strip()
            current_name = match.group(1).strip().replace("\\", "/")
            current_lines = []
            continue
        if current_name is not None:
            current_lines.append(line)
    if current_name is not None:
        files[current_name] = "\n".join(current_lines).strip()
    return files


def _full_code_requirements(code: str) -> tuple[bool, list[str]]:
    """Check whether a generated project block looks synthesis-ready.

    This is a structural gate, not a semantic compiler substitute.
    """
    reasons: list[str] = []
    files = _split_marked_project_files(code)
    file_markers = re.findall(r"^\s*//\s*FILE:\s*(.+)$", code, flags=re.MULTILINE)
    if len(file_markers) < 2:
        reasons.append("needs_at_least_two_files")

    lower_markers = [m.strip().lower() for m in file_markers]
    if not any(m.endswith("graph.h") for m in lower_markers):
        reasons.append("missing_graph_h")
    if not any("kernels/" in m and m.endswith((".cc", ".cpp")) for m in lower_markers):
        reasons.append("missing_kernel_source_file")

    checks = [
        (r"#include\s*<adf\.h>", "missing_adf_include"),
        (r"class\s+\w+\s*:\s*public\s+adf::graph", "missing_graph_class"),
        (r"adf::kernel::create\s*\(", "missing_kernel_create"),
        (r"adf::source\s*\(", "missing_source_assignment"),
        (r"adf::connect\s*<", "missing_connect"),
        (r"adf::runtime\s*<\s*adf::ratio\s*>\s*\(", "missing_runtime_ratio"),
    ]
    for pattern, reason in checks:
        if not re.search(pattern, code):
            reasons.append(reason)

    graph_code = files.get("graph.h", "")
    if graph_code:
        graph_match = re.search(r"class\s+(\w+)\s*:\s*public\s+adf::graph", graph_code)
        if graph_match:
            graph_class = graph_match.group(1)
            if not re.search(rf"\b{re.escape(graph_class)}\s+\w+\s*;", graph_code):
                reasons.append("missing_graph_instance")
        else:
            reasons.append("missing_graph_class")

        create_names: set[str] = set()
        for match in re.finditer(r"adf::kernel::create\s*\(\s*([A-Za-z_]\w*)\s*\)", graph_code):
            create_names.add(match.group(1))
        for match in re.finditer(r"adf::kernel::create\s*<\s*([A-Za-z_]\w*)\s*>\s*\(", graph_code):
            create_names.add(match.group(1))
        for name in create_names:
            if not re.search(rf"(?:^|\n)\s*(?:extern\s+)?void\s+{re.escape(name)}\s*\(", graph_code):
                reasons.append("missing_kernel_forward_declaration")
                break

        if re.search(r"\b(?:input_stream|output_stream)\s*<[^>]+>\s+[A-Za-z_]\w*\s*(?:,|;)", graph_code):
            reasons.append("kernel_stream_declared_in_graph_h")

        unqualified_adf_patterns = [
            r"(?<!adf::)\bkernel::create\s*\(",
            r"(?<!adf::)\binput_plio::create\s*\(",
            r"(?<!adf::)\boutput_plio::create\s*\(",
            r"(?<!adf::)\bconnect\s*<",
            r"(?<!adf::)\bruntime\s*<\s*ratio\s*>",
            r"(?<!adf::)\bplio_(?:32|64|128)_bits\b",
            r"(?<!adf::)\bstream\b",
            r"(?<!adf::)\bwindow\s*<",
        ]
        if any(re.search(pattern, graph_code) for pattern in unqualified_adf_patterns):
            reasons.append("unqualified_adf_graph_symbols")

    if re.search(r"adf::kernel::create\s*<", code):
        reasons.append("templated_kernel_create")
    if re.search(r"aie::zeros\s*<\s*(?:int|uint|cint|float|bfloat)\w*\s*,", code):
        reasons.append("invalid_vector_zeros_api")
    if re.search(r"\.to_vector\s*<[^>]+>\s*\(\s*\)", code):
        reasons.append("missing_to_vector_shift")
    if re.search(r"aie::mac\s*\([^\n,]+,[^\n,]+,\s*\d+\s*,", code):
        reasons.append("unsupported_sliding_mac_signature")
    if re.search(r"aie::shuffle_up\s*\(", code):
        reasons.append("unsupported_shuffle_up_usage")
    # aie::broadcast<T,N>(scalar) is a valid AIE API — no gate check needed

    if graph_code:
        uses_window_connect = re.search(r"adf::connect\s*<\s*adf::window<", graph_code)
        uses_stream_connect = re.search(r"adf::connect\s*<\s*adf::stream\s*>", graph_code)
        stream_signature = re.search(r"\bvoid\s+\w+\s*\([^)]*\b(?:input_stream|output_stream)\s*<", code)
        window_signature = re.search(r"\bvoid\s+\w+\s*\([^)]*\b(?:input_window|output_window)\s*<", code)
        if uses_window_connect and stream_signature:
            reasons.append("window_connect_stream_signature_mismatch")
        if uses_stream_connect and window_signature:
            reasons.append("stream_connect_window_signature_mismatch")

    if not re.search(r"\bvoid\s+\w+\s*\([^)]*\)\s*\{", code):
        reasons.append("missing_kernel_function_body")
    if not re.search(r"readincr|writeincr|load_v|store_v|aie::mac|aie::mul|input_buffer|output_buffer", code):
        reasons.append("missing_aie_kernel_ops")

    return (len(reasons) == 0), reasons


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert C++ developer specialising in AMD Versal AI Engine (AIE) \
kernel and graph programming. You write complete, realistic AIE mini-projects \
that are structurally ready for xchesscc + Vitis AIE builds.

APPROVED AIE API — use ONLY these forms, no others:
  Reading streams   : readincr(stream) / readincr_v<N>(stream)
  Writing streams   : writeincr(stream, val) / writeincr_v<N>(stream, vec)
  Vector types      : aie::vector<T,N>  (e.g. aie::vector<int16,16>)
  Accumulator types : aie::accum<acc48,N> / aie::accum<acc80,N> / aie::accum<cacc48,N>
  Load/store        : aie::load_v<N>(ptr) / aie::store_v(ptr, vec)
  MAC / MUL         : aie::mac(acc, vec_a, vec_b) / aie::mul(vec_a, vec_b)
  Shift-round-sat   : aie::srs(acc, shift)
  Broadcast         : aie::broadcast<T,N>(scalar)
    Zeros             : aie::zeros<acc48,N>() / aie::zeros<acc80,N>()
    To vector         : acc.to_vector<T>(shift)
    Begin vector      : aie::begin_vector<N>(buf)
  Pipelining pragma : chess_prepare_for_pipelining (on loops, no parentheses)
  Buffer iterators  : aie::begin(buf) / aie::end(buf)
    Sliding MAC       : aie::sliding_mul<...>(...)
    Concat            : aie::concat(vec_a, vec_b)
    Extract           : aie::extract<N>(vec, idx)
    Interleave        : aie::interleave_zip / aie::interleave_unzip
    Shuffle           : aie::shuffle_up / aie::shuffle_down
  Buffers           : input_buffer<T> / output_buffer<T> (with & reference)
  Streams           : input_stream<T>* / output_stream<T>* (pointer, not reference)

FORBIDDEN — never emit these (they do not exist in the real API):
  aie::read_v, aie::write_stream, aie::add(acc,...), acc.push(...),
  aie::mul(vec, scalar_index, ptr, offset), aie::extract_v<N,idx>,
  aie::reduce_add on an accum, vector_cast, aie::filter_sym, aie::fir_taps,
  aie::accumulate_init, aie::read_from_stream,
  aie::begin_vector<N> on streams (only works on buffers),
  input_stream<T>& (reference — streams are always pointers),
  aie::vector<T,N> with N not a power of 2,
  aie::zeros without template args (must specify accumulator type and lanes),
    aie::zeros<T,N>() for vector element types like int16/float/cint16,
    acc.to_vector<T>() with no shift argument,
    adf::kernel::create<func>() or adf::kernel::create<0>(func),
    old multi-offset aie::mac(acc, vec, 0, coeff, 0) signatures,
    aie::shuffle_up(...) helper idioms (aie::broadcast<T,N>(scalar) IS valid),
  #include "kernels/anything.cc" in graph.h (use adf::source instead),
  Unqualified ADF names (output_plio, plio_32_bits, stream, window, connect),
  readincr/writeincr without #include <aie_api/aie_adf.hpp>

GRAPH RULES (critical — violations cause compile failure):
  1. Always use adf:: prefix: adf::input_plio, adf::output_plio,
      adf::plio_32_bits, adf::plio_64_bits, adf::plio_128_bits,
      adf::stream, adf::window<N>, adf::connect,
      adf::kernel, adf::source, adf::runtime, adf::ratio.
      NEVER use unqualified names like output_plio or plio_32_bits.
  2. graph.h must NOT #include any kernels/*.cc file.
      Use adf::source(k) = "kernels/name.cc" instead.
  3. graph.h needs these includes ONLY:
      #include <adf.h>
      DO NOT include aie_api headers in graph.h — those go in kernels/*.cc.
  4. kernels/*.cc needs these includes:
      #include <aie_api/aie.hpp>
      #include <aie_api/aie_adf.hpp>

Window byte-size rule (critical):
  adf::connect<adf::window<BYTES>> uses BYTES not sample count.
  int16 = 2 bytes per sample, int32 = 4 bytes, float = 4 bytes.
  256 int16 samples = window<512>, not window<256>.
  Always show the byte calculation in a comment.

Project completeness rules (critical):
  1. Each BUGGY/CORRECT block must contain a complete mini-project using file
      markers in this exact format:
         // FILE: graph.h
         ...
         // FILE: kernels/<name>.cc
         ...
      At minimum include graph.h and one kernels/*.cc file.
      Prefer exactly two files total: graph.h and one kernels/*.cc file.
  2. graph.h must include: #include <adf.h>, class ... : public adf::graph,
      adf::kernel::create(...), adf::source(...), adf::connect<...>(...),
      adf::runtime<adf::ratio>(...), and one global graph object instance
      after the class definition because no separate graph.cpp file is emitted.
      graph.h must also declare every kernel entry function before it is used
      in adf::kernel::create(...), for example: void fir_filter(...);
  3. kernels/*.cc must include at least one actual kernel function body with AIE
      stream/buffer operations or AIE vector intrinsics.
  4. The BUGGY and CORRECT projects must have identical file layout and differ
      only by the minimal fix.
  5. Keep the project compact. Do not emit header guards, license banners, long
     prose comments, giant literal coefficient tables, or extra helper files
     unless they are strictly required for the bug.
  6. Hard size budget: keep the entire response under 170 total lines across
      both BUGGY and CORRECT blocks. Prefer 40-70 lines per block.
  7. Use small realistic kernels. Prefer 32, 64, or 128 iterations rather than
      1024+ unless the bug fundamentally requires a larger bound.
  8. Prefer scalar or simple vector kernels over advanced lane-shuffle or
      sliding-window idioms unless the bug specifically requires them.
  9. Keep graph interfaces consistent with kernel signatures: stream connects
      require input_stream/output_stream pointers; window connects require
      input_window/output_window pointers.
  10. Default shape unless the bug category explicitly requires otherwise:
      one input PLIO, one output PLIO, one kernel, adf::connect<adf::stream>,
      and a stream kernel that uses readincr/writeincr or readincr_v/writeincr_v.
      Do not use windows, RTPs, multiple kernels, or extra helper state unless
      the bug category cannot be expressed without them.

Code quality rules:
  1. Both projects must compile structurally: correct includes, valid types,
      no undefined symbols.
  2. The bug must be a real semantic error — wrong variable, missing pragma,
     off-by-one index, wrong operator — NOT a typo or missing semicolon.
  3. Every comment in the BUGGY file must be innocent. Do NOT write any comment
     containing: bug, error, wrong, broken, incorrect, bad, fix, issue, problem,
     expected, should be, todo, hack, workaround.
  4. The CORRECT file may add a short comment explaining the fix, but must be
     otherwise identical to the BUGGY file.

Mutation-friendly baseline rules:
  1. Correct-only projects should be easy to turn into a buggy/correct pair
      later by changing one local expression, constant, index, operator, loop
      bound, array choice, phase choice, or state variable.
  2. Prefer explicit named constants and intermediate variables over collapsed
      one-liners, so a future mutation can target one obvious semantic decision.
  3. Keep topic-relevant structure visible: if the topic mentions coefficients,
      phases, delay lines, state, normalization, stride, or channel independence,
      represent that concept directly in the correct code.
  4. Avoid clever helper abstractions, generated tables, and advanced APIs that
      make the later minimal buggy edit ambiguous or structurally risky.
"""

USER_TEMPLATE = """\
Create an AIE debugging exercise for the following bug category.

Bug category : {label}
Tier         : {tier}
Variant      : {variant_idx}

IMPORTANT — the bug category is a description of the ERROR CLASS, not a script.
Any specific values shown in parentheses (e.g. "7 instead of 3", "128 instead of 256")
are illustrative examples only. You MUST choose your own realistic values,
variable names, and algorithm context — do NOT reproduce the example numbers or
names literally. Every variant must be independently plausible as a real bug.

Respond with ONLY the two XML blocks below — no prose, no explanation, no diff.
Each block must be a full mini-project with explicit file markers.
Keep it compact: prefer exactly two files total, avoid header guards, and avoid
large literal data tables unless they are essential to the bug.
Hard budget: keep the entire response under 170 total lines across both blocks.
Default shape: one graph.h + one kernels/*.cc file, one kernel only, stream
connections only, and a simple stream kernel unless the bug category requires
window semantics.

<BUGGY>
// FILE: graph.h
...full graph code...
// FILE: kernels/<name>.cc
...full kernel code...
</BUGGY>

<CORRECT>
// FILE: graph.h
...full graph code...
// FILE: kernels/<name>.cc
...full kernel code with exactly one minimal fix...
</CORRECT>

Checklist before you respond — verify each point:
[ ] I only used API calls from the APPROVED list in the system prompt.
[ ] I did NOT use any FORBIDDEN function names.
[ ] The BUGGY file has zero comments containing: bug/error/wrong/broken/incorrect/bad/fix/issue.
[ ] The bug is a real semantic error that would cause wrong output or deadlock, not a syntax error.
[ ] The CORRECT file changes the minimum number of tokens needed to fix the bug.
[ ] Each project block contains at least graph.h and kernels/*.cc file sections.
[ ] graph.h contains adf::kernel::create, adf::source, adf::connect, and adf::runtime<adf::ratio>.
[ ] graph.h declares one global graph object instance after the class definition.
[ ] graph.h forward-declares each kernel function used by adf::kernel::create(...).
[ ] graph.h does NOT #include any .cc file.
[ ] graph.h uses adf:: prefix on every ADF symbol — no unqualified names.
[ ] graph.h includes ONLY <adf.h>, no aie_api headers.
[ ] graph.h does NOT declare input_stream/output_stream members; those belong in kernel functions.
[ ] kernels/*.cc has #include <aie_api/aie.hpp> and <aie_api/aie_adf.hpp>.
[ ] aie::zeros has explicit template args: aie::zeros<acc48,8>().
[ ] I did NOT use aie::zeros<int16,N>() or acc.to_vector<T>() without a shift.
[ ] I did NOT use adf::kernel::create<func>() or old 5-argument aie::mac signatures.
[ ] I did NOT use aie::shuffle_up unless the bug category specifically requires it.
[ ] I used the default one-kernel stream-based shape unless the bug category truly requires something else.
[ ] If graph.h uses adf::connect<adf::window<...>>, the kernel uses input_window/output_window, not streams.
[ ] Stream parameters use pointer syntax: input_stream<T>* not input_stream<T>&.
[ ] adf::window<N> uses BYTES not sample count (256 int16 samples = window<512>).
[ ] Both projects are complete enough to build with AIE toolchain layout.
[ ] I chose my own values — I did NOT copy the example numbers from the bug category description.
[ ] I kept the entire response under 170 total lines.

Variant {variant_idx}: use a {variant_hint} kernel shape.
Graph topology: use a {topology_hint} graph structure.
"""

CORRECT_ONLY_TEMPLATE = """\
Create ONLY a correct, compilable AIE mini-project for this bug category. Do
not create the buggy version yet. The project will be compiled immediately with
Vitis AIE tools, so prefer the simplest legal implementation over variety.
Also make the correct code a clean baseline that can later be mutated into a
buggy version with one small source edit.

Bug category : {label}
Tier         : {tier}
Variant      : {variant_idx}

Respond with ONLY this XML block, no prose and no markdown fence:

<CORRECT>
// FILE: graph.h
...complete graph code...
// FILE: kernels/<name>.cc
...complete kernel code...
</CORRECT>

Hard requirements:
[ ] Use exactly two files: graph.h and one kernels/*.cc file.
[ ] Use one adf::graph class, one global graph object, one adf::kernel.
[ ] Use one adf::input_plio, one adf::output_plio, and adf::connect<adf::stream>.
[ ] Kernel signature uses input_stream<T>* and output_stream<T>* pointers.
[ ] graph.h forward-declares the kernel before adf::kernel::create(...).
[ ] graph.h declares one global graph instance after the class, for example: MyGraph g;.
[ ] graph.h includes only <adf.h>; kernels/*.cc includes <aie_api/aie.hpp> and <aie_api/aie_adf.hpp>.
[ ] The kernel body contains real stream I/O: readincr or readincr_v, and writeincr or writeincr_v.
[ ] Prefer scalar readincr/writeincr loops and scalar int32/int64 accumulators.
[ ] Avoid aie::accum/aie::zeros/aie::srs/acc.to_vector unless they are strictly required.
[ ] Do not use windows, RTPs, multiple kernels, aie::shuffle_up, or aie::zeros<T,N>().
[ ] Do not use adf::kernel::create<...>, unqualified ADF names, or input_stream<T>&.
[ ] Include one topic-relevant, explicit semantic choice that can later be changed locally.
[ ] Use named constants/intermediate variables for coefficients, bounds, phase, stride, state, or scaling.
[ ] Do not add comments that say what future bug to introduce.
[ ] Keep the block under 90 total lines.

Variant shape: {variant_hint}.
Graph topology: {topology_hint}.
{feedback}
"""

# Orthogonal axes the model can vary freely across.
# These are combined at prompt time to produce a virtually unlimited
# number of distinct kernel shapes — one per (interface x datatype x operation).
_INTERFACES = [
    "stream-based (input_stream<T>* / output_stream<T>*)",
    "stream-based (input_stream<T>* / output_stream<T>*) with scalar reads/writes",
    "stream-based (input_stream<T>* / output_stream<T>*) with plain vector reads/writes",
]

_GRAPH_TOPOLOGIES = [
    "single kernel, one PLIO in, one PLIO out",
    "single kernel, one PLIO in, one PLIO out with stream connections only",
]

_DATATYPES = [
    "int16", "int32", "uint16",
]

_OPERATIONS = [
    "FIR filter",
    "element-wise scale and shift",
    "running sum / prefix scan",
    "downsampling / decimation",
    "threshold / clipping",
    "moving average filter",
]


def _user_prompt(label: str, tier: str, variant_idx: int) -> str:
    """Build a user prompt with a unique kernel shape for every variant_idx.

    The shape is derived from four independent axes (interface, datatype,
    operation, topology) so the combination space is large enough to avoid
    before any repetition — far more than any practical variant count.
    """
    op_count = len(_OPERATIONS)
    dtype_count = len(_DATATYPES)
    iface_count = len(_INTERFACES)
    topology_count = len(_GRAPH_TOPOLOGIES)

    op = _OPERATIONS[variant_idx % op_count]
    dtype = _DATATYPES[(variant_idx // op_count) % dtype_count]
    iface = _INTERFACES[(variant_idx // (op_count * dtype_count)) % iface_count]
    topology = _GRAPH_TOPOLOGIES[(variant_idx // (op_count * dtype_count * iface_count)) % topology_count]
    variant_hint = f"{op} kernel using {dtype} data with a {iface} interface"
    topology_hint = topology
    return USER_TEMPLATE.format(
        label=label,
        tier=tier,
        variant_idx=variant_idx,
        variant_hint=variant_hint,
        topology_hint=topology_hint,
    )


def _correct_only_prompt(label: str, tier: str, variant_idx: int, feedback: str = "") -> str:
    op_count = len(_OPERATIONS)
    dtype_count = len(_DATATYPES)
    iface_count = len(_INTERFACES)
    topology_count = len(_GRAPH_TOPOLOGIES)

    op = _OPERATIONS[variant_idx % op_count]
    dtype = _DATATYPES[(variant_idx // op_count) % dtype_count]
    iface = _INTERFACES[(variant_idx // (op_count * dtype_count)) % iface_count]
    topology = _GRAPH_TOPOLOGIES[(variant_idx // (op_count * dtype_count * iface_count)) % topology_count]
    feedback_text = ""
    if feedback.strip():
        feedback_text = (
            "\nPrevious attempt failed validation. Fix the exact issue below and "
            "return a complete corrected <CORRECT> block only:\n"
            f"{feedback.strip()}\n"
        )
    return CORRECT_ONLY_TEMPLATE.format(
        label=label,
        tier=tier,
        variant_idx=variant_idx,
        variant_hint=f"{op} kernel using {dtype} data with a {iface} interface",
        topology_hint=topology,
        feedback=feedback_text,
    )


def load_topic_entries(topics_file: Path, tier: str = DEFAULT_TOPICS_TIER) -> list[dict]:
    """Load custom bug-topic labels from a newline-delimited text file.

    Uppercase heading lines become group names. Non-empty non-heading lines
    become taxonomy-like entries with stable slugs derived from the label.
    """
    from build_aie_instruction_dataset import slugify_bug_type

    entries: list[dict] = []
    slug_counts: dict[str, int] = {}
    group = "custom_topics"
    for raw_line in topics_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = line.lstrip("-*").strip()
        if not normalized:
            continue
        if normalized.upper() == normalized and re.search(r"[A-Z]", normalized):
            group = slugify_bug_type(normalized)
            continue

        base_slug = slugify_bug_type(normalized)
        index = slug_counts.get(base_slug, 0)
        slug_counts[base_slug] = index + 1
        slug = base_slug if index == 0 else f"{base_slug}_{index + 1}"
        entries.append({
            "slug": slug,
            "label": normalized,
            "tier": tier,
            "group": group,
        })
    return entries


def _strip_optional_fences(text: str) -> str:
    text = text.strip()
    for lang in ("```cpp", "```c++", "```c", "```"):
        text = text.removeprefix(lang).strip()
    return text.removesuffix("```").strip()


def parse_correct_project(text: str) -> tuple[str | None, list[str]]:
    """Extract a correct-only project and run the structural gate."""
    match = _CORRECT_RE.search(text)
    code = match.group(1).strip() if match else text.strip()
    code = _strip_optional_fences(code)
    if not code:
        return None, ["empty_correct_project"]
    ok, reasons = _full_code_requirements(code)
    if not ok:
        return None, reasons
    return code, []


# ---------------------------------------------------------------------------
# Bedrock client
# ---------------------------------------------------------------------------

def make_client(region: str):
    """Return a boto3 bedrock-runtime client.

    boto3 >= 1.35 reads AWS_BEARER_TOKEN_BEDROCK from the environment
    automatically and applies bearer-token auth to all Bedrock calls.
    No AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY needed when the env var
    is set.
    """
    import boto3
    token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
    if not token:
        raise RuntimeError(
            "AWS_BEARER_TOKEN_BEDROCK is not set. "
            "Export your Bedrock API key before running."
        )
    return boto3.client("bedrock-runtime", region_name=region)


def call_bedrock(
    client,
    model_id: str,
    user_text: str,
    temperature: float = 0.7,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> tuple[str, int, int]:
    """Call Bedrock Converse API. Returns (response_text, input_tokens, output_tokens)."""
    resp = client.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_text}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
    )
    content = resp["output"]["message"]["content"]
    text = "".join(block.get("text", "") for block in content)
    usage = resp.get("usage", {})
    return text, usage.get("inputTokens", 0), usage.get("outputTokens", 0)


def call_with_retry(client, model_id: str, user_text: str, max_tokens: int) -> tuple[str, int, int]:
    delay = RETRY_BASE_DELAY
    for attempt in range(MAX_RETRIES):
        try:
            return call_bedrock(client, model_id, user_text, max_tokens=max_tokens)
        except Exception as exc:
            msg = str(exc)
            if "ThrottlingException" in msg or "ServiceUnavailable" in msg:
                if attempt < MAX_RETRIES - 1:
                    print(f"      throttled, retrying in {delay:.0f}s …")
                    time.sleep(delay)
                    delay *= 2
                    continue
            raise
    raise RuntimeError("Max retries exceeded")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_BUGGY_RE = re.compile(r"<BUGGY>(.*?)</BUGGY>", re.DOTALL)
_CORRECT_RE = re.compile(r"<CORRECT>(.*?)</CORRECT>", re.DOTALL)


def parse_pair(text: str) -> tuple[str, str] | None:
    """Extract (buggy, correct) code from model response. Returns None on failure."""
    buggy_m = _BUGGY_RE.search(text)
    correct_m = _CORRECT_RE.search(text)
    if not buggy_m or not correct_m:
        return None
    buggy = buggy_m.group(1).strip()
    correct = correct_m.group(1).strip()
    # Strip markdown fences if model wrapped them anyway
    for lang in ("```cpp", "```c++", "```c", "```"):
        buggy = buggy.removeprefix(lang).strip()
        correct = correct.removeprefix(lang).strip()
    buggy = buggy.removesuffix("```").strip()
    correct = correct.removesuffix("```").strip()
    if not buggy or not correct or buggy == correct:
        return None
    # Sanity: both must look like C++ and satisfy full-project structural checks.
    for code in (buggy, correct):
        if not re.search(r"#include|void\s+\w|int\s+\w", code):
            return None
        full_ok, _ = _full_code_requirements(code)
        if not full_ok:
            return None
    return buggy, correct


# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

# Active Bedrock model pricing (USD per 1M tokens, Apr 2026)
_INPUT_COST_PER_1M = {
    "deepseek.v3.2": 0.55,
    "deepseek.r1-v1:0": 1.35,
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": 0.80,
    "anthropic.claude-haiku-4-5-20251001-v1:0": 0.80,
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": 3.00,
    "anthropic.claude-sonnet-4-5-20250929-v1:0": 3.00,
    "anthropic.claude-sonnet-4-6": 3.00,
    "anthropic.claude-opus-4-6-v1": 15.00,
}
_OUTPUT_COST_PER_1M = {
    "deepseek.v3.2": 1.10,
    "deepseek.r1-v1:0": 5.40,
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": 4.00,
    "anthropic.claude-haiku-4-5-20251001-v1:0": 4.00,
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": 15.00,
    "anthropic.claude-sonnet-4-5-20250929-v1:0": 15.00,
    "anthropic.claude-sonnet-4-6": 15.00,
    "anthropic.claude-opus-4-6-v1": 75.00,
}


def estimate_cost(model_id: str, in_tok: int, out_tok: int) -> float:
    ic = _INPUT_COST_PER_1M.get(model_id, 1.0)
    oc = _OUTPUT_COST_PER_1M.get(model_id, 5.0)
    return (in_tok * ic + out_tok * oc) / 1_000_000


def record_cost_run(history_path: Path, totals_path: Path, entry: dict) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    if totals_path.exists():
        try:
            totals = json.loads(totals_path.read_text(encoding="utf-8"))
        except Exception:
            totals = {}
    else:
        totals = {}

    totals.setdefault("runs", 0)
    totals.setdefault("total_input_tokens", 0)
    totals.setdefault("total_output_tokens", 0)
    totals.setdefault("total_estimated_cost_usd", 0.0)
    totals.setdefault("total_successes", 0)
    totals.setdefault("total_failures", 0)
    totals.setdefault("by_model", {})

    totals["runs"] += 1
    totals["total_input_tokens"] += int(entry.get("input_tokens", 0))
    totals["total_output_tokens"] += int(entry.get("output_tokens", 0))
    totals["total_estimated_cost_usd"] += float(entry.get("estimated_cost_usd", 0.0))
    totals["total_successes"] += int(entry.get("successes", 0))
    totals["total_failures"] += int(entry.get("failures", 0))

    model = str(entry.get("model") or "<unknown>")
    by_model = totals["by_model"]
    model_totals = by_model.setdefault(
        model,
        {
            "runs": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "successes": 0,
            "failures": 0,
        },
    )
    model_totals["runs"] += 1
    model_totals["input_tokens"] += int(entry.get("input_tokens", 0))
    model_totals["output_tokens"] += int(entry.get("output_tokens", 0))
    model_totals["estimated_cost_usd"] += float(entry.get("estimated_cost_usd", 0.0))
    model_totals["successes"] += int(entry.get("successes", 0))
    model_totals["failures"] += int(entry.get("failures", 0))

    totals["last_run"] = entry
    totals_path.write_text(json.dumps(totals, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_existing(output_file: Path) -> set[tuple[str, int]]:
    """Return set of (slug, variant_idx) already saved."""
    done: set[tuple[str, int]] = set()
    if not output_file.exists():
        return done
    with output_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                # Only count rows that parsed successfully, pass quality check,
                # and satisfy full-code project constraints.
                if obj.get("parse_ok") and obj.get("quality_ok", True) and obj.get("full_code_ok", False):
                    done.add((obj["slug"], obj["variant_idx"]))
            except Exception:
                pass
    return done


def load_bedrock_failure_pairs(
    output_file: Path,
    extra_gate_reasons: set[str] | None = None,
) -> set[tuple[str, int]]:
    """Return (slug, variant_idx) pairs that should be retried.

    Includes pairs that failed with a Bedrock/API error and optionally pairs
    where the structural gate rejected them with a reason in *extra_gate_reasons*
    (useful for retrying false-positive gate checks that have since been fixed).
    """
    retry: set[tuple[str, int]] = set()
    if not output_file.exists():
        return retry
    with output_file.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                pair = (obj["slug"], obj["variant_idx"])
                if obj.get("compile_ok") is True:
                    retry.discard(pair)
                    continue
                if "bedrock_error:" in line or "Bearer Token has expired" in line:
                    retry.add(pair)
                    continue
                if extra_gate_reasons:
                    gate_hits = set(
                        (obj.get("full_code_reasons") or {}).get("correct", [])
                    ) & extra_gate_reasons
                    if gate_hits:
                        retry.add(pair)
            except Exception:
                pass
    return retry


def append_row(output_file: Path, row: dict) -> None:
    # Auto-tag quality when we have a successfully parsed pair
    if row.get("parse_ok") and row.get("buggy") and row.get("correct"):
        row["quality_ok"] = _is_quality_ok(row["buggy"], row["correct"])
        buggy_ok, buggy_reasons = _full_code_requirements(row["buggy"])
        correct_ok, correct_reasons = _full_code_requirements(row["correct"])
        row["full_code_ok"] = buggy_ok and correct_ok
        row["full_code_reasons"] = {
            "buggy": buggy_reasons,
            "correct": correct_reasons,
        }
    else:
        row.setdefault("full_code_ok", False)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _workspace_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _validate_correct_project_wsl(
    code: str,
    *,
    wsl_distro: str,
    timeout_s: int,
) -> dict:
    """Compile one correct-only project with the real WSL/Vitis validator."""
    temp_dir = ROOT / "data" / "processed" / "v3" / ".bedrock_correct_validate_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    stem = f"candidate_{uuid4().hex}"
    input_path = temp_dir / f"{stem}.jsonl"
    out_path = temp_dir / f"{stem}_results.jsonl"

    input_row = {
        "instruction": "Compile-check Bedrock generated correct AIE mini-project.",
        "context": "",
        "response": f"```cpp\n{code.rstrip()}\n```",
        "metadata": {"source": "bedrock_correct_only_compile_probe"},
    }
    input_path.write_text(json.dumps(input_row, ensure_ascii=False) + "\n", encoding="utf-8")

    cmd = [
        "wsl",
        "-d",
        wsl_distro,
        "--",
        "bash",
        "scripts/run_validate_wsl.sh",
        "--input",
        _workspace_relative(input_path),
        "--out",
        _workspace_relative(out_path),
        "--scope",
        "correct",
        "--target",
        "AIE",
        "--workers",
        "1",
        "--timeout",
        str(timeout_s),
        "--limit",
        "1",
    ]
    try:
        completed = subprocess.run(
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
            "error_class": "validation_timeout",
            "return_code": -9,
            "stdout_tail": (exc.stdout or "")[-2000:],
            "stderr_tail": f"validation timeout after {timeout_s + 240}s\n{(exc.stderr or '')[-2000:]}",
        }

    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return json.loads(line)

    return {
        "compile_ok": False,
        "error_class": "validation_driver_error",
        "return_code": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def _validation_feedback(validation: dict, max_chars: int = 1600) -> str:
    text = "\n".join(
        part for part in [
            str(validation.get("error_class") or ""),
            str(validation.get("stderr_tail") or ""),
            str(validation.get("stdout_tail") or ""),
        ]
        if part
    )
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "error:" in stripped or "ERROR:" in stripped or "fatal error:" in stripped:
            lines.append(stripped)
        elif not lines and len(lines) < 4:
            lines.append(stripped)
        if len("\n".join(lines)) >= max_chars:
            break
    feedback = "\n".join(lines) if lines else text
    return feedback[:max_chars]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Bedrock model ID")
    ap.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION))
    ap.add_argument("--variants", type=int, default=DEFAULT_VARIANTS, help="Variants per slug")
    ap.add_argument("--slugs", nargs="+", metavar="SLUG", help="Only generate for these slugs")
    ap.add_argument("--topics-file", help="Newline-delimited custom bug-topic labels. Uppercase lines are treated as group headings.")
    ap.add_argument("--topics-tier", default=DEFAULT_TOPICS_TIER,
                    help="Tier assigned to rows loaded from --topics-file.")
    ap.add_argument("--resume", action="store_true", help="Skip (slug, variant) pairs already in output")
    ap.add_argument("--dry-run", action="store_true", help="Print prompt without calling Bedrock")
    ap.add_argument("--output", default=str(OUTPUT_FILE), help="Output JSONL path")
    ap.add_argument("--cost-history", default=str(COST_HISTORY_FILE),
                    help="Append-only JSONL file tracking Bedrock run cost over time.")
    ap.add_argument("--cost-totals", default=str(COST_TOTALS_FILE),
                    help="Cumulative JSON summary of Bedrock spend across runs.")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                    help="Bedrock max output tokens. Increase for thinking models if outputs truncate.")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help="Concurrent Bedrock requests (default 1 = serial). "
                         "Try 4-8 for Haiku; watch for ThrottlingException.")
    ap.add_argument("--compile-validated-correct", action="store_true",
                    help="Generate only the CORRECT project first and accept it only after WSL/Vitis compile validation passes.")
    ap.add_argument("--correct-attempts", type=int, default=3,
                    help="Bedrock attempts per row in --compile-validated-correct mode, including compiler-feedback retries.")
    ap.add_argument("--wsl-distro", default="Ubuntu-24.04",
                    help="WSL distro used for --compile-validated-correct validation.")
    ap.add_argument("--validate-timeout", type=int, default=60,
                    help="Per-candidate WSL/Vitis validation timeout in seconds.")
    ap.add_argument("--max-budget-usd", type=float, default=0.0,
                    help="Stop before starting another Bedrock request once this run's estimated spend reaches the cap. 0 disables the cap.")
    ap.add_argument("--retry-bedrock-failures-only", action="store_true",
                    help="Only schedule (slug, variant) pairs that previously failed with a Bedrock/API error and are not already compile-valid.")
    ap.add_argument("--retry-gate-reason", nargs="+", metavar="REASON",
                    help="Also retry pairs that were previously rejected by the structural gate with one of these reason strings (e.g. unsupported_broadcast_usage). Implies --retry-bedrock-failures-only loading logic.")
    args = ap.parse_args()

    output_path = Path(args.output)

    from build_aie_instruction_dataset import BUG_TAXONOMY_ENTRIES

    if args.topics_file:
        topics_path = Path(args.topics_file)
        if not topics_path.exists():
            raise FileNotFoundError(f"topics file not found: {topics_path}")
        entries = load_topic_entries(topics_path, tier=args.topics_tier)
        print(f"Loaded custom topic entries: {len(entries)} from {topics_path}")
    else:
        entries = BUG_TAXONOMY_ENTRIES
    if args.slugs:
        slug_set = set(args.slugs)
        entries = [e for e in entries if e["slug"] in slug_set]
        missing = slug_set - {e["slug"] for e in entries}
        if missing:
            print(f"WARNING: unknown slugs ignored: {missing}")

    if not entries:
        print("No entries to process.")
        return

    done = load_existing(output_path) if args.resume else set()
    extra_gate = set(args.retry_gate_reason) if getattr(args, "retry_gate_reason", None) else None
    use_retry_loader = args.retry_bedrock_failures_only or bool(extra_gate)
    retry_pairs = load_bedrock_failure_pairs(output_path, extra_gate_reasons=extra_gate) if use_retry_loader else None
    total_pairs = len(entries) * args.variants
    already_done = sum(1 for e in entries for v in range(args.variants) if (e["slug"], v) in done)
    remaining = total_pairs - already_done
    print(f"Slugs: {len(entries)}  Variants each: {args.variants}  "
          f"Total pairs: {total_pairs}  Already done: {already_done}  Remaining: {remaining}")
    if retry_pairs is not None:
        print(f"Retry filter: {len(retry_pairs)} Bedrock/API failure pair(s) eligible before resume filtering")

    if args.dry_run:
        e = entries[0]
        if args.compile_validated_correct:
            prompt = _correct_only_prompt(e["label"], e["tier"], 0)
        else:
            prompt = _user_prompt(e["label"], e["tier"], 0)
        print("\n--- SYSTEM PROMPT ---")
        print(SYSTEM_PROMPT)
        print("\n--- USER PROMPT (first slug) ---")
        print(prompt)
        return

    client = make_client(args.region)
    print(f"Model: {args.model}  Region: {args.region}  Workers: {args.workers}\n")

    total_in = total_out = 0
    total_cost = 0.0
    successes = failures = 0

    # Build the work queue
    jobs = []
    for idx_e, entry in enumerate(entries):
        for v in range(args.variants):
            if retry_pairs is not None and (entry["slug"], v) not in retry_pairs:
                continue
            if args.resume and (entry["slug"], v) in done:
                continue
            jobs.append((idx_e, entry, v))

    if not jobs:
        print("Nothing to do.")
        return

    print(f"Dispatching {len(jobs)} jobs across {args.workers} worker(s)\n")

    if args.compile_validated_correct:
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed

        print("Mode: compile-validated CORRECT-only generation")
        print("Each accepted row has passed scripts/run_validate_wsl.sh with --scope correct.\n")
        if args.max_budget_usd > 0:
            print(f"Budget cap: ${args.max_budget_usd:.2f} estimated Bedrock spend for this run.\n")

        write_lock = threading.Lock()
        counter_lock = threading.Lock()
        budget_lock = threading.Lock()
        completed = 0
        budget_stopped = False

        def process_compile_validated_job(indexed_job):
            nonlocal total_in, total_out, total_cost

            job_index, job = indexed_job
            idx_e, entry, v = job
            slug = entry["slug"]
            label = entry["label"]
            tier = entry["tier"]
            feedback = ""
            last_text = ""
            last_reasons: list[str] = []
            accepted = False

            for attempt in range(1, max(1, args.correct_attempts) + 1):
                with budget_lock:
                    current_cost = total_cost
                if args.max_budget_usd > 0 and current_cost >= args.max_budget_usd:
                    return {
                        "status": "budget",
                        "job_index": job_index,
                        "slug": slug,
                        "variant_idx": v,
                        "attempt": attempt,
                        "cost": current_cost,
                    }

                prompt = _correct_only_prompt(label, tier, v, feedback=feedback)
                try:
                    text, in_tok, out_tok = call_with_retry(client, args.model, prompt, args.max_tokens)
                except Exception as exc:
                    last_reasons = [f"bedrock_error: {exc}"]
                    break

                last_text = text
                cost = estimate_cost(args.model, in_tok, out_tok)
                with budget_lock:
                    total_in += in_tok
                    total_out += out_tok
                    total_cost += cost

                correct, reasons = parse_correct_project(text)
                if correct is None:
                    last_reasons = reasons
                    feedback = "Structural validation failed: " + ", ".join(reasons)
                    continue

                validation = _validate_correct_project_wsl(
                    correct,
                    wsl_distro=args.wsl_distro,
                    timeout_s=args.validate_timeout,
                )
                if validation.get("compile_ok"):
                    row = {
                        "slug": slug,
                        "label": label,
                        "tier": tier,
                        "variant_idx": v,
                        "buggy": None,
                        "correct": correct,
                        "correct_only": True,
                        "model": args.model,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "parse_ok": True,
                        "quality_ok": True,
                        "full_code_ok": True,
                        "full_code_reasons": {"correct": []},
                        "compile_ok": True,
                        "compile_validation": validation,
                        "compile_validated_attempt": attempt,
                    }
                    with write_lock:
                        append_row(output_path, row)
                    accepted = True
                    return {
                        "status": "ok",
                        "job_index": job_index,
                        "slug": slug,
                        "variant_idx": v,
                        "attempt": attempt,
                    }

                feedback = _validation_feedback(validation)
                last_reasons = [validation.get("error_class") or "compile_error"]

            if not accepted:
                row = {
                    "slug": slug,
                    "label": label,
                    "tier": tier,
                    "variant_idx": v,
                    "buggy": None,
                    "correct": None,
                    "correct_only": True,
                    "raw_response": last_text[:8000],
                    "model": args.model,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "parse_ok": False,
                    "full_code_ok": False,
                    "full_code_reasons": {"correct": last_reasons},
                    "compile_ok": False,
                    "validation_feedback": feedback,
                }
                with write_lock:
                    append_row(output_path, row)
                return {
                    "status": "fail",
                    "job_index": job_index,
                    "slug": slug,
                    "variant_idx": v,
                    "reasons": last_reasons,
                }

            return {
                "status": "fail",
                "job_index": job_index,
                "slug": slug,
                "variant_idx": v,
                "reasons": last_reasons,
            }

        indexed_jobs = list(enumerate(jobs, 1))
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(process_compile_validated_job, indexed_job): indexed_job for indexed_job in indexed_jobs}
            for fut in as_completed(futures):
                result = fut.result()
                with counter_lock:
                    completed += 1
                    status = result["status"]
                    slug = result["slug"]
                    variant_idx = result["variant_idx"]
                    if status == "ok":
                        successes += 1
                        print(
                            f"[{completed}/{len(jobs)}] {slug} v={variant_idx}  COMPILES  "
                            f"attempt={result['attempt']}"
                        )
                    elif status == "budget":
                        budget_stopped = True
                        print(
                            f"[{completed}/{len(jobs)}] {slug} v={variant_idx}  STOPPED  "
                            f"budget cap reached (${result['cost']:.4f})"
                        )
                    else:
                        failures += 1
                        print(
                            f"[{completed}/{len(jobs)}] {slug} v={variant_idx}  NO COMPILE  "
                            f"reasons={result.get('reasons', [])[:3]}"
                        )

        if budget_stopped:
            print("Budget cap reached; remaining queued jobs were skipped before making new Bedrock requests.")

        print(f"\n--- Summary ---")
        print(f"Compile-validated correct rows: {successes}  Failures: {failures}")
        print(f"Tokens: {total_in:,} in / {total_out:,} out")
        print(f"Estimated cost: ${total_cost:.4f}")
        print(f"Output: {output_path}")
        record_cost_run(
            Path(args.cost_history),
            Path(args.cost_totals),
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "model": args.model,
                "region": args.region,
                "workers": 1,
                "temperature": args.temperature,
                "max_tokens": args.max_tokens,
                "output_path": str(output_path),
                "mode": "compile_validated_correct",
                "slugs_requested": len(entries),
                "variants_per_slug": args.variants,
                "jobs_attempted": len(jobs),
                "budget_stopped": budget_stopped,
                "successes": successes,
                "failures": failures,
                "input_tokens": total_in,
                "output_tokens": total_out,
                "estimated_cost_usd": round(total_cost, 6),
            },
        )
        print(f"Cost history: {args.cost_history}")
        print(f"Cost totals : {args.cost_totals}")
        return

    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    write_lock = threading.Lock()
    counter_lock = threading.Lock()
    completed = [0]

    def process_one(job):
        idx_e, entry, v = job
        slug = entry["slug"]
        label = entry["label"]
        tier = entry["tier"]
        prompt = _user_prompt(label, tier, v)
        try:
            text, in_tok, out_tok = call_with_retry(client, args.model, prompt, args.max_tokens)
        except Exception as exc:
            return ("error", idx_e, slug, v, 0, 0, str(exc))

        pair = parse_pair(text)
        if pair is None:
            row = {
                "slug": slug, "label": label, "tier": tier, "variant_idx": v,
                "buggy": None, "correct": None,
                "raw_response": text[:2000],
                "model": args.model, "ts": datetime.now(timezone.utc).isoformat(),
                "input_tokens": in_tok, "output_tokens": out_tok, "parse_ok": False,
            }
            with write_lock:
                append_row(output_path, row)
            return ("parse_fail", idx_e, slug, v, in_tok, out_tok, None)

        buggy, correct = pair
        row = {
            "slug": slug, "label": label, "tier": tier, "variant_idx": v,
            "buggy": buggy, "correct": correct,
            "model": args.model, "ts": datetime.now(timezone.utc).isoformat(),
            "input_tokens": in_tok, "output_tokens": out_tok, "parse_ok": True,
        }
        with write_lock:
            append_row(output_path, row)
        return ("ok", idx_e, slug, v, in_tok, out_tok, None)

    if args.workers <= 1:
        # Serial path (preserves original behavior)
        for job in jobs:
            status, idx_e, slug, v, in_tok, out_tok, err = process_one(job)
            with counter_lock:
                completed[0] += 1
                total_in += in_tok
                total_out += out_tok
                cost = estimate_cost(args.model, in_tok, out_tok)
                total_cost += cost
                if status == "ok":
                    successes += 1
                    print(f"[{completed[0]}/{len(jobs)}] {slug} v={v}  OK  "
                          f"({in_tok}in/{out_tok}out  ${cost:.4f})  total=${total_cost:.2f}")
                elif status == "parse_fail":
                    failures += 1
                    print(f"[{completed[0]}/{len(jobs)}] {slug} v={v}  PARSE FAIL  "
                          f"({in_tok}in/{out_tok}out  ${cost:.4f})")
                else:
                    failures += 1
                    print(f"[{completed[0]}/{len(jobs)}] {slug} v={v}  ERROR: {err}")
    else:
        # Parallel path
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(process_one, j): j for j in jobs}
            for fut in as_completed(futures):
                status, idx_e, slug, v, in_tok, out_tok, err = fut.result()
                with counter_lock:
                    completed[0] += 1
                    total_in += in_tok
                    total_out += out_tok
                    cost = estimate_cost(args.model, in_tok, out_tok)
                    total_cost += cost
                    if status == "ok":
                        successes += 1
                        print(f"[{completed[0]}/{len(jobs)}] {slug} v={v}  OK  "
                              f"({in_tok}in/{out_tok}out  ${cost:.4f})  total=${total_cost:.2f}")
                    elif status == "parse_fail":
                        failures += 1
                        print(f"[{completed[0]}/{len(jobs)}] {slug} v={v}  PARSE FAIL  "
                              f"({in_tok}in/{out_tok}out  ${cost:.4f})")
                    else:
                        failures += 1
                        print(f"[{completed[0]}/{len(jobs)}] {slug} v={v}  ERROR: {err}")

    print(f"\n--- Summary ---")
    print(f"Successes: {successes}  Failures: {failures}")
    print(f"Tokens: {total_in:,} in / {total_out:,} out")
    print(f"Estimated cost: ${total_cost:.4f}")
    print(f"Output: {output_path}")

    record_cost_run(
        Path(args.cost_history),
        Path(args.cost_totals),
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "region": args.region,
            "workers": args.workers,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "output_path": str(output_path),
            "slugs_requested": len(entries),
            "variants_per_slug": args.variants,
            "jobs_attempted": len(jobs),
            "successes": successes,
            "failures": failures,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "estimated_cost_usd": round(total_cost, 6),
        },
    )

    print(f"Cost history: {args.cost_history}")
    print(f"Cost totals : {args.cost_totals}")
    if failures:
        print(f"\nRe-run with --resume to retry failed rows.")


if __name__ == "__main__":
    main()
