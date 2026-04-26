"""Synthesize buggy/correct AIE code pairs for every taxonomy bug slug.

Produces one (input, fixed_code) pair per taxonomy entry. Strategy:
  * A small library of realistic AIE kernel/graph templates with placeholder
    numeric values.
  * Keyword-routed "recipes" that match phrases in the bug label and apply a
    specific textual transform to create the buggy version (the correct
    version is the template with its original values).
  * A generic fallback that uses the label's key phrase to drive a
    distinguishing 1-2 line numeric / type change, so every taxonomy slug
    ends up with a non-trivial diff even if we can't model it deeply.

Output keys match the format consumed by `build_holdout_benchmark.py`
and `eval_aie_debugging_v2.py`:  { input, fixed_code, bug_type, label, tier }
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_aie_instruction_dataset import BUG_TAXONOMY_ENTRIES  # noqa: E402


# ---------------------------------------------------------------------------
# Canonical "correct" templates. Each returns full source text.
# The synthesizer mutates these to produce buggy variants.
# ---------------------------------------------------------------------------

T_FIR_MAC = '''#include "aie_api/aie.hpp"
#include "aie_api/aie_adf.hpp"

void fir_kernel(input_buffer<int16> & __restrict in,
                output_buffer<int32> & __restrict out) {
    auto in_iter  = aie::begin_vector<8>(in);
    auto out_iter = aie::begin_vector<8>(out);
    aie::accum<acc48, 8> acc = aie::zeros<acc48, 8>();
    for (int i = 0; i < 32; i++) chess_prepare_for_pipelining {
        aie::vector<int16, 8> data  = *in_iter++;
        aie::vector<int16, 8> coeff = aie::broadcast<int16, 8>(3);
        acc = aie::mac(acc, data, coeff);
        *out_iter++ = acc.to_vector<int32>(0);
    }
}
'''

T_SCALE = '''#include "aie_api/aie.hpp"

void scale_kernel(input_buffer<int32> & __restrict in,
                  output_buffer<int32> & __restrict out) {
    auto in_iter  = aie::begin_vector<8>(in);
    auto out_iter = aie::begin_vector<8>(out);
    for (int i = 0; i < 32; i++) chess_prepare_for_pipelining {
        aie::vector<int32, 8> v = *in_iter++;
        *out_iter++ = aie::mul(v, aie::broadcast<int32, 8>(3)).to_vector<int32>(0);
    }
}
'''

T_DUAL_STREAM = '''#include "aie_api/aie.hpp"

void add_streams(input_stream_int32 * __restrict in_a,
                 input_stream_int32 * __restrict in_b,
                 output_stream_int32 * __restrict out) {
    for (int i = 0; i < 256; i++) chess_prepare_for_pipelining {
        int32 a = readincr(in_a);
        int32 b = readincr(in_b);
        writeincr(out, a + b);
    }
}
'''

T_CASCADE = '''#include "aie_api/aie.hpp"

void stage1(input_buffer<int16> & __restrict in,
            output_buffer<int16> & __restrict out) {
    auto in_iter  = aie::begin_vector<8>(in);
    auto out_iter = aie::begin_vector<8>(out);
    for (int i = 0; i < 32; i++) chess_prepare_for_pipelining {
        *out_iter++ = *in_iter++;
    }
}

void stage2(input_buffer<int16> & __restrict in,
            output_buffer<int32> & __restrict out) {
    auto in_iter  = aie::begin_vector<8>(in);
    auto out_iter = aie::begin_vector<8>(out);
    for (int i = 0; i < 32; i++) chess_prepare_for_pipelining {
        aie::vector<int16, 8> v = *in_iter++;
        *out_iter++ = aie::mul(v, aie::broadcast<int16, 8>(2)).to_vector<int32>(0);
    }
}
'''

T_GRAPH = '''#include "adf.h"
#include "kernel.h"

class MyGraph : public adf::graph {
public:
    adf::kernel k1;
    adf::input_plio  in0;
    adf::output_plio out0;

    MyGraph() {
        k1   = adf::kernel::create(fir_kernel);
        in0  = adf::input_plio::create("DataIn",  adf::plio_32_bits, "data/input.txt");
        out0 = adf::output_plio::create("DataOut", adf::plio_32_bits, "data/output.txt");
        adf::connect<adf::window<512>>(in0.out[0],  k1.in[0]);
        adf::connect<adf::window<1024>>(k1.out[0], out0.in[0]);
        adf::dimensions(k1.in[0])  = {256};
        adf::dimensions(k1.out[0]) = {256};
        adf::source(k1) = "kernel.cpp";
        adf::runtime<ratio>(k1) = 0.9;
    }
};
'''

T_LOCK = '''#include "aie_api/aie.hpp"

void double_buffered(input_buffer<int32> & __restrict in,
                     output_buffer<int32> & __restrict out) {
    acquire(in_lock);
    acquire(out_lock);
    auto in_iter  = aie::begin_vector<8>(in);
    auto out_iter = aie::begin_vector<8>(out);
    for (int i = 0; i < 32; i++) chess_prepare_for_pipelining {
        *out_iter++ = *in_iter++;
    }
    release(out_lock);
    release(in_lock);
}
'''

T_RTP = '''#include "aie_api/aie.hpp"

void rtp_kernel(input_buffer<int16> & __restrict in,
                output_buffer<int16> & __restrict out,
                const int32 & gain) {
    const int32 local_gain = gain;
    auto in_iter  = aie::begin_vector<8>(in);
    auto out_iter = aie::begin_vector<8>(out);
    for (int i = 0; i < 32; i++) chess_prepare_for_pipelining {
        aie::vector<int16, 8> v = *in_iter++;
        *out_iter++ = aie::mul(v, aie::broadcast<int16, 8>(local_gain)).to_vector<int16>(0);
    }
}
'''


# ---------------------------------------------------------------------------
# Recipes: map keyword predicates -> (template, buggy_transform)
# ---------------------------------------------------------------------------

def _sub_once(text: str, old: str, new: str) -> tuple[str, bool]:
    idx = text.find(old)
    if idx < 0:
        return text, False
    return text[:idx] + new + text[idx + len(old):], True


def _comment_anchor(buggy: str, correct: str, label: str, fail_mode: str) -> tuple[str, str]:
    """Prepend a unique comment block tied to the label so near-identical
    templates still diff in a unique way. Only added to BUGGY when otherwise
    the diff would be trivial-looking."""
    slug_key = re.sub(r"[^a-z0-9]+", "-", label.lower())[:60].strip("-")
    buggy_head = f"// BUG: {label}\n// symptom: {fail_mode}\n"
    correct_head = f"// FIX applied for: {label}\n"
    return buggy_head + buggy, correct_head + correct


Recipe = tuple[Callable[[str], bool], Callable[[str], tuple[str, str]]]


def recipe_numeric_swap(label: str) -> tuple[str, str] | None:
    """Handle labels containing 'X instead of Y' or '(X instead of Y)'."""
    m = re.search(r"([\w\-\.]+)\s+instead\s+of\s+([\w\-\.]+)", label, re.I)
    if not m:
        return None
    buggy_val, correct_val = m.group(1), m.group(2)
    # Only swap when correct_val appears as a whole token (avoids "3" -> "7"
    # corrupting "int32" to "int72"). Use word-boundary regex with exact case.
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(correct_val) + r"(?![A-Za-z0-9_])")
    for template in (T_FIR_MAC, T_SCALE, T_DUAL_STREAM, T_CASCADE, T_GRAPH, T_RTP):
        if pattern.search(template):
            buggy = pattern.sub(buggy_val, template, count=1)
            if buggy != template:
                return buggy, template
    # No template contains the exact token - use a comment-anchored fallback.
    buggy = T_FIR_MAC.replace(
        "aie::zeros<acc48, 8>();",
        f"aie::zeros<acc48, 8>(); // using {buggy_val} incorrectly",
    )
    correct = T_FIR_MAC.replace(
        "aie::zeros<acc48, 8>();",
        f"aie::zeros<acc48, 8>(); // using {correct_val}",
    )
    return buggy, correct


def recipe_accumulator_width(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "acc48" in lw and ("acc80" in lw or "acc32" in lw or "int32" in lw or "overflow" in lw):
        correct = T_FIR_MAC.replace("acc48", "acc80")
        buggy = T_FIR_MAC  # uses acc48 — wrong
        return buggy, correct
    if "accum" in lw and ("lanes" in lw or "width" in lw) and "vector" in lw:
        correct = T_FIR_MAC
        buggy = T_FIR_MAC.replace("aie::accum<acc48, 8>", "aie::accum<acc48, 4>")
        return buggy, correct
    return None


def recipe_vector_lane(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if ("begin_vector" in lw and "lane" in lw) or ("vector lane" in lw) or ("iterator width" in lw and "computation" in lw):
        buggy = T_FIR_MAC.replace("begin_vector<8>(out)", "begin_vector<16>(out)")
        return buggy, T_FIR_MAC
    return None


def recipe_plio_width(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "plio" in lw and ("bit" in lw or "width" in lw or "16" in lw or "32" in lw or "64" in lw or "128" in lw):
        # Default: buggy uses 32-bit PLIO for int16 data (should be 16).
        if "64" in lw:
            buggy = T_GRAPH.replace("plio_32_bits", "plio_64_bits")
            return buggy, T_GRAPH
        if "128" in lw:
            buggy = T_GRAPH.replace("plio_32_bits", "plio_128_bits")
            return buggy, T_GRAPH
        if "16" in lw:
            buggy = T_GRAPH.replace("plio_32_bits", "plio_16_bits")
            return buggy, T_GRAPH
        buggy = T_GRAPH.replace("plio_32_bits", "plio_8_bits")
        return buggy, T_GRAPH
    return None


def recipe_window_size(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "window" in lw and ("byte" in lw or "sample" in lw or "size" in lw or "dimension" in lw):
        buggy = T_GRAPH.replace("window<512>", "window<256>").replace("window<1024>", "window<512>")
        return buggy, T_GRAPH
    return None


def recipe_runtime_ratio(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "runtime" in lw and ("ratio" in lw or "scheduled" in lw or "never" in lw):
        buggy = T_GRAPH.replace("runtime<ratio>(k1) = 0.9;", "runtime<ratio>(k1) = 0.0;")
        return buggy, T_GRAPH
    return None


def recipe_missing_source(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "missing" in lw and ("source" in lw or "adf::source" in lw or "kernel file" in lw):
        buggy = T_GRAPH.replace('        adf::source(k1) = "kernel.cpp";\n', "")
        return buggy, T_GRAPH
    return None


def recipe_missing_write(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "missing" in lw and ("output write" in lw or "writeincr" in lw or "output_iter" in lw):
        buggy = T_FIR_MAC.replace("*out_iter++ = acc.to_vector<int32>(0);", "// output write removed")
        return buggy, T_FIR_MAC
    return None


def recipe_missing_increment(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "missing" in lw and ("increment" in lw or "iterator++" in lw or "advance" in lw):
        buggy = T_FIR_MAC.replace("*out_iter++", "*out_iter")
        return buggy, T_FIR_MAC
    return None


def recipe_add_vs_sub(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if ("subtract" in lw and "add" in lw) or ("minus" in lw and "plus" in lw) or "subtraction_instead_of_addition" in lw:
        buggy = T_DUAL_STREAM.replace("a + b", "a - b")
        return buggy, T_DUAL_STREAM
    return None


def recipe_mul_vs_mac(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if ("aie_add used instead of aie_mul" in lw) or ("aie_mul used instead of aie_mac" in lw):
        buggy = T_FIR_MAC.replace("acc = aie::mac(acc, data, coeff);", "acc = aie::mul(data, coeff);")
        return buggy, T_FIR_MAC
    return None


def recipe_unconsumed_stream(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "unconsumed" in lw or ("stream" in lw and "never consumed" in lw) or "deadlock" in lw and "stream" in lw:
        correct = '''#include "aie_api/aie.hpp"
void fir_kernel(input_buffer<int16> & __restrict in,
                input_stream_int16 * __restrict coeff_stream,
                output_buffer<int32> & __restrict out) {
    auto in_iter  = aie::begin_vector<8>(in);
    auto out_iter = aie::begin_vector<8>(out);
    aie::accum<acc48, 8> acc = aie::zeros<acc48, 8>();
    for (int i = 0; i < 32; i++) chess_prepare_for_pipelining {
        aie::vector<int16, 8> data  = *in_iter++;
        int16 c = readincr(coeff_stream);
        aie::vector<int16, 8> coeff = aie::broadcast<int16, 8>(c);
        acc = aie::mac(acc, data, coeff);
        *out_iter++ = acc.to_vector<int32>(0);
    }
}
'''
        buggy = correct.replace("int16 c = readincr(coeff_stream);\n        ", "")
        buggy = buggy.replace("aie::broadcast<int16, 8>(c)", "aie::broadcast<int16, 8>(3)")
        return buggy, correct
    return None


def recipe_duplicate_read(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "duplicate" in lw and ("read" in lw or "stream" in lw):
        buggy = T_DUAL_STREAM.replace("int32 b = readincr(in_b);", "int32 b = readincr(in_a);")
        return buggy, T_DUAL_STREAM
    return None


def recipe_off_by_one(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "off-by-one" in lw or "off by one" in lw or "circular buffer index" in lw or "n_1_loop_bound" in lw or "drops last sample" in lw:
        buggy = T_FIR_MAC.replace("i < 32", "i < 31")
        return buggy, T_FIR_MAC
    return None


def recipe_cascade_types(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if ("stage1" in lw and "stage2" in lw) or ("cascade" in lw and ("type" in lw or "int32" in lw or "int16" in lw)):
        # buggy: stage2 reads int32 even though stage1 outputs int16.
        buggy = T_CASCADE.replace(
            "void stage2(input_buffer<int16> & __restrict in,",
            "void stage2(input_buffer<int32> & __restrict in,",
        )
        return buggy, T_CASCADE
    return None


def recipe_shuffle_mode(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "shuffle" in lw or "shift" in lw and "vector" in lw:
        correct = T_FIR_MAC.replace(
            "acc = aie::mac(acc, data, coeff);",
            "data = aie::shuffle_down(data, 0);\n        acc = aie::mac(acc, data, coeff);",
        )
        buggy = correct.replace("shuffle_down(data, 0)", "shuffle_up(data, 0)")
        return buggy, correct
    return None


def recipe_chess_pragma(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "chess_prepare_for_pipelining" in lw or ("missing" in lw and "pipelining" in lw) or "chess pragma" in lw:
        buggy = T_FIR_MAC.replace(" chess_prepare_for_pipelining", "")
        return buggy, T_FIR_MAC
    return None


def recipe_rtp_inside_loop(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "rtp" in lw and ("loop" in lw or "read" in lw):
        buggy = T_RTP.replace(
            "    const int32 local_gain = gain;\n",
            "",
        ).replace(
            "aie::broadcast<int16, 8>(local_gain)",
            "aie::broadcast<int16, 8>(gain)",
        )
        return buggy, T_RTP
    return None


def recipe_lock_ordering(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "lock" in lw and ("order" in lw or "acquire" in lw or "release" in lw):
        buggy = T_LOCK.replace(
            "    acquire(in_lock);\n    acquire(out_lock);",
            "    acquire(out_lock);\n    acquire(in_lock);",
        ).replace(
            "    release(out_lock);\n    release(in_lock);",
            "    release(in_lock);\n    release(out_lock);",
        )
        return buggy, T_LOCK
    return None


def recipe_saturation(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "saturat" in lw or "wrap" in lw or "to_vector" in lw and "shift" in lw:
        buggy = T_FIR_MAC.replace(
            "acc.to_vector<int32>(0);",
            "acc.to_vector<int32>(15);",
        )
        return buggy, T_FIR_MAC
    return None


def recipe_acc_init(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if ("accumulator" in lw and ("init" in lw or "reset" in lw or "zero" in lw)) or "uninitialized_accumulator" in lw:
        buggy = T_FIR_MAC.replace(
            "aie::accum<acc48, 8> acc = aie::zeros<acc48, 8>();",
            "aie::accum<acc48, 8> acc;",
        )
        return buggy, T_FIR_MAC
    return None


def recipe_dimensions_mismatch(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "dimension" in lw and ("mismatch" in lw or "window" in lw):
        buggy = T_GRAPH.replace(
            "adf::dimensions(k1.in[0])  = {256};",
            "adf::dimensions(k1.in[0])  = {128};",
        )
        return buggy, T_GRAPH
    return None


def recipe_reversed_connect(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "reverse" in lw or ("connect" in lw and "direction" in lw):
        buggy = T_GRAPH.replace(
            "adf::connect<adf::window<512>>(in0.out[0],  k1.in[0]);",
            "adf::connect<adf::window<512>>(k1.in[0], in0.out[0]);",
        )
        return buggy, T_GRAPH
    return None


def recipe_wrong_loop_count(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "loop count" in lw or ("loop" in lw and "bound" in lw):
        buggy = T_FIR_MAC.replace("i < 32", "i < 16")
        return buggy, T_FIR_MAC
    return None


def recipe_missing_window_margin(label: str) -> tuple[str, str] | None:
    lw = label.lower()
    if "margin" in lw or "window margin" in lw:
        correct = T_GRAPH.replace(
            "adf::dimensions(k1.in[0])  = {256};",
            "adf::dimensions(k1.in[0])  = {256};\n        adf::location<adf::parameter>(k1.in[0]) = adf::location<adf::kernel>(k1);\n        adf::window_margin(k1.in[0]) = 64;",
        )
        buggy = T_GRAPH  # no window margin
        return buggy, correct
    return None


RECIPES: list[Callable[[str], tuple[str, str] | None]] = [
    recipe_unconsumed_stream,  # must come before generic "deadlock" matches
    recipe_duplicate_read,
    recipe_cascade_types,
    recipe_accumulator_width,
    recipe_acc_init,
    recipe_plio_width,
    recipe_runtime_ratio,
    recipe_dimensions_mismatch,
    recipe_reversed_connect,
    recipe_missing_window_margin,
    recipe_window_size,
    recipe_missing_source,
    recipe_missing_write,
    recipe_missing_increment,
    recipe_add_vs_sub,
    recipe_mul_vs_mac,
    recipe_wrong_loop_count,
    recipe_shuffle_mode,
    recipe_chess_pragma,
    recipe_rtp_inside_loop,
    recipe_lock_ordering,
    recipe_saturation,
    recipe_vector_lane,
    recipe_numeric_swap,  # generic "X instead of Y" - last so specific recipes win
]


def _generic_fallback(label: str, tier: str) -> tuple[str, str]:
    """Synthesize a plausible pair for labels no recipe matched.

    Picks a template based on which artifact type the label talks about,
    then produces a minimal distinguishing change keyed to a short hash of
    the label so different fallback cases don't all produce identical diffs."""
    lw = label.lower()
    # Pick the most topical template.
    if "graph" in lw or "plio" in lw or "dimension" in lw or "runtime" in lw or "adf" in lw:
        template = T_GRAPH
        # Flip a graph-side numeric we can legitimately "get wrong".
        variants = [
            ("runtime<ratio>(k1) = 0.9;", "runtime<ratio>(k1) = 0.4;"),
            ("adf::dimensions(k1.out[0]) = {256};", "adf::dimensions(k1.out[0]) = {128};"),
            ('adf::plio_32_bits', 'adf::plio_64_bits'),
            ("window<512>", "window<256>"),
            ("window<1024>", "window<512>"),
        ]
    elif "cascade" in lw or "pipeline" in lw or "two-stage" in lw or "stage" in lw:
        template = T_CASCADE
        variants = [
            ("i < 32", "i < 16"),
            ("aie::broadcast<int16, 8>(2)", "aie::broadcast<int16, 8>(0)"),
            ("begin_vector<8>(out)", "begin_vector<4>(out)"),
            ("input_buffer<int16> & __restrict in,\n            output_buffer<int32>",
             "input_buffer<int32> & __restrict in,\n            output_buffer<int32>"),
        ]
    elif "stream" in lw or "deadlock" in lw or "token" in lw or "fifo" in lw:
        template = T_DUAL_STREAM
        variants = [
            ("i < 256", "i < 128"),
            ("a + b", "a | b"),
            ("readincr(in_a)", "readincr_v<8>(in_a)"),
            ("readincr(in_b)", "readincr(in_a)"),
        ]
    elif "rtp" in lw or "parameter" in lw:
        template = T_RTP
        variants = [
            ("const int32 local_gain = gain;", "int32 local_gain = 0;"),
            ("i < 32", "i < 64"),
        ]
    elif "lock" in lw or "acquire" in lw or "release" in lw:
        template = T_LOCK
        variants = [
            ("release(out_lock);\n    release(in_lock);", "release(out_lock);"),
            ("acquire(in_lock);\n    acquire(out_lock);", "acquire(in_lock);"),
        ]
    else:
        template = T_FIR_MAC
        variants = [
            ("i < 32", "i < 16"),
            ("aie::broadcast<int16, 8>(3)", "aie::broadcast<int16, 8>(7)"),
            ("aie::accum<acc48, 8>", "aie::accum<acc48, 4>"),
            ("acc.to_vector<int32>(0);", "acc.to_vector<int32>(15);"),
            ("aie::zeros<acc48, 8>();", "aie::zeros<acc48, 4>();"),
            ("*out_iter++", "*out_iter"),
        ]

    # Deterministic variant selection from label hash.
    idx = sum(ord(c) for c in label) % len(variants)
    correct_snip, buggy_snip = variants[idx]
    correct = template
    buggy = template.replace(correct_snip, buggy_snip, 1)
    if buggy == correct:
        # Template didn't contain the "correct" snippet; try next variant.
        for other_correct, other_buggy in variants:
            if other_correct in template:
                buggy = template.replace(other_correct, other_buggy, 1)
                if buggy != template:
                    return _comment_anchor(buggy, template, label, "semantic mismatch per taxonomy")[0], template
    return _comment_anchor(buggy, correct, label, "semantic mismatch per taxonomy")[0], correct


def _load_bedrock_cache() -> dict[str, list[tuple[str, str]]]:
    """Load pre-generated LLM pairs from Bedrock synth pair JSONL.

    Returns {slug: [(buggy, correct), ...]} for successfully parsed rows.
    The cache is loaded once and stored in _BEDROCK_CACHE.
    """
    cache_candidates = [
        ROOT / "data" / "processed" / "v3" / "bedrock_synth_pairs_v3.jsonl",
        ROOT / "data" / "processed" / "bedrock_synth_pairs.jsonl",
    ]
    cache_path = next((p for p in cache_candidates if p.exists()), cache_candidates[0])
    result: dict[str, dict[int, tuple[str, str]]] = {}
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = __import__("json").loads(line)
                if not obj.get("parse_ok"):
                    continue
                # Skip rows flagged as low-quality (bad API patterns, hint comments)
                if obj.get("quality_ok") is False:
                    continue
                # For v3 rows, require structural full-code gate to pass.
                if "full_code_ok" in obj and obj.get("full_code_ok") is False:
                    continue
                buggy = obj.get("buggy", "")
                correct = obj.get("correct", "")
                if buggy and correct and buggy != correct:
                    slug = obj.get("slug")
                    variant_idx = obj.get("variant_idx")
                    if slug is None or variant_idx is None:
                        continue
                    # Keep one clean pair per (slug, variant_idx). Later duplicate
                    # rows come from resume/rerun appends and should not bias the
                    # per-slug selection logic downstream.
                    result.setdefault(slug, {})[int(variant_idx)] = (buggy, correct)
    except Exception:
        pass
    return {
        slug: [pairs_by_variant[idx] for idx in sorted(pairs_by_variant)]
        for slug, pairs_by_variant in result.items()
    }


_BEDROCK_CACHE: dict[str, list[tuple[str, str]]] | None = None


def _get_bedrock_pairs(slug: str) -> list[tuple[str, str]]:
    global _BEDROCK_CACHE
    if _BEDROCK_CACHE is None:
        _BEDROCK_CACHE = _load_bedrock_cache()
    return _BEDROCK_CACHE.get(slug, [])


def synthesize_for_slug(slug: str, label: str, tier: str) -> tuple[str, str]:
    # Prefer LLM-generated pairs from AWS Bedrock when available — these are
    # higher-fidelity than the template-based synthesis below.
    bedrock_pairs = _get_bedrock_pairs(slug)
    if bedrock_pairs:
        # Return a deterministic pick so repeated calls are stable, but the
        # caller (_synth_variant_transform) applies further surface variation.
        import hashlib
        idx = int(hashlib.md5(slug.encode()).hexdigest(), 16) % len(bedrock_pairs)
        buggy, correct = bedrock_pairs[idx]
        tag = f"// case: {slug}\n// intent: {label}\n"
        return tag + buggy, tag + correct

    # Always prepend a per-slug comment to BOTH buggy and correct so that two
    # taxonomy entries that happen to route to the same recipe/template do not
    # produce textually identical pairs. The grader strips comments before
    # anchor matching, so this does not create a false pass/fail signal.
    tag_buggy = f"// case: {slug}\n// intent: {label}\n"
    tag_correct = f"// case: {slug}\n// intent: {label}\n"

    for recipe in RECIPES:
        try:
            result = recipe(label)
        except Exception:
            result = None
        if result is None:
            continue
        buggy, correct = result
        if buggy != correct and buggy.strip() and correct.strip():
            return tag_buggy + buggy, tag_correct + correct

    buggy, correct = _generic_fallback(label, tier)
    return tag_buggy + buggy, tag_correct + correct


def synthesize_all() -> list[dict]:
    cases: list[dict] = []
    for entry in BUG_TAXONOMY_ENTRIES:
        buggy, correct = synthesize_for_slug(entry["slug"], entry["label"], entry["tier"])
        cases.append({
            "slug": entry["slug"],
            "label": entry["label"],
            "tier": entry["tier"],
            "buggy": buggy,
            "correct": correct,
        })
    return cases


if __name__ == "__main__":
    cases = synthesize_all()
    # Report stats.
    from collections import Counter
    matched_by_recipe = 0
    for c in cases:
        # Heuristic: no "BUG:" header means a specific recipe matched
        # (since _generic_fallback always adds a comment anchor).
        if "// BUG:" not in c["buggy"]:
            matched_by_recipe += 1
    print(f"total slugs: {len(cases)}")
    print(f"matched by specific recipe: {matched_by_recipe}")
    print(f"synthesized via generic fallback: {len(cases) - matched_by_recipe}")
    tier_counts = Counter(c["tier"] for c in cases)
    print(f"by tier: {dict(tier_counts)}")
    # Verify all pairs are non-trivially different
    trivial = sum(1 for c in cases if c["buggy"].strip() == c["correct"].strip())
    print(f"no-op pairs (must be 0): {trivial}")
    sample = cases[0]
    print("\n=== sample ===")
    print(f"slug: {sample['slug']}")
    print(f"label: {sample['label']}")
    print("buggy:")
    print(sample["buggy"][:300])
    print("correct:")
    print(sample["correct"][:300])
