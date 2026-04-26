import argparse
import difflib
import hashlib
import json
import re
import sys as _sys
from pathlib import Path

# Make sibling scripts importable regardless of how this file is launched.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))


ROOT = Path(__file__).resolve().parents[1]
AIE_DATASET_DIR = ROOT / "aie_dataset"
OUTPUT_DIR = ROOT / "data" / "processed"
OUTPUT_ALL = OUTPUT_DIR / "aie_instruction_all.jsonl"
OUTPUT_TRAIN = OUTPUT_DIR / "aie_instruction_train.jsonl"
OUTPUT_VALIDATION = OUTPUT_DIR / "aie_instruction_validation.jsonl"
OUTPUT_UPLOAD = OUTPUT_DIR / "aie_sft_upload_ready.jsonl"

OUTPUT_ALL_V2 = OUTPUT_DIR / "aie_instruction_v2_all.jsonl"
OUTPUT_TRAIN_V2 = OUTPUT_DIR / "aie_instruction_v2_train.jsonl"
OUTPUT_VALIDATION_V2 = OUTPUT_DIR / "aie_instruction_v2_validation.jsonl"
OUTPUT_UPLOAD_V2 = OUTPUT_DIR / "aie_sft_v2_upload_ready.jsonl"

# When True, debug-response variants emit the full corrected source code (no diff,
# no lead sentence, no symptom/anchor/rationale prose). Flipped via --v2 CLI flag.
V2_MODE = False


def _v2_lang_for_path(relative_path: str | None) -> str:
    if not relative_path:
        return "cpp"
    lower = str(relative_path).lower()
    if lower.endswith((".h", ".hpp")):
        return "cpp"
    return "cpp"


def build_v2_code_response(correct_text: str, relative_path: str | None = None) -> str:
    """V2 response: the full corrected source, fenced, with no surrounding prose.
    Whitespace is normalized (strip trailing whitespace per line, single trailing newline)."""
    lang = _v2_lang_for_path(relative_path)
    text = correct_text or ""
    # Strip trailing whitespace on each line and drop excess trailing blank lines
    # so the model doesn't learn to reproduce noise from source files.
    normalized_lines = [line.rstrip() for line in text.splitlines()]
    while normalized_lines and normalized_lines[-1] == "":
        normalized_lines.pop()
    body = "\n".join(normalized_lines)
    return f"```{lang}\n{body}\n```"


# ---------------------------------------------------------------------------
# V2 instruction paraphrases
#
# V1 instructions were formulaic ("This AIE source has a `subtraction_instead_of_addition`
# issue. The bug is explicit. ...") which (a) leaks the bug slug the model won't see at
# inference and (b) trains the model to pattern-match on the sentence template instead of
# on the code. V2 replaces these with:
#   - a diverse pool of natural-English paraphrases,
#   - tier-aware hint density (easy => often no hint; extra_hard => rarely name the bug),
#   - no snake_case slug leakage (we keep slugs in metadata only).
# ---------------------------------------------------------------------------

_V2_NO_HINT_PROMPTS = [
    "Fix the bug in this AIE source and return the full corrected file.",
    "Something is wrong with this AIE code. Return a corrected version of the whole file.",
    "Debug this Versal AIE source. Respond with the complete fixed file.",
    "This AIE source has a defect. Return the corrected source, in full.",
    "Find and fix the bug in this AIE file. Output the entire corrected file.",
    "Repair this AIE kernel/graph and return the full corrected source.",
    "There's a bug in the code below. Rewrite the file with the bug fixed.",
    "This Versal AIE code misbehaves. Return a corrected version of the complete source.",
    "Correct the AIE source below. Return the full fixed file - no commentary.",
    "This AIE design is broken. Emit the whole corrected source file.",
    "Patch the AIE source below. Output the full corrected file and nothing else.",
    "The AIE file below has a defect. Produce the corrected version of the entire file.",
    "Identify and correct the bug in this AIE source; return the whole fixed file.",
    "Rewrite this AIE source so it's correct. Return the complete file.",
    "There is exactly one bug in the AIE source below. Fix it and return the full file.",
    "This code is supposed to work on Versal AIE but it's broken. Return the corrected file.",
    "Produce a corrected version of the AIE file below (full source, no explanation).",
    "Please debug and repair the AIE source below. Return the complete fixed file.",
]

_V2_SYMPTOM_PROMPTS = [
    "The AIE design below misbehaves: {symptom}. Return the full corrected source file.",
    "I'm seeing this on the code below: {symptom}. Fix it and return the complete file.",
    "This AIE source exhibits the following behavior: {symptom}. Return the corrected file in full.",
    "Debug this AIE source - symptom: {symptom}. Emit the full fixed file.",
    "Symptom observed during aiesim/deployment: {symptom}. Fix the source and return the whole file.",
    "When this runs, {symptom}. Find the cause and return the complete corrected file.",
    "Observed failure: {symptom}. Correct the AIE source below and return the full file.",
    "Failure mode: {symptom}. Fix the file and return the complete corrected source.",
    "This AIE file fails with: {symptom}. Return the whole corrected file.",
    "Runtime symptom: {symptom}. Debug the source and return the full corrected file.",
    "Tracing shows {symptom} in the source below. Fix it and return the complete file.",
]

_V2_MULTI_FILE_NO_HINT = [
    "Fix the bug in the primary AIE source below. The related file is provided for context. Return the full corrected primary file.",
    "The primary AIE source is broken; the related file shows the shared contract. Return the complete corrected primary file.",
    "Debug the primary AIE source using the related file as reference. Emit the full corrected primary file.",
    "Something is wrong in the primary file below. Use the related file for context. Return the whole fixed primary source.",
    "Correct the primary AIE file. The related file is provided so you can see the cross-file contract. Return the full fixed primary source.",
    "There is a cross-file bug involving the primary and the related file below. Return the full corrected primary file.",
    "The primary AIE source violates a contract that is visible in the related file. Return the complete corrected primary file.",
    "Read both files below. The bug lives in the primary file. Return the full corrected primary source.",
    "Repair the primary AIE file. Use the related file only as a reference. Return the whole fixed primary source.",
    "Rewrite the primary AIE source so it matches the contract implied by the related file. Return the complete primary file.",
]

_V2_MULTI_FILE_SYMPTOM = [
    "Cross-file bug; symptom: {symptom}. Return the full corrected primary file (the related file is for context).",
    "The primary AIE source misbehaves against the related file with symptom: {symptom}. Emit the complete corrected primary file.",
    "Symptom at integration: {symptom}. Fix the primary file using the related file as reference and return the full primary source.",
    "Observed: {symptom}. Debug the primary file below (the related file is for context) and return the complete corrected primary source.",
    "Failure across the two files: {symptom}. Fix the primary file and return it in full.",
    "Integration failure - {symptom}. Return the full corrected primary AIE file.",
    "When wired together, these files produce: {symptom}. Fix the primary file and return it in full.",
    "Runtime issue: {symptom}. Repair the primary AIE file (the related file is for reference) and return the whole file.",
]

_V2_HUMAN_HINT_PROMPTS = [
    "My AIE design hangs in aiesim. Fix the file below.",
    "Output is wrong / truncated / zeros. Fix this AIE source and return the full file.",
    "aiecompiler reports a problem here. Fix it and return the whole file.",
    "The kernel compiles but the deployed design misbehaves. Fix the file and return it in full.",
    "My graph builds but at runtime something is off. Correct the file and return the complete source.",
    "Something in the AIE source below is broken. Can you rewrite it correctly? Return the whole file.",
    "This used to work and now it doesn't - the file is below. Return the fixed version.",
    "Help me debug this AIE file. Return the corrected source (complete file).",
    "My AIE kernel is producing garbage. Fix it and return the full corrected file.",
    "The aiesimulator output doesn't match the reference. Fix this AIE file and return it in full.",
    "Graph routing looks fine but the results are wrong. Correct this AIE source and return the whole file.",
]

# V2 inspection-negative templates. These legitimately name the bug pattern
# because the task IS "does this code show <pattern>?" - but the sentence shape
# varies so inspection-negatives (~41% of the corpus) don't repeat a single frame.
_V2_INSPECTION_SINGLE_TEMPLATES = [
    "Does this AIE source exhibit the {label} pattern? Inspect the code and answer yes or no.",
    "Check the following AIE code for the {label} issue. Is it present?",
    "I need to know if {label} is occurring in this kernel. Review the code below.",
    "Pattern check: {label}. Does the kernel below contain this bug?",
    "Audit this AIE source: does it have a {label} defect?",
    "True or false - this code has a {label} problem.",
    "Scan for {label} in the following AIE file and report your finding.",
    "AIE code review request: is there evidence of {label} here?",
    "Flag it or clear it: does this kernel show {label}?",
    "Examine the code below. Report whether {label} is present.",
    "Is the {label} pattern visible in this AIE source? Yes or no.",
    "Triage this AIE file for the {label} defect. Is it there?",
    "Quick lint: does this AIE code show {label}?",
    "Verify whether {label} applies to the AIE source below.",
    "Looking specifically for {label} - is it present in the code below?",
    "Classify this AIE source: is it affected by {label}?",
]

_V2_INSPECTION_MULTI_TEMPLATES = [
    "Do the two AIE sources below jointly exhibit the {label} pattern? Inspect both files.",
    "Cross-file pattern check: {label}. Is it present across the primary and related file below?",
    "Audit these two AIE files together: do they show a {label} defect?",
    "Is there evidence of {label} across this primary/related file pair?",
    "Multi-file triage: does the pair below jointly show the {label} bug?",
    "Verify whether {label} applies to the combined primary+related AIE sources below.",
    "Pair inspection for {label} - is it present across the two files?",
    "Examine both files. Report whether {label} is jointly evident.",
    "True or false - these two AIE files together exhibit {label}.",
    "Flag it or clear it: does the pair below show {label}?",
    "Joint pattern check across the two AIE sources: is {label} present?",
    "Looking specifically for {label} across the two files below - is it there?",
]

# Short verdict phrasings for V2 inspection-negatives so the response isn't
# one frozen sentence repeated 871x per pattern.
_V2_NEG_VERDICTS_SINGLE = [
    "No - the {label} pattern is not present in this source.",
    "Verdict: the {label} pattern is not evident here.",
    "{label}: not present.",
    "This source does not exhibit {label}.",
    "Clear - no {label} defect in this code.",
    "Answer: {label} is not occurring in this source.",
    "Not present. The source does not show the {label} pattern.",
    "False - {label} is not visible in this AIE source.",
]

_V2_NEG_VERDICTS_MULTI = [
    "No - the {label} pattern is not jointly evident across these two files.",
    "Verdict: {label} is not present across the primary and related files.",
    "{label}: not present across the pair.",
    "These two files do not jointly exhibit {label}.",
    "Clear - no joint {label} defect across the pair.",
    "Answer: {label} is not occurring across the two sources.",
]

# Tier-aware mix of hint density. Each entry is a list of (pool_name, weight) tuples.
# Pool names: "none", "symptom", "human".
_V2_TIER_MIX = {
    "easy":       [("none", 6), ("human", 3), ("symptom", 1)],
    "normal":     [("none", 4), ("symptom", 4), ("human", 2)],
    "medium":     [("none", 4), ("symptom", 4), ("human", 2)],
    "hard":       [("none", 5), ("symptom", 4), ("human", 1)],
    "extra_hard": [("none", 6), ("symptom", 3), ("human", 1)],
    "cross_cutting": [("none", 4), ("symptom", 4), ("human", 2)],
}


def _v2_pick_pool(tier: str, seed_key: str) -> str:
    mix = _V2_TIER_MIX.get(tier, _V2_TIER_MIX["normal"])
    total = sum(w for _, w in mix)
    pick = deterministic_index("pool:" + seed_key, total)
    acc = 0
    for name, w in mix:
        acc += w
        if pick < acc:
            return name
    return mix[-1][0]


def build_v2_instruction(seed_key: str, tier: str, symptom: str | None) -> str:
    """Pick a single-file debug instruction for V2. No bug slug is leaked."""
    pool = _v2_pick_pool(tier, seed_key)
    if pool == "symptom" and symptom and symptom != "synthetic mutation":
        template = _V2_SYMPTOM_PROMPTS[deterministic_index("sym:" + seed_key, len(_V2_SYMPTOM_PROMPTS))]
        return template.format(symptom=str(symptom).rstrip("."))
    if pool == "human":
        return _V2_HUMAN_HINT_PROMPTS[deterministic_index("hum:" + seed_key, len(_V2_HUMAN_HINT_PROMPTS))]
    return _V2_NO_HINT_PROMPTS[deterministic_index("none:" + seed_key, len(_V2_NO_HINT_PROMPTS))]


def build_v2_multi_file_instruction(seed_key: str, tier: str, symptom: str | None) -> str:
    """Pick a multi-file debug instruction for V2. No bug slug is leaked."""
    pool = _v2_pick_pool(tier, seed_key)
    if pool == "symptom" and symptom and symptom != "synthetic mutation":
        template = _V2_MULTI_FILE_SYMPTOM[deterministic_index("mfsym:" + seed_key, len(_V2_MULTI_FILE_SYMPTOM))]
        return template.format(symptom=str(symptom).rstrip("."))
    return _V2_MULTI_FILE_NO_HINT[deterministic_index("mfnone:" + seed_key, len(_V2_MULTI_FILE_NO_HINT))]


def build_v2_inspection_instruction(seed_key: str, label: str, multi_file: bool) -> str:
    """Pick a varied inspection-negative instruction for V2 (keeps the human-readable
    label, which IS the question, but varies the sentence shape across the 41% of the
    corpus that is inspection-negatives)."""
    pool = _V2_INSPECTION_MULTI_TEMPLATES if multi_file else _V2_INSPECTION_SINGLE_TEMPLATES
    template = pool[deterministic_index("insp:" + ("m:" if multi_file else "s:") + seed_key, len(pool))]
    return template.format(label=label)


def build_v2_inspection_verdict(seed_key: str, label: str, multi_file: bool) -> str:
    """Pick a varied short verdict for V2 inspection-negative responses."""
    pool = _V2_NEG_VERDICTS_MULTI if multi_file else _V2_NEG_VERDICTS_SINGLE
    template = pool[deterministic_index("verd:" + ("m:" if multi_file else "s:") + seed_key, len(pool))]
    return template.format(label=label)


# V2 clean-code distractor prompts: asks the model to judge a correct source and
# answer affirmatively. Balances the inspection-negative rows (which always say
# "not present") with legitimate "looks fine" examples so the model doesn't
# collapse to always-refuse.
_V2_CLEAN_CODE_PROMPTS = [
    "Does this AIE source have a bug, or is it correct as written?",
    "Review this Versal AIE file and tell me whether it is correct or has a defect.",
    "Is there anything wrong with this AIE source? Give a short verdict.",
    "Audit this file and state whether it is correct.",
    "Inspect this AIE source and tell me if it is buggy or clean.",
    "Please check this file for correctness and report your verdict.",
    "Is this AIE code correct, or does it contain a bug I should fix?",
    "Look over this Versal source and give me a pass/fail verdict.",
    "Is this source implementation correct?",
    "Evaluate this AIE source for correctness.",
]

_V2_CLEAN_CODE_VERDICTS = [
    "Yes - this AIE source looks correct. I do not see a bug.",
    "Verdict: this file appears correct as written.",
    "No defect found. The source implementation is correct.",
    "This AIE source looks clean - no bug to fix.",
    "Pass. The code is correct.",
    "I do not see a defect here; the source looks correct.",
    "Clean - no issue detected in this AIE source.",
    "Correct. I would ship this as-is.",
]


def build_v2_clean_code_instruction(seed_key: str) -> str:
    idx = deterministic_index("v2cc:prompt:" + seed_key, len(_V2_CLEAN_CODE_PROMPTS))
    return _V2_CLEAN_CODE_PROMPTS[idx]


def build_v2_clean_code_response(seed_key: str) -> str:
    idx = deterministic_index("v2cc:verdict:" + seed_key, len(_V2_CLEAN_CODE_VERDICTS))
    return _V2_CLEAN_CODE_VERDICTS[idx]


def build_v2_clean_code_entries(file_infos: list[dict], target_rows: int = 500) -> list[dict]:
    """Produce clean-code distractor rows: given a known-correct AIE file,
    ask whether it has a bug and respond 'no, looks fine'. Deterministic
    selection across the corpus, capped at ~target_rows."""
    entries: list[dict] = []
    seen_paths: set[str] = set()
    candidates = [
        info for info in file_infos
        if info.get("context") and len(info.get("context", "")) < 12_000
        and not info.get("bug_type")  # only known-correct sources
    ]
    stride = max(1, len(candidates) // max(1, target_rows))
    for i, info in enumerate(candidates):
        if len(entries) >= target_rows:
            break
        if i % stride != 0:
            continue
        rel_path_raw = info.get("relative_path") or info.get("path") or f"file_{i}"
        rel_path = str(rel_path_raw)
        if rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)
        seed_key = f"clean:{rel_path}"
        instruction = build_v2_clean_code_instruction(seed_key)
        response = build_v2_clean_code_response(seed_key)
        context_text = (info.get("context") or "").rstrip() + "\n"
        entries.append({
            "instruction": instruction,
            "context": context_text,
            "response": response,
            "metadata": {
                "variant": "clean_code_distractor",
                "bug_type": None,
                "tier": "normal",
                "source_group": info.get("source_group") or rel_path,
                "relative_path": rel_path,
                "split": info.get("split", "train"),
            },
        })
    return entries


def crop_context_around_diff(buggy_text: str, correct_text: str, window: int = 20) -> tuple[str, str] | None:
    """Return cropped (buggy, correct) snippets around the line-diff region.

    Uses longest-common-prefix / longest-common-suffix so that single-edit
    insertions or deletions (length mismatch) are handled correctly. The
    returned snippets cover the SAME contextual region in both files, padded
    by ``window`` lines on each side.
    """
    buggy_lines = buggy_text.splitlines()
    correct_lines = correct_text.splitlines()
    if not buggy_lines or not correct_lines:
        return None

    # Longest common prefix.
    lcp = 0
    limit = min(len(buggy_lines), len(correct_lines))
    while lcp < limit and buggy_lines[lcp] == correct_lines[lcp]:
        lcp += 1
    # Longest common suffix (not overlapping the prefix).
    lcs = 0
    while (
        lcs < len(buggy_lines) - lcp
        and lcs < len(correct_lines) - lcp
        and buggy_lines[-1 - lcs] == correct_lines[-1 - lcs]
    ):
        lcs += 1

    # Diff region (half-open):
    #   buggy:   [lcp, len(buggy_lines) - lcs)
    #   correct: [lcp, len(correct_lines) - lcs)
    if lcp == len(buggy_lines) == len(correct_lines):
        # Files are identical - nothing to crop around.
        return None

    b_diff_start = lcp
    b_diff_end = len(buggy_lines) - lcs  # exclusive
    c_diff_start = lcp
    c_diff_end = len(correct_lines) - lcs

    lo = max(0, lcp - window)
    hi_b = min(len(buggy_lines), b_diff_end + window)
    hi_c = min(len(correct_lines), c_diff_end + window)
    buggy_snip = "\n".join(buggy_lines[lo:hi_b])
    correct_snip = "\n".join(correct_lines[lo:hi_c])
    if not buggy_snip.strip() or not correct_snip.strip():
        return None
    # Accept the crop if it is an ABSOLUTE size win (<4000 chars) OR a clear
    # RELATIVE win over the original (<75% of buggy_text size). This lets us
    # crop large multi-line-span mutations while still avoiding no-op crops.
    if len(buggy_snip) >= 4000 and len(buggy_snip) >= 0.75 * len(buggy_text):
        return None
    return buggy_snip, correct_snip


# Prompt variants for the cropped-snippet variant so many rows don't share
# one frozen instruction.
_V2_CROP_PROMPTS = [
    "Here is a short snippet extracted from an AIE source around a suspected defect. Return the corrected snippet (same line range).",
    "Below is the region of an AIE file that contains a bug. Fix it and return the corrected excerpt.",
    "This is an excerpt from an AIE source; a defect lives inside this region. Return the fixed snippet.",
    "Patch this AIE code excerpt. Return the corrected version of the same region.",
    "Small excerpt from a buggy AIE file. Output the fixed snippet covering the same lines.",
    "The following AIE code region contains a defect. Return the corrected excerpt.",
    "Here is an AIE snippet around a bug. Please return the fixed version of this region.",
    "I pulled out the lines around a bug in my AIE source. Give me back the fixed excerpt.",
    "Excerpt of AIE code with a bug somewhere inside. Return just the fixed excerpt.",
    "Fix the bug in this AIE code excerpt and return the corrected snippet.",
]


def build_v2_cropped_variants(bug_rows: list[dict], max_variants: int = 800) -> list[dict]:
    """Companion rows whose context+response are cropped to the defect's neighborhood."""
    produced: list[dict] = []
    for row in bug_rows:
        if len(produced) >= max_variants:
            break
        meta = row.get("metadata", {}) or {}
        variant = meta.get("variant")
        if variant not in {"bug_fix_pair", "taxonomy_debug_scenario", "multi_file_bug_fix_pair"}:
            continue
        buggy = row.get("context", "") or ""
        correct_fenced = row.get("response", "") or ""
        m = re.match(r"^```[a-zA-Z0-9]*\n(.*?)\n```\s*$", correct_fenced, flags=re.DOTALL)
        if not m:
            continue
        correct = m.group(1)
        # V2 bug-fix contexts contain 'Buggy version:\n<buggy>\nCorrect version:\n<correct>'.
        # Extract the buggy half for line-diff alignment.
        buggy_clean = buggy
        start_marker = "Buggy version:\n"
        if buggy_clean.startswith(start_marker):
            buggy_clean = buggy_clean[len(start_marker):]
        end_marker = "\nCorrect version:"
        if end_marker in buggy_clean:
            buggy_clean = buggy_clean.split(end_marker, 1)[0]
        cropped = crop_context_around_diff(buggy_clean, correct, window=15)
        if cropped is None:
            continue
        buggy_snip, correct_snip = cropped
        rel_path = str(meta.get("relative_path") or "file")
        lang = _v2_lang_for_path(rel_path)
        new_meta = dict(meta)
        new_meta["variant"] = f"{variant}_cropped"
        new_meta["cropped_window"] = 15
        seed_key = f"crop:{rel_path}:{meta.get('bug_type','')}:{len(produced)}"
        prompt_idx = deterministic_index(seed_key, len(_V2_CROP_PROMPTS))
        produced.append({
            "instruction": _V2_CROP_PROMPTS[prompt_idx],
            "context": buggy_snip,
            "response": f"```{lang}\n{correct_snip}\n```",
            "metadata": new_meta,
        })
    return produced


# Realistic error-message fragments keyed by a substring of the bug_type slug.
# These are the kinds of strings real users paste from aiecompiler / chess /
# aiesimulator / x86sim output, rather than prose narration.
_V2_ERROR_TEMPLATES: list[tuple[str, list[str]]] = [
    ("off_by_one", [
        "aiesimulator: segfault in kernel; likely OOB buffer read near end of window.",
        "ERROR: window read index exceeds configured window size by 1 sample.",
        "Runtime trap: out-of-bounds access on input window.",
    ]),
    ("oob", [
        "ERROR: port index out of range in connect(); kernel only declares 2 inputs.",
        "aiecompiler: error: buffer access outside declared bounds.",
    ]),
    ("deadlock", [
        "aiesimulator hang detected; no kernel made progress for 1e6 cycles.",
        "WARNING: producer-consumer stream stall; graph failed to terminate.",
        "chess: stream backpressure deadlock suspected (unbalanced reads/writes).",
    ]),
    ("plio", [
        "aiecompiler: error: PLIO width mismatch between graph declaration and kernel port.",
        "ERROR: plio_32_bits interface connected to a 16-bit kernel port.",
    ]),
    ("runtime_ratio", [
        "aiecompiler: error: runtime<ratio> must be > 0.",
        "aiecompiler: error: sum of runtime<ratio> for kernels on the same tile exceeds 1.0.",
    ]),
    ("missing_connection", [
        "aiecompiler: error: kernel port declared but not connected in graph constructor.",
        "ERROR: unconnected port on kernel instance.",
    ]),
    ("self_loop", [
        "aiecompiler: error: connect forms a self-loop without an intermediate buffer.",
    ]),
    ("missing_graph_wait", [
        "Host test fails: output buffer still zero when checked; likely missing graph wait.",
    ]),
    ("missing_adf_source", [
        "aiecompiler: error: kernel has no adf::source(k) assignment; cannot locate kernel body.",
    ]),
    ("window_margin", [
        "aiecompiler: error: window margin larger than window size is invalid.",
    ]),
    ("pipelining", [
        "chess: warning: loop could not be pipelined; 3x throughput loss.",
        "chess: break statement prevents software pipelining of this loop.",
    ]),
    ("to_vector_shift", [
        "Output values off by 2^15; to_vector shift parameter looks wrong.",
    ]),
    ("acc48", [
        "chess: accumulator overflow detected; consider acc80 for int32xint32.",
    ]),
    ("signed_unsigned", [
        "Sign-extension artifacts at the MSB boundary of output samples.",
    ]),
    ("unaligned", [
        "chess: warning: pointer not aligned to 128-bit boundary for vector load.",
    ]),
    ("modulo", [
        "chess: software pipelining suboptimal; modulo inside loop - use chess_circular_buffer.",
    ]),
    ("bfloat16", [
        "aiecompiler: error: bfloat16 is only supported on AIE-ML, not AIE1.",
    ]),
    ("begin_vector", [
        "aiecompiler: error: begin_vector width must be a power of 2.",
    ]),
    ("broadcast", [
        "aiecompiler: error: broadcast width does not match destination vector width.",
    ]),
    ("stream_drops", [
        "Unit test fails: last sample of each output block is missing.",
    ]),
    ("signed", [
        "uint16 data is being sign-extended as int16 producing wrong values.",
    ]),
    ("accumulator_reset", [
        "Output has a DC offset that accumulates across calls; accumulator not cleared.",
    ]),
    ("wrong_loop_count", [
        "Output length is half of expected; loop bound looks wrong.",
    ]),
    ("missing_output_write", [
        "Host test fails: output stream is empty; kernel never writes output.",
    ]),
    ("subtraction_instead_of_addition", [
        "Unit test fails: outputs are the negation of expected.",
    ]),
    ("aie_add_used_instead_of_aie_mul", [
        "Outputs look like sums rather than products; aie::add used where aie::mul was intended.",
    ]),
]

_V2_ERROR_WRAPPERS = [
    "I'm seeing this error when I build:\n\n  {error}\n\nCan you fix the source?",
    "Build fails with:\n  {error}\nReturn the corrected file.",
    "aiecompiler output:\n  {error}\nFix the AIE source below.",
    "Got this runtime message:\n  {error}\nPlease fix the code.",
    "{error}\n\nPlease return the fixed file.",
    "My design fails with:\n  {error}\nPatch the source and return the corrected file.",
    "The toolchain reports:\n  {error}\nFix the AIE code.",
    "I get:\n  {error}\nCan you correct the source?",
]


def _match_error_templates(bug_slug: str) -> list[str]:
    slug_lower = (bug_slug or "").lower()
    matched: list[str] = []
    for key, templates in _V2_ERROR_TEMPLATES:
        if key in slug_lower:
            matched.extend(templates)
    return matched


def build_v2_compiler_error_rows(bug_rows: list[dict], max_rows: int = 800) -> list[dict]:
    """Produce rows where the instruction leads with a realistic compiler /
    runtime error fragment rather than a prose symptom. These teach the model
    to recognize concrete error strings and map them to a fix."""
    produced: list[dict] = []
    for row in bug_rows:
        if len(produced) >= max_rows:
            break
        meta = row.get("metadata", {}) or {}
        variant = meta.get("variant")
        if variant not in {"bug_fix_pair", "taxonomy_debug_scenario"}:
            continue
        bug_slug = str(meta.get("bug_type") or "")
        templates = _match_error_templates(bug_slug)
        if not templates:
            continue
        response = row.get("response", "") or ""
        if not response.startswith("```"):
            continue
        context = row.get("context", "") or ""
        # Strip existing V2 'Buggy version:' / 'Correct version:' framing and
        # present only the buggy source, since the error itself supplies the
        # symptom.
        if context.startswith("Buggy version:\n"):
            context = context[len("Buggy version:\n"):]
        if "\nCorrect version:" in context:
            context = context.split("\nCorrect version:", 1)[0]
        rel_path = str(meta.get("relative_path") or "file")
        seed_key = f"err:{rel_path}:{bug_slug}:{len(produced)}"
        err = templates[deterministic_index(seed_key + ":t", len(templates))]
        wrapper = _V2_ERROR_WRAPPERS[deterministic_index(seed_key + ":w", len(_V2_ERROR_WRAPPERS))]
        instruction = wrapper.format(error=err)
        new_meta = dict(meta)
        new_meta["variant"] = f"{variant}_compiler_error"
        new_meta["error_prompt"] = True
        produced.append({
            "instruction": instruction,
            "context": context,
            "response": response,
            "metadata": new_meta,
        })
    return produced


def cap_negative_ratio(rows: list[dict], max_ratio: float = 0.18) -> list[dict]:
    """V2: cap taxonomy_*_inspection_negative rows at max_ratio of the corpus."""
    def _is_neg(r: dict) -> bool:
        return str(r.get("metadata", {}).get("variant", "")).endswith("inspection_negative")

    negatives = [r for r in rows if _is_neg(r)]
    positives = [r for r in rows if not _is_neg(r)]
    if not negatives or not positives:
        return rows
    allowed = int(len(positives) * max_ratio / max(1e-6, (1.0 - max_ratio)))
    if len(negatives) <= allowed:
        return rows
    negatives_sorted = sorted(negatives, key=lambda r: (
        str(r.get("metadata", {}).get("variant", "")),
        str(r.get("metadata", {}).get("bug_type", "")),
        str(r.get("metadata", {}).get("relative_path", "")),
        str(r.get("instruction", ""))[:80],
    ))
    stride = max(1, len(negatives_sorted) // max(1, allowed))
    kept = [negatives_sorted[i] for i in range(0, len(negatives_sorted), stride)][:allowed]
    return positives + kept


def dedup_near_identical_responses(rows: list[dict], max_per_response_key: int = 40, short_threshold: int = 500) -> list[dict]:
    """V2: drop near-identical SHORT response duplicates (templated verdicts).

    Full-file code responses are left alone - a single correct file is legitimately
    the target for many different mutations of itself. We only cap repetition on
    short outputs (inspection-negative verdicts, clean-code verdicts) where the
    same phrasing would otherwise appear hundreds of times."""
    kept: list[dict] = []
    counts: dict[str, int] = {}
    for row in rows:
        out = (row.get("response") or row.get("output") or "")
        if len(out) > short_threshold:
            kept.append(row)
            continue
        key = re.sub(r"\s+", " ", out).strip()[:400]
        c = counts.get(key, 0)
        if c >= max_per_response_key:
            continue
        counts[key] = c + 1
        kept.append(row)
    return kept


def _row_group_id(row: dict) -> str:
    """Stable per-source-file identifier for group-aware splitting."""
    meta = row.get("metadata", {}) or {}
    repo = str(meta.get("source_repo") or "local")
    src = str(meta.get("source_path") or meta.get("relative_path") or meta.get("source") or "")
    norm = src.replace("\\", "/").lower()
    norm = re.sub(r"_correct(?=\.)", "", norm)
    norm = re.sub(r"_buggy[^/.]*(?=\.)", "", norm)
    return f"{repo}:{norm}"


def stamp_group_ids(rows: list[dict]) -> list[dict]:
    """Attach a stable group_id to each row so downstream trainers can
    perform group-aware splits (no source file in both train and eval)."""
    for row in rows:
        meta = row.setdefault("metadata", {})
        meta["group_id"] = _row_group_id(row)
    return rows


def build_v2_general_code_mixin(v1_all_path: Path, target_rows: int = 300) -> list[dict]:
    """V2: 3-5% mix-in of v1 general-AIE explanation rows to preserve the
    model's ability to reason about AIE code beyond pure bug-fix responses.

    Only pulls v1 rows whose split == "train" (v1 already uses group-aware
    splitting), so none of these sources leak into the V2 held-out set.
    Variants preserved: causal_debugging, structured_extraction, deep_explanation."""
    if not v1_all_path.exists():
        return []
    allowed_variants = {"causal_debugging", "structured_extraction", "deep_explanation"}
    candidates: list[dict] = []
    with v1_all_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            meta = row.get("metadata", {}) or {}
            if meta.get("split") != "train":
                continue
            if meta.get("variant") not in allowed_variants:
                continue
            if not (row.get("instruction") and row.get("response")):
                continue
            candidates.append(row)
    if not candidates:
        return []
    # Deterministic stride sample, balanced across variants.
    candidates.sort(key=lambda r: (r["metadata"].get("variant", ""), _row_group_id(r)))
    per_variant = max(1, target_rows // len(allowed_variants))
    buckets: dict[str, list[dict]] = {v: [] for v in allowed_variants}
    for row in candidates:
        v = row["metadata"].get("variant", "")
        if v in buckets and len(buckets[v]) < per_variant * 3:
            buckets[v].append(row)
    out: list[dict] = []
    for v, bucket in buckets.items():
        if not bucket:
            continue
        stride = max(1, len(bucket) // per_variant)
        out.extend(bucket[::stride][:per_variant])
    # Normalize metadata: tag as mix-in, force split=train, preserve group_id.
    normalized: list[dict] = []
    for row in out:
        meta = dict(row.get("metadata", {}) or {})
        original_variant = meta.get("variant") or "general"
        meta["variant"] = f"v1_mixin_{original_variant}"
        meta["split"] = "train"
        meta["mixin"] = True
        meta["group_id"] = _row_group_id({"metadata": meta})
        normalized.append({
            "instruction": row["instruction"],
            "context": row.get("context", ""),
            "response": row["response"],
            "metadata": meta,
        })
        if len(normalized) >= target_rows:
            break
    return normalized


DEFAULT_EXPANDED_SOURCE_JSONL = ROOT / "data" / "raw" / "aie_expanded_sources.jsonl"
SUPPORTED_SUFFIXES = {".cc", ".cpp", ".h", ".hpp"}

GENERAL_KERNEL_TEMPLATES = [
    "Analyze this Versal AIE kernel for vectorization, interfaces, buffer requirements, and the mathematical operation performed.",
    "Explain how this Versal AIE kernel moves data, uses vector types, and maps its math onto AIE intrinsics.",
    "Review this AIE kernel and summarize its interfaces, SIMD structure, buffering assumptions, and computation.",
    "Describe the purpose of this Versal AI Engine kernel, including ports, vector lanes, and the main numerical transformation.",
    "Inspect this AIE compute kernel and explain its data types, intrinsics, I/O behavior, and math pipeline.",
    "Provide a technical breakdown of this Versal AIE kernel with emphasis on vectorization, interfaces, and arithmetic flow.",
    "Summarize this AI Engine kernel in terms of its I/O pattern, vector operations, buffering needs, and core computation.",
    "What does this Versal AIE kernel do, and how do its interfaces and vector intrinsics support the implementation?",
    "Analyze this AIE source as a compute kernel and explain its ports, SIMD strategy, and mathematical role.",
    "Give a hardware-aware explanation of this Versal AIE kernel, focusing on interfaces, buffers, vector types, and operations.",
]

GENERAL_GRAPH_TEMPLATES = [
    "Analyze this Versal ADF graph for data flow, interfaces, buffer sizing, and AIE deployment constraints.",
    "Explain how this Versal ADF graph wires kernels together and what interface and buffer assumptions it relies on.",
    "Review this ADF graph and describe its topology, external ports, connectivity, and deployment-sensitive constraints.",
    "Summarize the dataflow structure of this Versal graph, including graph ports, kernel chaining, and buffer sizing choices.",
    "Describe this AI Engine graph in terms of its PLIO or stream interfaces, kernel topology, and scheduling assumptions.",
    "Provide a technical analysis of this Versal ADF graph, focusing on connectivity, interface types, and runtime constraints.",
    "What is the end-to-end data path in this ADF graph, and which AIE-specific constraints affect correctness or throughput?",
    "Inspect this graph source and explain its topology, interfaces, and data-movement constraints for deployment on Versal AIE.",
    "Break down this Versal graph into ports, kernels, buffers, and routing assumptions relevant to AIE execution.",
    "Explain this ADF graph from a hardware-software integration perspective, including kernel creation, connectivity, and buffering.",
]

FEATURE_KERNEL_TEMPLATES = [
    "Identify the interface types, vector types, and AIE intrinsics used in this Versal AIE kernel.",
    "List the key AIE data types, vector lanes, interfaces, and intrinsics present in this kernel.",
    "Extract the main hardware-facing features from this AIE kernel: ports, vector types, and intrinsic calls.",
    "What interfaces, SIMD data types, and AI Engine APIs are used by this kernel?",
    "Catalog the important AIE-specific primitives in this kernel, including interfaces, vector types, and intrinsics.",
    "Identify the low-level AIE building blocks used in this kernel source.",
    "From this kernel, extract the port structure, vector types, and major AIE intrinsics.",
    "Highlight the key AIE programming features present in this compute kernel.",
]

FEATURE_GRAPH_TEMPLATES = [
    "Identify the interface types, graph ports, and AIE-specific APIs used in this Versal ADF graph.",
    "Extract the main graph-level AIE features from this source, including ports, connect patterns, and deployment APIs.",
    "What graph interfaces, connectivity primitives, and AIE runtime constructs are present in this ADF graph?",
    "List the key ADF graph features used here, including port declarations, interface types, and graph APIs.",
    "Catalog the graph-level AIE mechanisms in this source, such as connect, runtime, dimensions, and placement constructs.",
    "Identify the structural AIE programming features used by this graph.",
    "Summarize the interface and orchestration primitives used in this Versal graph source.",
    "From this graph, extract the declared ports, interface types, and main AIE orchestration APIs.",
]

DEBUG_KERNEL_TEMPLATES = [
    "Review this Versal AIE kernel for likely debug, deadlock, memory, or throughput failure modes.",
    "What are the most likely debug risks in this AIE kernel, especially around streams, windows, and vectorized access patterns?",
    "Inspect this kernel for failure modes that could cause deadlock, memory violations, or performance regressions on AIE.",
    "Analyze this AIE kernel from a debugging perspective and point out the highest-risk correctness or throughput issues.",
    "Where would you look first when debugging this Versal AIE kernel for stalls, bad outputs, or memory faults?",
    "Explain the main debug-sensitive behaviors in this AIE kernel, including stream balance and buffer access assumptions.",
    "Identify the most plausible runtime or compile-time failure modes in this AI Engine kernel.",
    "What AIE-specific issues could break this kernel at runtime or during integration into a graph?",
]

DEBUG_GRAPH_TEMPLATES = [
    "Review this Versal ADF graph for likely debug, deadlock, or deployment failure modes.",
    "What are the most likely sources of deadlock, routing failure, or throughput mismatch in this ADF graph?",
    "Analyze this graph from a debugging perspective and identify the highest-risk connectivity or scheduling issues.",
    "Where are the main integration risks in this Versal graph, especially around connect topology, dimensions, and runtime ratios?",
    "Explain the debug-sensitive aspects of this ADF graph that could cause stalls, placement problems, or buffer mismatches.",
    "Identify the main graph-level failure modes that an AIE developer should verify before deployment.",
    "What AIE-specific graph issues could cause this design to hang, misroute data, or miss throughput targets?",
    "Inspect this graph for deployment risks related to topology, placement, and buffering.",
]

CAUSAL_DEBUG_KERNEL_TEMPLATES = [
    "Given the metadata and source, explain step-by-step how this AIE kernel would fail, stall, or degrade if its assumptions are violated.",
    "Use the provided metadata to reason through the causal chain from the first kernel-side mismatch to the final runtime symptom.",
    "Explain the debugging chain of causality in this kernel, including where the first incorrect event occurs and how it propagates.",
    "Given the interfaces and bug context, trace the failure from data movement assumptions to the observed kernel symptom.",
]

CAUSAL_DEBUG_GRAPH_TEMPLATES = [
    "Given the metadata and graph source, explain step-by-step how a mismatch would propagate into a graph-level stall, routing failure, or throughput collapse.",
    "Use the provided metadata to trace the causal chain from the first graph wiring mistake to the final deployment or runtime symptom.",
    "Explain the debugging chain of causality in this ADF graph, including where tokens accumulate or dimensions diverge.",
    "Given the interfaces and bug context, trace the failure from graph topology assumptions to the observed graph symptom.",
]

CONTRASTIVE_BUG_TEMPLATES = [
    "Given bug_type = {bug_type} and interfaces = {interfaces}, compare the buggy and correct AIE sources, explain the causal failure chain, and identify the minimal fix.",
    "Contrast these buggy and corrected AIE sources. Use bug_type = {bug_type} and interfaces = {interfaces} to explain why one fails and the other works.",
    "Explain why version A fails but version B does not. Use the metadata bug_type = {bug_type} and interfaces = {interfaces}, and describe the minimal repair.",
    "Analyze this buggy-versus-correct AIE pair using bug_type = {bug_type}. Describe the first broken assumption, the downstream symptom, and the smallest fix.",
]

DATAFLOW_KERNEL_TEMPLATES = [
    "Explain the data movement and mathematical operation in this Versal AIE kernel.",
    "Describe how data enters, transforms, and exits this AI Engine kernel.",
    "Walk through the dataflow of this kernel from input ports to output ports.",
    "Explain the computation pipeline in this AIE kernel, including how data is buffered and transformed.",
    "How does this kernel move data through AIE buffers or streams, and what math does it apply?",
    "Summarize the step-by-step dataflow and computation performed by this Versal kernel.",
    "Describe the operational flow of this AIE kernel, from reads through vector math to writes.",
    "Explain how this compute kernel maps input data movement to its final output values.",
]

DATAFLOW_GRAPH_TEMPLATES = [
    "Explain the end-to-end data flow in this Versal ADF graph, including kernel chaining, external interfaces, and buffer propagation.",
    "Walk through the full data path of this ADF graph from external input to final outputs.",
    "Describe how data moves across kernels, ports, and buffers in this Versal graph.",
    "Explain the end-to-end graph topology and how buffers or streams propagate through it.",
    "How does this ADF graph route data between interfaces and kernels, and where are the key handoff points?",
    "Summarize the graph-level dataflow from ingress ports through intermediate kernels to egress ports.",
    "Describe the sequence of data transformations and transfers in this Versal AI Engine graph.",
    "Explain the full kernel-to-kernel and interface-to-kernel data movement in this graph.",
]

BUG_PAIR_TEMPLATES = [
    "Compare the buggy AIE source against the correct version and explain the root cause, symptom, and fix.",
    "Explain what is wrong in the buggy version, what symptom it causes, and why the correct version fixes it.",
    "Review this buggy-versus-correct AIE pair and identify the specific defect, failure mode, and repair.",
    "Analyze the difference between these two AIE sources and describe the bug, the observed behavior, and the correction.",
    "Given a buggy and corrected AIE source pair, explain the root cause and the exact fix.",
    "What changed between the buggy and correct AIE versions, and how does that change resolve the observed failure?",
]

SYNTHETIC_BUG_PAIR_TARGET = 15000
BUG_FOCUSED_TARGET_RATIO = 0.80
MAX_SYNTHETIC_MUTATIONS_PER_SOURCE = 16
MAX_BUG_ROWS_PER_BUG_TYPE = 1200
MAX_BUG_ROWS_PER_SOURCE_GROUP = 22
TAXONOMY_SCENARIOS_PER_BUG_TYPE = 8
TAXONOMY_MULTI_FILE_SCENARIOS_PER_BUG_TYPE = 3
# Tier-aware overrides: hard and extra_hard bugs are the weakest-covered tiers,
# so oversample them relative to the baseline to shift the final tier histogram upward.
TAXONOMY_SCENARIOS_PER_TIER = {
    "easy": 8,
    "normal": 8,
    "medium": 10,
    "hard": 30,
    "extra_hard": 30,
    "cross_cutting": 8,
}
TAXONOMY_MULTI_FILE_SCENARIOS_PER_TIER = {
    "easy": 3,
    "normal": 3,
    "medium": 4,
    "hard": 8,
    "extra_hard": 8,
    "cross_cutting": 3,
}
MULTI_FILE_RELATED_VARIANTS_PER_RECORD = 4

BUG_TAXONOMY = {
    "easy": [
        "Wrong numeric constant used as gain or scale factor",
        "Wrong loop iteration count - off by factor of 2 or more",
        "Wrong arithmetic operator - subtraction instead of addition or vice versa",
        "Missing output iterator increment - iterator never advances",
        "Reading from wrong stream variable - same stream read twice instead of two distinct streams",
        "Missing adf::source() assignment in graph",
        "Output iterator vector width does not match input iterator width",
        "Wrong accumulator type for data width - acc48 used where acc80 is required",
        "Missing writeincr() call - output stream never written",
        "Wrong PLIO bit width for data type",
        "Runtime ratio set to zero - kernel never executes",
        "to_vector output type does not match buffer element type",
        "Wrong shift amount in srs() - value saturates or loses all precision",
        "Accumulator not initialized with aie::zeros before use",
        "Data read once outside loop instead of once per iteration - stale data reused",
        "Result computed but never written to output - missing store or writeincr",
        "aie::broadcast width does not match target vector width",
        "Window size uses wrong literal - off by factor of 2",
        "adf::connect direction reversed - output port connected to output port",
        "Cascade chain type mismatch between producer and consumer",
        "Wrong constant used as multiplication factor in MAC chain",
        "begin_vector width mismatch - wrong template parameter on one iterator",
        "readincr called on output stream instead of input stream",
        "Missing adf::runtime<ratio>() call entirely",
        "aie::add used where aie::mul was needed",
        "Input iterator incremented twice per loop iteration - skips every other sample",
        "Output always written to index 0 instead of advancing with iterator",
        "Wrong accumulator lane count - half the output lanes never computed",
        "Wrong shift direction in srs - shift goes the wrong way",
        "Graph kernel port declared as input but connected as output",
        "readincr vector width larger than stream provides - overreads stream",
        "Coefficient array uses wrong element type causing silent truncation",
        "Coefficient array not marked static - reloaded from memory every invocation",
        "Loop upper bound uses wrong type causing overflow on large inputs",
        "Uninitialized scalar broadcast as zero into all vector lanes",
        "writeincr called where readincr was needed - reads and writes swapped",
        "Output buffer pointer advanced by 1 instead of vector width each iteration",
        "Loop bound off by one - uses N-1 instead of N or < instead of <=",
        "Vector insert/extract index out of range for the vector size",
        "shuffle_up or shuffle_down shift amount wrong for the element size",
        "Vector concat operand order reversed - low and high halves swapped",
        "aie::store_v width does not match source vector width",
        "Multiply-subtract used where multiply-accumulate was needed",
        "fpmac vs fpmsc confusion in floating point MAC chain",
        "Negated operand in MAC chain producing inverted output",
        "Wrong operand order in asymmetric operation (subtract, divide, shift)",
        "Missing SRS before output - raw accumulator bits stored to vector buffer",
        "Self-loop adf::connect - kernel output connected back to its own input",
        "Port index out of bounds in adf::connect (in[2] when kernel has only 2 inputs)",
        "kernel::create references a function name that does not exist in any source file",
        "adf::source() path points to a .cpp file that does not contain the kernel",
        "Connect type mismatch - window connect attached to stream port",
        "Implicit type narrowing without explicit shift losing precision",
        "Complex to real conversion losing imaginary component unintentionally",
        "Float to fixed conversion using wrong scale factor",
        "bfloat16 conversion used on AIE1 target which does not support it",
        "alignas value too small for vector access (alignas(8) for 32-byte vector load)",
        "REGISTER_FUNCTION macro points to wrong member function name",
        "Inverted comparison in threshold/clip kernel - greater-than swapped with less-than",
        "Off-by-one in coefficient indexing - first or last tap missed",
        "Vector load/store stride wrong - reads every other element instead of contiguous",
        "Wrong sign on initial accumulator load (negative zero vs positive zero)",
        "Missing parentheses around macro argument causing wrong operator precedence",
    ],
    "normal": [
        "Stream deadlock - one input stream never consumed in a dual-stream kernel",
        "Vector width inconsistency between iterator and vector declaration buried in 25-line kernel",
        "Window size in bytes does not match data type (256 samples x sizeof(int16) = 512 bytes, not 256)",
        "Loop processes half the buffer (loop count = samples/vector_width/2)",
        "Two bugs in one kernel: wrong gain and wrong loop count",
        "Three bugs: wrong vector width, wrong loop count, wrong gain",
        "Graph dimensions do not match kernel expected buffer size",
        "Accumulator overflow - acc48 used for int16xint16 with 512 MACs",
        "Missing chess_prepare_for_pipelining causing 3x throughput loss",
        "Input data read once, used in all loop iterations (not refreshed)",
        "Buffer interface in graph but stream interface in kernel signature",
        "Output window size in graph smaller than what kernel actually writes",
        "Two kernels connected but window sizes do not match between them",
        "PLIO width mismatch causing every other sample to be garbage",
        "Kernel reads 256 samples but graph window only provides 128",
        "aie::begin_vector<8> on int16 buffer but computation uses vector<int16, 16>",
        "Inner loop accumulates but outer loop does not reset accumulator",
        "Stream kernel processes 128 samples but graph sends 256",
        "Graph connects two outputs to same input port",
        "Kernel writes fewer samples than output window expects - partial buffer",
        "aie::mul result assigned to wrong-width accumulator",
        "Input iterator advanced in wrong scope (inside inner loop instead of outer)",
        "Two-kernel graph where second kernel input window does not match first kernel output window",
        "Coefficient array declared with wrong size for the filter length",
        "Output buffer iterator created with begin() instead of begin_vector<8>()",
        "begin_vector width not a power of 2 (e.g., begin_vector<6>) which is invalid on AIE",
        "Mixing aie::begin() and aie::begin_vector() - scalar iterator assigned to vector variable",
        "Iterator created from wrong buffer - in_iter initialized from out instead of in",
        "Vector concat/split width mismatch - aie::concat of two vector<int16,8> assigned to vector<int16,8>",
        "aie::filter/select with wrong mask size - mask has fewer bits than vector lanes",
        "Shuffle pattern does not match vector width - 8-element shuffle pattern on 16-element vector",
        "Vector extract with out-of-range index - extracting lane 8 from an 8-lane vector",
        "Interleave/deinterleave applied in wrong order - zip used where unzip was needed",
        "aie::load_v with wrong alignment - pointer not aligned to vector boundary",
        "Using aie::store_v on an input buffer - writing to a read-only buffer",
        "acc48 used for int32xint16 MAC - needs acc80 since product is 48-bit",
        "Accumulator lanes do not match vector lanes - accum<acc48,4> with vector<int16,8>",
        "to_vector with negative shift - undefined behavior",
        "to_vector shift too large for output type - shift of 32 on int16 output zeros everything",
        "Accumulator not reset between output blocks - DC offset accumulates across calls",
        "Using aie::add on accumulator instead of aie::mac - loses precision via premature to_vector",
        "Chaining aie::mac then aie::msc in wrong order - sign error in filter computation",
        "acc48 sufficient for single MAC but overflows after N iterations (accumulation-limit miscalc)",
        "Mixing accumulator types in conditional branches - acc48 in if-branch, acc80 in else-branch",
        "Assigning aie::mul result directly to vector without to_vector - accumulator vs vector confusion",
        "int8 kernel using int16 broadcast - type mismatch in multiplication",
        "cint16 treated as two separate int16 values - wrong vector width for complex data",
        "chess_prepare_for_pipelining inside inner loop instead of outer - wrong loop pipelined",
        "Loop count not a multiple of vector width - last partial vector iteration reads past buffer end",
        "Nested loop with wrong inner trip count - processes wrong number of taps per sample",
        "for loop uses signed int but comparison is unsigned - infinite loop on counter wrap",
        "Break statement in pipelined loop - chess compiler cannot pipeline with early exits",
        "Function call inside inner loop - prevents VLIW pipelining unless inlined",
        "Loop-carried dependency on accumulator prevents pipelining - needs unroll or multiple accumulators",
        "Modulo operation in loop for circular buffer - should use chess_circular_buffer pragma",
        "Kernel execution order not guaranteed - assuming kernel A runs before B without explicit dependency",
        "Cascade chain has gap - kernel A cascades to C skipping B in the physical path",
        "Runtime ratios do not add up - three kernels on same tile sum to >1.0",
        "Repetition count mismatch - graph.run(100) but kernel expects internal looping",
        "graph.wait() missing after graph.run() - host reads output before kernels finish",
        "Subgraph port mapping wrong - hierarchical graph connects inner ports in wrong order",
        "float kernel using integer accumulator - accfloat needed instead of acc48",
        "Signed/unsigned mismatch - uint16 data processed as int16 causing sign extension errors",
        "bfloat16 kernel on AIE1 tile - bfloat16 only supported on AIE-ML",
        "cint32 complex multiply using real multiply intrinsic - need aie::mul(conj) or complex API",
        "int16 data stored in int32 buffer - upper 16 bits are garbage, not sign-extended",
        "Truncation without saturation - int32 to int16 conversion wraps instead of saturating",
        "Window size not a multiple of vector width x sizeof(type) - causes alignment issues",
        "Window margin specified but kernel does not skip margin samples",
        "Total tile memory exceeded - code plus stack plus buffers plus coefficients over 32KB",
        "Program memory exceeded - kernel binary over 16KB instruction memory",
        "Stack overflow - deep recursion or large local arrays on 1KB default stack",
        "Memory bank conflict - two arrays accessed in same cycle mapped to same bank",
        "DMA and kernel accessing same buffer simultaneously - no lock synchronization",
        "Unaligned access - pointer not aligned to 128-bit boundary for vector load",
        "Cache coherency issue - L1 cache not flushed before DMA reads modified data (AIE-ML)",
        "Cascade requires adjacent tiles but mapper cannot place them adjacently",
        "Kernel assumes specific tile coordinates - hardcoded addresses only valid on tile (0,0)",
        "Too many streams per tile - tile supports max 2 input + 2 output streams",
        "Shared memory access between non-adjacent tiles - only neighbors share banks",
        "Window margin larger than window size - invalid configuration",
        "Double buffer disabled but kernel execution time exceeds input rate - data corruption",
        "Window size correct in bytes but graph uses sample count - units confusion",
        "Asymmetric window sizes on multi-input kernel - inputs sized differently while kernel assumes equal",
        "Window size changed but kernel loop count not updated - processes partial buffer or overruns",
        "Shared buffer between kernels without proper synchronization - race condition",
        "Connecting kernel output port to two different consumers without broadcast",
        "adf::connect with stream type but kernel expects buffer - interface mismatch",
        "Port index out of range - k1.in[2] when kernel only has 2 inputs",
        "Cascade port connected as regular stream - wrong connection type for cascade",
        "PLIO connected to wrong kernel port - input data goes to coefficient port",
        "Missing connection - kernel port declared but never connected in graph constructor",
        "Self-loop - kernel output connected back to its own input without intermediate buffer",
        "Connecting two output ports to same input port - data collision",
        "PLIO frequency exceeds maximum for bit width - plio_128_bits limited to 250 MHz",
        "PLIO data file path wrong - relative path does not resolve from build directory",
        "PLIO data file format does not match bit width - hex values are 32-bit but PLIO is 16-bit",
        "Input PLIO file has fewer samples than kernel expects - simulation hangs waiting for data",
        "PLIO name collision - two PLIOs with same string name",
        # new normal entries
        "Coefficient array declared as local instead of static - re-initialized on every kernel call",
        "Two RTP parameters swapped in graph connect - gain applied as offset and offset applied as gain",
        "Output buffer size in graph is correct but kernel writes samples+1 due to loop fence post error",
        "Input stream consumed at half rate - readincr_v16 used but only 8 lanes processed",
        "Kernel uses int32 vector but graph allocates int16 buffer - every other element is garbage",
        "aie::mac accumulates into wrong lane group - result shifted by 8 lanes",
        "Kernel uses post-increment on iterator but loop condition checks pre-incremented value",
        "Missing restrict qualifier on output buffer causes compiler to assume aliasing and serializes stores",
        "Cascade output written unconditionally in first iteration only - remaining iterations silently skipped",
        "Wrong conditional branch taken for negative inputs - abs() not applied before threshold compare",
        "Graph has two kernels but only one is added via content() - second kernel never scheduled",
        "Symmetric FIR uses wrong half-length for coefficient loop - processes all N taps twice",
        "RTP read placed before graph.run() in host code - value not yet propagated to kernel",
        "Stream/cascade port declared in kernel signature but never consumed - causes deadlock",
        "chess_loop_range hint contradicts actual loop bound - software pipelining disabled",
        "Stream producer sends different sample count than consumer expects per invocation",
        "Accumulator not reset between kernel invocations - state leaks across frames",
        "Wrong accumulator type for complex vs real data (acc48 used where cacc48 needed)",
        "Mixed accumulator types in same kernel causing silent precision loss at conversion",
        "begin_restrict_vector used where begin_vector was needed (or vice versa)",
        "Iterator advanced in wrong loop scope - inner loop instead of outer",
        "chess_unroll_loop applied to a loop that should not be unrolled - code bloat exceeds program memory",
        "Data-dependent loop count that can evaluate to zero - skip-frame causes downstream deadlock",
        "Cascade output written with wrong accumulator type - downstream stage reinterprets bits",
        "Stream read inside conditional branch not always executed - asymmetric token consumption",
        "Pointer arithmetic overflow for large buffer sizes - 32-bit pointer wraps past 4GB",
        "__restrict qualifier asserted on buffers that actually alias - silent miscompile",
        "chess_storage register assignment conflicts between two variables sharing same register",
        "volatile qualifier missing on shared memory buffer - reads cached stale value",
        "inline attribute on function too large - exceeds instruction memory budget",
        "Graph edge creates feedback cycle scheduler cannot resolve",
        "Window size correct for input type but wrong for output type after conversion",
        "Window margin size wrong for filter length - last samples computed with garbage",
        "Window size not aligned to vector byte boundary causing bus error",
        "Shared buffer dimensions disagree between producer and consumer kernels",
        "GMIO burst length does not align with kernel buffer size - partial transfers",
        "GMIO bandwidth insufficient for kernel consumption rate - backpressure stalls",
        "Mixed PLIO widths in same design causing PL routing congestion",
        "Runtime ratio set to value greater than 1.0 - schedule infeasible",
        "Runtime ratios across cascade chain do not sum to a consistent rate",
        "RTP connected as sync but kernel reads as async (or vice versa)",
        "RTP read inside inner loop instead of outside - performance collapse from per-iter sync",
        "RTP read outside all loops - value never refreshed during execution",
        "adf::connect for RTP has wrong argument order - source and sink swapped",
        "RTP array size does not match kernel's expected parameter size",
        "Multiple RTP updates between graph.run() calls but kernel only reads parameter once",
        "location<kernel> constraint conflicts with required cascade adjacency",
        "location<buffer> assigns two buffers to the same memory bank",
        "Cascade chain length does not match TP_CASC_LEN template parameter",
        "Middle kernel in cascade does not forward partial results - chain broken silently",
        "Last kernel in cascade does not convert from cascade format to output format",
        "First kernel in cascade initializes accumulator wrong for cascade protocol",
        "Cascade and stream connections mixed incorrectly on same kernel port",
        "Header file declares different function signature than implementation provides",
        "Graph includes wrong header file for kernel function declaration",
        "Kernel compiled with different preprocessor defines than graph expects",
        "Two source files define a kernel function with the same name causing link error",
        "Graph references a kernel function name that does not exist in any source file",
        "Three-stage pipeline middle stage changes data format unexpectedly",
        "Pipeline with feedback loop the scheduler cannot resolve",
        "Pipeline stages with incompatible vector widths at the connection boundary",
        "Interpolation factor in kernel does not match graph output window size expectation",
        "Decimation factor in kernel does not match graph input/output rate ratio",
        "Multi-rate graph configuration creates scheduling deadlock",
        "SSR super sample rate configuration mismatch between graph and kernel",
        "Throughput bottleneck from one slow kernel in otherwise balanced pipeline",
        "DMA transfer size does not match kernel buffer size",
        "Missing DMA synchronization - kernel reads buffer before DMA completes write",
        "DMA burst length not aligned to memory interface width",
        "External memory base address not aligned to DMA requirement",
        "Shim DMA configuration does not match graph PLIO/GMIO setup",
    ],
    "medium": [
        "Two-stage pipeline compiles but stage2 output is corrupted - stage1 outputs int16, stage2 reads int32",
        "Kernel compiles but output is all zeros - to_vector shift is 15 bits, destroying all signal",
        "Graph compiles but simulation produces half the expected output samples - loop count is half",
        "FIR filter output is correct for first tap but wrong for remaining taps - coefficient index does not advance",
        "Kernel output values are correct but in wrong order - input and output iterators use different vector widths",
        "Graph links successfully but simulation deadlocks at T=500000 - stream port never read",
        "Filter produces correct output for small inputs but wraps negative for large inputs - acc48 overflow after many MACs",
        "Kernel output has correct magnitude but wrong sign - subtraction instead of addition in MAC chain",
        "Graph compiles but PLIO input data is interpreted as wrong values - plio_32_bits used for int16 data",
        "Two-kernel pipeline where kernel2 sees stale data from kernel1 - ping-pong buffer timing violated by wrong runtime ratio",
        "Beamformer output has correct first channel but remaining channels are shifted - off-by-one in buffer indexing",
        "Matched filter produces zero output - accumulator never written to output buffer",
        "Channel estimator works in isolation but produces garbage in full graph - window size mismatch between graph and kernel",
        "Pulse compression kernel output is 6dB too low - to_vector shift parameter is 1 instead of 0",
        "FFT butterfly kernel produces correct even samples but wrong odd samples - twiddle factor index calculation wrong",
        "Kernel passes functional sim but throughput is 4x too low - no chess_prepare_for_pipelining and inner loop not VLIW-friendly",
        "Multi-channel filter processes channel 0 correctly but channels 1-3 are copies of channel 0 - data pointer not advanced",
        "Interpolating filter produces twice as many samples as expected - output loop count is 2x input",
        "Decimating filter drops every other output - output iterator advanced twice per iteration",
        "Graph with RTP where parameter value is always initial - RTP read is outside processing loop",
        # new medium entries
        "Polyphase filter bank computes first phase correctly but remaining phases reuse first phase twiddles",
        "Sliding window kernel advances input pointer by window_size instead of hop_size - windows overlap incorrectly",
        "Kernel produces correct single-precision output but graph expects bfloat16 - silent precision loss on AIE-ML",
        "Upsampling kernel inserts zeros correctly but output rate declared as 1x instead of Nx in graph",
        "IIR feedback kernel reads previous output from wrong buffer index - DC drift builds up across frames",
        "Matrix multiplication result is transposed - row and column loop indices are swapped",
        "Vectorised absolute value uses aie::abs on signed vector but input has unsigned type - all values unchanged",
        "Kernel correct for even-length inputs but produces one extra output for odd lengths due to ceiling division",
        "Scale factor applied twice - once in kernel MAC shift and once in graph RTP gain",
        "Stream-based FIR uses circular coefficient buffer but wrap index is never updated across invocations",
        "Accumulator precision sufficient for one MAC but wrong for full chain length under worst-case input",
        "Graph deadlocks at specific time - one port in multi-port kernel never serviced",
        "Filter correct for small values but overflows silently for full-range inputs",
        "Result computed correctly but never reaches output - intermediate write dropped by optimizer",
        "Twiddle factor or coefficient index calculation wrong causing frequency-domain errors",
        "Async buffer kernel stalls or corrupts due to acquire/release protocol error",
        "Two kernels correct independently but cascaded produce wrong result - format mismatch at boundary",
        "Saturation mode wrong on type conversion - wrap vs saturate behavior swapped",
        "Lock acquire pattern allows two kernels to deadlock by acquiring same locks in different order",
        "FFT bit-reversal output ordering applied at wrong stage of pipeline",
        "Multi-tile correlation produces correct peak but wrong sidelobe pattern from window indexing error",
        "Window size kernel reads is correct in samples but graph allocates in bytes (or vice versa)",
        "Conditional kernel branch consumes one stream in one path and a different stream in the other",
        "Floating-point reduction order changes result across vector widths - non-associativity",
    ],
    "hard": [
        "Kernel exceeds tile data memory (32KB) due to local coefficient array plus double-buffered IO",
        "Kernel throughput is 50% of expected due to vector loads crossing memory bank boundaries",
        "Simulation works for 128-sample input but deadlocks on 1024-sample input from producer-consumer drift",
        "Intermittent corruption every other frame from broken double-buffer assumptions",
        "Non-deterministic output between runs from missing lock protocol on shared memory",
        "acc48 overflow after 256 int16xint16 MACs due to sum growth",
        "Mapper fails despite sufficient tiles due to placement constraint and cascade conflict",
        "Optimized build differs from debug build due to missing volatile on shared buffer",
        "Cascade output always zero because cascade stream between non-adjacent tiles is dropped",
        "Graph processes one frame then deadlocks due to circular multi-rate schedule",
        "Kernel works at 256 samples but fails at 512 due to tile memory overrun",
        "FIR output has periodic glitches every 256 samples from circular buffer wrap off-by-one",
        "Beamformer correct for 4 antennas but wrong for 8 due to hardcoded vector width assumptions",
        "Two kernels sharing one tile fail from local memory bank conflicts",
        "Kernel output correct first call but accumulates DC offset due to missing accumulator reset",
        "Stream kernel drops last sample in each block from N-1 loop bound",
        "Three cascaded kernels gradually fill buffer because middle output rate mismatches consumer rate",
        "Kernel reads coefficient RTP once at startup and never refreshes it",
        "Packet-switched stream routes to wrong kernel due to header ID mismatch",
        "Kernel works in x86 sim but fails in aiesimulator due to alignment assumptions",
        # new hard entries
        "Lock acquire/release ordering inverted between producer and consumer kernels - deadlocks after first frame",
        "Kernel local array larger than available scratchpad - overflows into adjacent kernel stack silently",
        "Multi-rate graph where fast kernel runs 4x but buffer is sized for 1x - fourth invocation corrupts memory",
        "Cascade tile pair passes unit test but fails at system level because cascade direction is reversed in graph",
        "Chess compiler unrolls inner loop and reuses register causing read-after-write hazard - wrong output every 4th element",
        "Buffer ping-pong period is 1 graph iteration but kernel needs 2 iterations to produce valid output - stale buffer read",
        "Stream synchronization word (tlast) consumed by wrong kernel in multi-kernel pipeline - frame alignment lost",
        "Float32 kernel accumulates NaN due to uninitialized local float variable used as initial accumulator value",
        "Kernel with two input streams reads them in fixed order but graph scheduler delivers them in opposite order",
        "Dead kernel branch prevents compiler from optimizing live branch - throughput 3x below budget",
        "Works for even buffer sizes but fails for odd - half-vector tail handling missing",
        "Works for power-of-two vector widths but fails for non-power-of-two",
        "Works for real data types but fails for complex - complex accumulator path not exercised",
        "Works for signed types but fails for unsigned - sign-extension assumptions wrong",
        "Works in functional simulation but fails in cycle-accurate simulation - timing assumption invalid",
        "Works in aiesim but fails on hardware - DMA descriptor timing assumption invalid on real silicon",
        "Works for first N frames then fails due to slow state accumulation in hidden variable",
        "Code looks correct but compiler optimizes away a necessary side-effecting operation",
        "Code violates an undocumented AIE API constraint that only manifests at scheduling time",
        "Matrix kernel correct for row-major data but wrong when fed column-major",
        "Two code paths that should be equivalent differ due to floating-point non-associativity",
        "Output is all zeros at runtime despite clean compile - intermediate store dropped due to dead-store elimination",
        "Output samples in wrong order due to lane permutation between two pipeline stages",
        "Compilation succeeds but linking fails - kernel symbol missing from any source file",
        "Mapping succeeds but routing fails - tile-to-tile path is congested by unrelated traffic",
        "Design works in x86sim but fails in aiesim - register-bank assumptions invalid in cycle-accurate model",
        "Memory bank conflict only manifests when two specific kernels happen to be co-located",
        "Race condition on shared coefficient table only triggers under specific multi-rate schedule",
        "Kernel correct standalone but graph throughput collapses due to PLIO clock domain crossing jitter",
    ],
    "extra_hard": [
        "Removing one unrelated kernel fixes others due to circular dependency in buffer allocation pressure",
        "Kernel works on AIE-ML but fails on AIE1 due to accumulator width differences",
        "Streaming multicast with asymmetric consumer rates where fast consumer starves slow consumer",
        "Functional behavior correct but VLIW slot violation from same-bank dual loads in one cycle",
        "Graph works at 1 GHz but fails at 1.25 GHz due to cascade timing closure",
        "DMA and kernel memory access conflict from missing synchronization",
        "Async buffer window_acquire/window_release mismatch causing use-after-release",
        "Packet-switched routing collision because graph exceeds streams-per-tile limits",
        "Compiler-inferred buffer placement crosses tile boundary due to large contiguous vector allocations",
        "int8 kernel arithmetic correct but saturation mode wrong (wrap vs saturate)",
        "4-tile systolic array has one cascade arriving one cycle late due to pipeline depth mismatch",
        "Kernel passes unit tests but fails integration due to hidden aligned(32) requirement",
        "Optimization level changes buffer placement and breaks assumed layout",
        "Two valid kernels co-located on one tile create hidden memory conflict",
        "Fractional runtime ratio rounded to 0 by scheduler so kernel never executes",
        "Stream merge FIFO depth too shallow causing unrelated deadlock via backpressure",
        "AXI4-Stream PL-AIE boundary missing TLAST so DMA never completes",
        "Kernel uses chess_protect() on wrong buffer and misses required fence",
        "Event trace debug calls consume a stream port and break production connectivity",
        "Multi-graph partition induces PLIO routing congestion",
        "Data-dependent loop count deadlocks when specific input pattern yields zero iterations",
        "Shared coefficient buffer between kernels is corrupted due to missing mutex",
        "aie::load_v<8> with unaligned pointer works in sim but faults on hardware",
        "Window margin configured in graph but kernel iteration ignores margin samples",
        "Kernel compiled with different ABI flags than graph causing signature mismatch",
        "Broadcast stream with one slow consumer stalls all branches via backpressure",
        "Tile DMA descriptor chain wraps to wrong address after first N transfers",
        "Kernel uses __restrict on buffers that alias in real topology",
        "Clock gating on idle tiles perturbs adjacent tile memory timing",
        "Recursive kernel function exceeds tile stack for large inputs",
        "Cross-graph physical routing congestion under inter-graph PLIO traffic",
        # new extra_hard entries
        "Kernel correctness depends on compiler not reordering two adjacent stores - breaks at -O2 without volatile",
        "Graph topology valid but AIE router cannot satisfy both cascade and stream constraints simultaneously",
        "DMA burst length exceeds FIFO depth between PL and AIE - sporadic data loss under sustained load",
        "Two subgraphs share a PLIO clock domain but run at different graph rates - phase drift causes misalignment",
        "Kernel uses tile-local timer for profiling but timer wraps mid-frame - negative latency measurement causes assert",
        "Coefficient update via RTP races with kernel mid-computation - output corrupted for one frame per update",
        "Graph compiled with -profile flag adds extra stream reads that change token counts and deadlock production build",
        "Heterogeneous graph mixes AIE and HLS kernels with incompatible FIFO depths - backpressure deadlock at boundary",
        "Feature used that only exists on AIE-ML (bfloat16, acc64) silently breaks on AIE1",
        "Memory size assumption wrong - 32KB tile assumed but target is 64KB AIE-ML (or vice versa)",
        "Stream count per tile assumption wrong between architectures - too many streams routed",
        "Cascade interface width differs between architectures causing silent reinterpret",
        "Kernel meets timing at base frequency but fails at boosted frequency target",
        "Cascade path too long across array causing timing closure failure",
        "VLIW slot conflict - two memory operations from same bank scheduled in same cycle",
        "Software pipelining fails due to loop-carried dependency that compiler cannot break",
        "Throughput limited by memory bandwidth not compute - kernel cannot saturate MAC units",
        "Code compiles and runs but produces subtly wrong results due to integer type promotion rules",
        "Endianness assumption wrong - kernel reads bytes in opposite order from PL producer",
        "Insufficient memory at mapping time - aggregate tile budget exceeded across all kernels",
        "Cannot place kernel at mapping time - location constraints over-specified leaving no valid tile",
        "Routing congestion at routing time - too many stream crossings through one shim row",
        "Timing closure failure at implementation time - long combinational path through shim",
        "DMA error at runtime - descriptor chain underflows when consumer is faster than producer",
        "Lock timeout at runtime - producer kernel never released lock due to early return",
        "Bus error at runtime on hardware - unaligned vector access trap not seen in simulation",
        "Stack overflow at runtime - deep call chain triggered only by specific input pattern",
        "PLIO/GMIO clock rate mismatch with kernel processing rate - sporadic data corruption",
        "Bug only manifests when -profile flag adds trace stream that consumes one of the kernel's stream slots",
        "Bug only manifests at -O2 because -O0 inserts debug spills that hide an aliasing assumption",
        "Multi-graph design where one graph's idle DMA bursts perturb another graph's kernel cache lines",
    ],
    "cross_cutting": [
        "Window size confusion: samples vs bytes",
        "Iterator width vs computation width mismatch",
        "Accumulator precision vs MAC chain length",
        "Producer-consumer rate mismatch",
        "Memory bank conflicts",
        "Buffer lifetime and double-buffer timing violations",
        "Cascade connectivity constraints",
        "RTP read timing and placement errors",
        "Saturation vs wrap conversion behavior mismatch",
        "Tile memory budget overflow (32KB data + 16KB program)",
    ],
}

LEGACY_BUG_TYPE_TIER_MAP = {
    "wrong_numeric_literal": "easy",
    "simple_operator_swap": "easy",
    "missing_iterator_increment": "easy",
    "wrong_variable_name": "easy",
    "missing_adf_source": "easy",
    "wrong_template_parameter": "easy",
    "wrong_accumulator_type": "easy",
    "missing_stream_read_or_write": "easy",
    "wrong_plio_bit_width": "easy",
    "runtime_ratio_zero": "easy",
    "output_type_mismatch": "easy",
    "duplicate_stream_read": "easy",
    "wrong_to_vector_shift": "easy",
    "uninitialized_accumulator": "easy",
    "missing_output_write": "easy",
    "window_template_mismatch": "easy",
    "reversed_connect_direction": "easy",
    "cascade_type_mismatch": "easy",
    "unconsumed_stream_deadlock": "normal",
    "stream_deadlock_unbalanced_tokens": "normal",
    "wrong_vector_lane_width": "normal",
    "wrong_vector_iterator_width": "normal",
    "graph_buffer_dimension_mismatch": "normal",
    "buffer_size_mismatch": "normal",
    "output_buffer_overrun": "normal",
    "missing_chess_prepare_for_pipelining": "normal",
    "mismatched_plio_width": "normal",
    "off_by_one_oob": "normal",
    "off_by_one_circular_buffer_index": "medium",
    "rtp_read_inside_loop": "medium",
    "wrong_accumulator_mode": "medium",
    "incorrect_shuffle_mode": "medium",
    "incorrect_lock_ordering": "hard",
    "blocking_read_on_async_port": "hard",
    "double_free_buffer_iterator": "extra_hard",
}


def normalize_bug_text(value: str) -> str:
    text = str(value).replace("x", "x").replace("X", "x")
    text = text.replace("\u00d7", "x")
    return re.sub(r"\s+", " ", text.strip().lower())


def slugify_bug_type(label: str) -> str:
    text = normalize_bug_text(label)
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unspecified_bug_type"


def build_bug_taxonomy_entries() -> list[dict]:
    entries = []
    slug_counts: dict[str, int] = {}
    for group, labels in BUG_TAXONOMY.items():
        tier = "normal" if group == "cross_cutting" else group
        for label in labels:
            base_slug = slugify_bug_type(label)
            index = slug_counts.get(base_slug, 0)
            slug_counts[base_slug] = index + 1
            slug = base_slug if index == 0 else f"{base_slug}_{index + 1}"
            entries.append({"slug": slug, "label": label, "tier": tier, "group": group})
    return entries


BUG_TAXONOMY_ENTRIES = build_bug_taxonomy_entries()
BUG_LABEL_TO_SLUG = {normalize_bug_text(entry["label"]): entry["slug"] for entry in BUG_TAXONOMY_ENTRIES}
BUG_TYPE_TIER_MAP = {entry["slug"]: entry["tier"] for entry in BUG_TAXONOMY_ENTRIES}
BUG_TYPE_TIER_MAP.update(LEGACY_BUG_TYPE_TIER_MAP)


def load_source_files() -> list[Path]:
    return sorted(
        path for path in AIE_DATASET_DIR.rglob("*") if path.is_file() and path.suffix in SUPPORTED_SUFFIXES
    )


def load_expanded_source_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []

    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
    return rows


def split_leading_comment(text: str) -> tuple[str, str]:
    body = text.lstrip()
    header = ""

    block_match = re.match(r"/\*.*?\*/", body, re.DOTALL)
    if block_match:
        header = block_match.group(0)
        body = body[block_match.end():]

    while True:
        body = body.lstrip("\n")
        if re.match(r"/\*.*?\*/", body, re.DOTALL):
            body = re.sub(r"^/\*.*?\*/\s*", "", body, count=1, flags=re.DOTALL)
            continue
        line_comment_match = re.match(r"(?:(?:\s*//.*(?:\n|$))+)", body)
        if line_comment_match:
            if not header and "SOURCE:" in line_comment_match.group(0):
                header = line_comment_match.group(0)
            body = body[line_comment_match.end():]
            continue
        break

    return header, body.lstrip("\n")


def parse_header_metadata(header: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for raw_line in header.splitlines():
        line = raw_line.strip().lstrip("*").strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip()
    return metadata


def canonical_stem(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"_CORRECT$", "", stem)
    stem = re.sub(r"_BUGGY.*$", "", stem)
    return stem


def split_top_level_commas(text: str) -> list[str]:
    parts = []
    current = []
    angle_depth = 0
    paren_depth = 0

    for char in text:
        if char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth > 0:
            angle_depth -= 1
        elif char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth > 0:
            paren_depth -= 1

        if char == "," and angle_depth == 0 and paren_depth == 0:
            part = " ".join("".join(current).split())
            if part:
                parts.append(part)
            current = []
            continue

        current.append(char)

    tail = " ".join("".join(current).split())
    if tail:
        parts.append(tail)
    return parts


def normalize_cpp_type(text: str) -> str:
    normalized = " ".join(text.split())
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    normalized = re.sub(r"<\s*", "<", normalized)
    normalized = re.sub(r"\s*>", ">", normalized)
    return normalized


def deterministic_index(key: str, modulo: int) -> int:
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    return int(digest, 16) % modulo


def choose_template(templates: list[str], key: str) -> str:
    return templates[deterministic_index(key, len(templates))]


def normalize_text_for_diversity(text: str) -> str:
    normalized = text.lower()
    normalized = re.sub(r"//.*", "", normalized)
    normalized = re.sub(r"/\*.*?\*/", "", normalized, flags=re.DOTALL)
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def context_diversity_fingerprint(text: str) -> str:
    normalized = normalize_text_for_diversity(text)
    trimmed = normalized[:3000]
    return hashlib.md5(trimmed.encode("utf-8")).hexdigest()


def normalize_metadata_text(value, fallback: str) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else fallback
    if value is None:
        return fallback
    return str(value)


def metadata_prompt_suffix(info: dict, include_bug: bool = True) -> str:
    interfaces = ", ".join(info.get("interfaces", [])) if info.get("interfaces") else "unknown"
    vector_types = ", ".join(info.get("vector_types", [])[:3]) if info.get("vector_types") else "unknown"
    bug_type = info.get("bug_type")
    parts = [f"interfaces = {interfaces}", f"vector_types = {vector_types}"]
    if include_bug and bug_type:
        parts.append(f"bug_type = {bug_type}")
    return " Metadata: " + "; ".join(parts) + "."


def build_source_index(file_infos: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for info in file_infos:
        header = info["header_metadata"]
        if header.get("path"):
            key = str(info["relative_path"].parent / canonical_stem(info["path"]))
            index[key] = info
    return index


def parse_repo_branch(source_value: str | None) -> tuple[str | None, str | None]:
    if not source_value:
        return None, None
    match = re.search(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+),\s*branch\s*([^\s(]+)", source_value)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def build_source_url(info: dict, source_index: dict[str, dict]) -> tuple[str | None, str | None, str | None, str | None]:
    if any(info.get(key) for key in ["source_repo", "source_branch", "source_path", "source_url"]):
        return info.get("source_repo"), info.get("source_branch"), info.get("source_path"), info.get("source_url")

    header = info["header_metadata"]
    repo, branch = parse_repo_branch(header.get("source"))
    source_path = header.get("path")

    if not source_path:
        counterpart_key = str(info["relative_path"].parent / canonical_stem(info["path"]))
        counterpart = source_index.get(counterpart_key)
        if counterpart:
            counterpart_header = counterpart["header_metadata"]
            source_path = counterpart_header.get("path")
            if repo is None:
                repo, branch = parse_repo_branch(counterpart_header.get("source"))

    source_url = None
    if repo and branch and source_path:
        source_url = f"https://github.com/{repo}/blob/{branch}/{source_path}"

    return repo, branch, source_path, source_url


def classify_artifact_type(code: str, path: Path) -> str:
    graph_patterns = [
        r"class\s+\w+\s*:\s*public\s+(?:adf::)?graph",
        r"\binput_plio\b",
        r"\boutput_plio\b",
        r"\bconnect<",
        r"\bkernel::create",
        r"\bdimensions\(",
    ]
    if any(re.search(pattern, code) for pattern in graph_patterns) or "graph" in path.stem.lower():
        return "graph_type"
    return "kernel_type"


def is_relevant_aie_source(code: str, path: Path) -> bool:
    lowered = code.lower()
    markers = [
        "#include <adf.h>",
        "#include <aie_api/aie.hpp>",
        "input_buffer",
        "output_buffer",
        "input_window",
        "output_window",
        "input_stream",
        "output_stream",
        "kernel::create",
        "connect<",
        "class ",
        "aie::",
        "chess_prepare_for_pipelining",
        "window_readincr",
        "window_writeincr",
    ]
    if any(marker in lowered for marker in [item.lower() for item in markers]):
        return True
    if "graph" in path.stem.lower() or "kernel" in path.stem.lower():
        return True
    return False


def unique_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def extract_vector_types(code: str, header_metadata: dict[str, str]) -> list[str]:
    vector_types: list[str] = []
    header_value = header_metadata.get("vector types")
    if header_value:
        vector_types.extend(split_top_level_commas(header_value))

    vector_types.extend(re.findall(r"aie::vector<\s*[^>]+>", code))
    vector_types.extend(re.findall(r"aie::accum<\s*[^>]+>", code))
    vector_types.extend(re.findall(r"\bv\d+[A-Za-z0-9_]+\b", code))
    return unique_preserve_order([normalize_cpp_type(item) for item in vector_types if item])


def extract_intrinsics(code: str, header_metadata: dict[str, str], artifact_type: str) -> list[str]:
    intrinsics: list[str] = []
    header_value = header_metadata.get("key intrinsics")
    if header_value:
        intrinsics.extend(split_top_level_commas(header_value))

    intrinsics.extend(f"aie::{name}" for name in re.findall(r"aie::([A-Za-z_][A-Za-z0-9_]*)", code))

    intrinsic_patterns = [
        r"\breadincr(?:_v\d+)?\b",
        r"\bwriteincr(?:_v\d+)?\b",
        r"\bupd_v\b",
        r"\bsrs\b",
        r"\bmul4\b",
        r"\bmac4\b",
        r"\bconnect(?:<[^>]+>)?\b",
        r"\bdimensions\b",
        r"\bruntime\b",
        r"\blocation\b",
        r"\bbank\b",
        r"\btile\b",
        r"\binitialization_function\b",
    ]
    for pattern in intrinsic_patterns:
        intrinsics.extend(re.findall(pattern, code))

    if artifact_type == "graph_type":
        intrinsics = [item.replace("\\b", "") for item in intrinsics]

    return unique_preserve_order([item for item in intrinsics if item])


def extract_interface_types(code: str, header_metadata: dict[str, str]) -> list[str]:
    interfaces: list[str] = []
    header_value = header_metadata.get("interface")
    if header_value:
        if "window" in header_value.lower():
            interfaces.append("Window")
        if "stream" in header_value.lower():
            interfaces.append("Stream")
        if "cascade" in header_value.lower():
            interfaces.append("Cascade")
        if "plio" in header_value.lower():
            interfaces.append("PLIO")

    code_lower = code.lower()
    if any(token in code_lower for token in ["input_buffer", "output_buffer", "window<", "window_"]):
        interfaces.append("Window")
    if any(token in code_lower for token in ["input_stream", "output_stream", "connect<stream>"]):
        interfaces.append("Stream")
    if "connect<cascade>" in code_lower:
        interfaces.append("Cascade")
    if any(token in code_lower for token in ["input_plio", "output_plio", "plio_"]):
        interfaces.append("PLIO")
    return unique_preserve_order(interfaces)


def extract_math_operation(path: Path, code: str) -> str:
    stem = path.stem.lower()
    text = code.lower()
    if "peak_detect" in stem:
        return "Peak detection and derived feature generation: compute a vector maximum, compute a vector minimum, scale each lane by PI, and emit both pass-through data and transformed floating-point output."
    if "upscale" in stem:
        return "Threshold-based scaling: consume a floating-point vector and a scalar stream, then scale the vector by either 1 or 2 depending on the streamed maximum value."
    if "data_shuffle" in stem:
        return "Vector rearrangement: broadcast a scalar control value from a stream and use shuffle_up_fill to inject that value into an int32 vector stream."
    if "fir" in stem or "fir" in text:
        return "Finite impulse response filtering using complex multiply-accumulate across streamed samples and tap coefficients."
    if "beamforming" in stem or "bfcascadechain" in text or "bf8x8" in text:
        return "Beamforming accumulation across multiple antenna or layer streams using cascaded kernels and complex MAC operations."
    if "graph" in stem or "converter" in stem:
        return "ADF graph orchestration that wires kernels, interfaces, and buffer dimensions into a complete AIE dataflow pipeline."
    return "AIE dataflow computation over vectorized kernels and graph connections."


def summarize_purpose(path: Path, artifact_type: str, interfaces: list[str], vector_types: list[str], math_operation: str) -> str:
    interface_text = ", ".join(interfaces) if interfaces else "AIE interfaces"
    vector_text = ", ".join(vector_types[:3]) if vector_types else "scalar and vector AIE data types"
    artifact_label = "ADF graph" if artifact_type == "graph_type" else "AIE kernel"
    return f"This {artifact_label} uses {interface_text} interfaces and {vector_text} to implement {math_operation.rstrip('.')}"


def split_signature_params(params_blob: str) -> list[str]:
    return split_top_level_commas(params_blob)


def extract_kernel_io_ports(code: str) -> str:
    signature_match = re.search(r"\b\w+(?:::\w+)?\s*<[^>]+>::\w+\((.*?)\)\s*\{|\bvoid\s+\w+\s*\((.*?)\)\s*\{", code, re.DOTALL)
    if not signature_match:
        return "I/O ports are not trivially recoverable from the source signature."
    params_blob = next(group for group in signature_match.groups() if group is not None)
    params_blob = re.sub(r"//.*", "", params_blob)
    params = split_signature_params(params_blob)
    if not params:
        return "Kernel has no explicit ports in the parsed signature."
    return "Kernel ports: " + "; ".join(params) + "."


def extract_graph_io_ports(code: str) -> str:
    kernel_count = len(re.findall(r"kernel::create(?:_object)?", code))
    declarations = []
    for keyword in ["input_port", "output_port", "input_plio", "output_plio"]:
        matches = re.findall(rf"^\s*{keyword}\b[^;]*;", code, re.MULTILINE)
        for match in matches:
            declarations.append(" ".join(match.split()))

    if declarations:
        return "Graph port declarations: " + " ".join(declarations) + f" Internal kernel creations detected: {kernel_count}."
    return f"Graph port declarations are not explicit in simple one-line form. Internal kernel creations detected: {kernel_count}."


def extract_buffer_requirements(code: str, artifact_type: str, interfaces: list[str], vector_types: list[str]) -> str:
    dimension_matches = re.findall(r"dimensions\([^\)]+\)\s*=\s*\{([^}]+)\}", code)
    window_matches = re.findall(r"connect<window<\s*([^>]+)\s*>>", code)
    begin_vector_matches = re.findall(r"begin_vector<\s*(\d+)\s*>", code)

    requirements = []
    if dimension_matches:
        unique_dimensions = unique_preserve_order([item.strip() for item in dimension_matches])
        requirements.append(
            f"Explicit graph dimensions appear {len(dimension_matches)} times with sizes: " + "; ".join(unique_dimensions) + "."
        )
    if window_matches:
        requirements.append("Window connections declare byte extents: " + "; ".join(item.strip() for item in window_matches) + ".")
    if begin_vector_matches:
        lane_sizes = unique_preserve_order(begin_vector_matches)
        requirements.append(
            "Kernel vector iterators consume lanes of size " + ", ".join(lane_sizes) + " per access; upstream buffers must be sized to sustain those bursts."
        )
    if not requirements and artifact_type == "kernel_type":
        if "Stream" in interfaces:
            requirements.append("No explicit window dimensions appear in the kernel; correct execution depends on balanced stream token rates across producer and consumer kernels.")
        else:
            vector_text = ", ".join(vector_types[:2]) if vector_types else "the declared vector accesses"
            requirements.append(f"The kernel relies on buffers consistent with {vector_text}; window sizes must align with the iterator width and loop trip count.")
    if not requirements and artifact_type == "graph_type":
        requirements.append("The graph relies on interface-level buffer sizing, runtime ratios, and port topology rather than kernel-local allocations.")
    return " ".join(requirements)


def build_constraints_text(code: str, artifact_type: str, interfaces: list[str], intrinsics: list[str]) -> str:
    constraints = []
    if artifact_type == "graph_type":
        if "connect<cascade>" in code:
            constraints.append("Cascade links impose tile-to-tile ordering constraints and require placement-aware scheduling across the chain.")
        if "location<kernel>" in code or "bank(" in code:
            constraints.append("The graph encodes explicit tile and memory-bank placement, so tiling is a first-order correctness and performance constraint.")
        if "runtime<ratio>" in code:
            constraints.append("Runtime ratios guide AIE scheduling and can expose throughput bottlenecks when kernels are overcommitted.")
        if "PLIO" in " ".join(interfaces):
            constraints.append("PLIO bandwidth and packetization must stay aligned with the connected kernel rates to avoid stalls at graph boundaries.")
    else:
        if any(item in " ".join(intrinsics) for item in ["mul4", "mac4", "aie::mul", "aie::add"]):
            constraints.append("The kernel is written for SIMD and VLIW issue slots, so vector-friendly instruction ordering matters for throughput.")
        if "chess_prepare_for_pipelining" in code or "chess_loop_range" in code:
            constraints.append("CHESS pipelining pragmas indicate the compiler is expected to software-pipeline the loop for the target tile.")
        if "Stream" in interfaces:
            constraints.append("Stream interfaces require exact producer-consumer balance; missing reads or writes can deadlock the graph.")
        if "Window" in interfaces:
            constraints.append("Window and buffer iterators must remain aligned with the intended vector width and tile-local memory limits.")
    if not constraints:
        constraints.append("The implementation depends on AIE tile-level parallelism, deterministic data movement, and compile-time scheduling assumptions.")
    return " ".join(constraints)


def source_group_key(info: dict) -> str:
    source_repo = info.get("source_repo") or "local"
    source_path = info.get("source_path")
    if source_path:
        normalized = source_path.replace("\\", "/")
    else:
        normalized = str(info["relative_path"]).replace("\\", "/")
    normalized = re.sub(r"_CORRECT(?=\.)", "", normalized)
    normalized = re.sub(r"_BUGGY[^/.]*(?=\.)", "", normalized)
    return f"{source_repo}:{normalized}"


def build_instruction(info: dict) -> str:
    key = f"{source_group_key(info)}:general"
    if info["artifact_type"] == "graph_type":
        return choose_template(GENERAL_GRAPH_TEMPLATES, key)
    return choose_template(GENERAL_KERNEL_TEMPLATES, key)


def build_response(info: dict) -> str:
    artifact_type = info["artifact_type"]
    code = info["context"]
    vector_types = info["vector_types"]
    intrinsics = info["intrinsics"]
    interfaces = info["interfaces"]
    math_operation = info["math_operation"]
    summary = info["summary"]
    io_ports = extract_graph_io_ports(code) if artifact_type == "graph_type" else extract_kernel_io_ports(code)
    buffer_requirements = extract_buffer_requirements(code, artifact_type, interfaces, vector_types)
    constraints = build_constraints_text(code, artifact_type, interfaces, intrinsics)
    vector_text = ", ".join(vector_types) if vector_types else "No explicit vector types detected"
    intrinsic_text = ", ".join(intrinsics[:12]) if intrinsics else "No explicit AIE intrinsics detected"
    interface_text = ", ".join(interfaces) if interfaces else "No explicit interface type detected"

    summary_text = summary.rstrip(".")
    return (
        f"Summary: {summary_text}.\n\n"
        f"Artifact classification: {'ADF graph' if artifact_type == 'graph_type' else 'AIE kernel'}. {io_ports}\n\n"
        f"Interfaces: {interface_text}.\n"
        f"Vector types: {vector_text}.\n"
        f"Intrinsics and AIE APIs: {intrinsic_text}.\n\n"
        f"Buffer requirements: {buffer_requirements}\n\n"
        f"Mathematical or architectural role: {math_operation}\n\n"
        f"AIE-specific constraints: {constraints}"
    )


def build_feature_response(info: dict) -> str:
    io_ports = extract_graph_io_ports(info["context"]) if info["artifact_type"] == "graph_type" else extract_kernel_io_ports(info["context"])
    vector_text = ", ".join(info["vector_types"]) if info["vector_types"] else "No explicit vector types detected"
    intrinsic_text = ", ".join(info["intrinsics"][:12]) if info["intrinsics"] else "No explicit AIE intrinsics detected"
    interface_text = ", ".join(info["interfaces"]) if info["interfaces"] else "No explicit interface type detected"
    return (
        f"Classification: {'ADF graph' if info['artifact_type'] == 'graph_type' else 'AIE kernel'}. {io_ports}\n\n"
        f"Interfaces: {interface_text}.\n"
        f"Vector types: {vector_text}.\n"
        f"AIE intrinsics and APIs: {intrinsic_text}.\n"
        f"Primary operation: {info['math_operation']}"
    )


def build_debug_response(info: dict) -> str:
    steps = []
    if info.get("bug_type"):
        steps.append(f"1. Start from the declared failure mode `{info['bug_type']}` and identify the exact assumption that it breaks.")
    else:
        steps.append("1. Start from the interfaces and loop structure to identify the first assumption that must hold for correct execution.")

    if "Stream" in info["interfaces"]:
        steps.append("2. Check token production and consumption at every stream edge. A missing read or write first creates backpressure, then stalls adjacent producers or consumers, and finally deadlocks the graph.")
    elif "Window" in info["interfaces"]:
        steps.append("2. Check window extents, iterator stride, and loop trip counts. The first mismatch causes a wrong access boundary, which then propagates into corrupted computation or out-of-bounds memory behavior.")
    else:
        steps.append("2. Check how the declared interfaces align with the kernel or graph schedule, because the first mismatch typically appears at the data-movement boundary.")

    if info["artifact_type"] == "graph_type":
        steps.append("3. Follow the effect into graph topology: mismatched dimensions, runtime ratios, or connect patterns accumulate imbalance, so either tokens queue indefinitely or throughput collapses at a downstream kernel.")
    else:
        steps.append("3. Follow the effect inside the kernel: vector width mismatches, accumulator misuse, or incorrect iterator movement distort the computed lanes and then surface as bad outputs, stalls, or illegal accesses.")

    symptom = info.get("symptom") or "stalls, deadlock, corrupted outputs, or memory faults"
    steps.append(f"4. The externally visible symptom is {symptom}, because the earlier mismatch is not absorbed locally and instead propagates through the AIE pipeline.")
    return "\n".join(steps)


def build_dataflow_instruction(info: dict) -> str:
    key = f"{source_group_key(info)}:dataflow"
    if info["artifact_type"] == "graph_type":
        return choose_template(DATAFLOW_GRAPH_TEMPLATES, key)
    return choose_template(DATAFLOW_KERNEL_TEMPLATES, key)


def build_dataflow_response(info: dict) -> str:
    io_ports = extract_graph_io_ports(info["context"]) if info["artifact_type"] == "graph_type" else extract_kernel_io_ports(info["context"])
    buffer_requirements = extract_buffer_requirements(info["context"], info["artifact_type"], info["interfaces"], info["vector_types"])
    constraints = build_constraints_text(info["context"], info["artifact_type"], info["interfaces"], info["intrinsics"])
    return (
        f"Dataflow role: {info['math_operation']}\n\n"
        f"I/O structure: {io_ports}\n\n"
        f"Buffering and transfer requirements: {buffer_requirements}\n\n"
        f"Deployment constraints: {constraints}"
    )


def build_feature_instruction(info: dict) -> str:
    key = f"{source_group_key(info)}:feature"
    if info["artifact_type"] == "graph_type":
        return choose_template(FEATURE_GRAPH_TEMPLATES, key)
    return choose_template(FEATURE_KERNEL_TEMPLATES, key)


def build_debug_instruction(info: dict) -> str:
    key = f"{source_group_key(info)}:debug"
    if info["artifact_type"] == "graph_type":
        return choose_template(CAUSAL_DEBUG_GRAPH_TEMPLATES, key)
    return choose_template(CAUSAL_DEBUG_KERNEL_TEMPLATES, key)


def build_entry_variant(info: dict, instruction: str, response: str, variant: str) -> dict:
    entry = build_entry(info)
    entry["instruction"] = instruction
    entry["response"] = response
    entry["metadata"] = dict(entry["metadata"])
    entry["metadata"]["variant"] = variant
    return entry


def build_entries_for_info(info: dict) -> list[dict]:
    if V2_MODE:
        # V2 is a debug-only dataset: drop structured_extraction / deep_explanation / causal_debugging
        # prose variants so the model is trained purely on "buggy source -> full corrected source".
        return []
    entries = [
        build_entry_variant(info, build_feature_instruction(info), build_feature_response(info), "structured_extraction"),
        build_entry_variant(info, build_instruction(info), build_response(info), "deep_explanation"),
        build_entry_variant(info, build_debug_instruction(info), build_debug_response(info), "causal_debugging"),
    ]
    return entries


def select_taxonomy_contexts(file_infos: list[dict], bug_slug: str, count: int) -> list[dict]:
    pool = [info for info in file_infos if info.get("context")]
    if not pool or count <= 0:
        return []

    pool.sort(key=lambda info: hashlib.md5(source_group_key(info).encode("utf-8")).hexdigest())
    start = deterministic_index(bug_slug, len(pool))
    ordered = [pool[(start + offset) % len(pool)] for offset in range(len(pool))]

    def info_repo(info: dict) -> str:
        return info.get("source_repo") or "local"

    def info_parent(info: dict) -> str:
        return source_parent_key(info)

    selected: list[dict] = []
    seen_groups: set[str] = set()
    seen_parents: set[str] = set()
    seen_repos: set[str] = set()

    # Pass 1: maximize distinct source_repo.
    for info in ordered:
        if len(selected) >= count:
            break
        repo = info_repo(info)
        group = source_group_key(info)
        if repo in seen_repos or group in seen_groups:
            continue
        selected.append(info)
        seen_repos.add(repo)
        seen_parents.add(info_parent(info))
        seen_groups.add(group)

    # Pass 2: fill by distinct source_parent_key (file/directory parent).
    if len(selected) < count:
        for info in ordered:
            if len(selected) >= count:
                break
            parent = info_parent(info)
            group = source_group_key(info)
            if parent in seen_parents or group in seen_groups:
                continue
            selected.append(info)
            seen_parents.add(parent)
            seen_groups.add(group)

    # Pass 3: fill by distinct source_group.
    if len(selected) < count:
        for info in ordered:
            if len(selected) >= count:
                break
            group = source_group_key(info)
            if group in seen_groups:
                continue
            selected.append(info)
            seen_groups.add(group)

    # Pass 4: fallback allow duplicates if the pool is smaller than requested.
    if len(selected) < count:
        for info in ordered:
            if len(selected) >= count:
                break
            if info in selected:
                continue
            selected.append(info)

    return selected


def build_taxonomy_debug_response(info: dict, bug_entry: dict) -> str:
    bug_label = bug_entry["label"]
    bug_slug = bug_entry["slug"]
    artifact_name = "ADF graph" if info.get("artifact_type") == "graph_type" else "AIE kernel"

    return (
        f"Likely root-cause pattern: `{bug_label}` ({bug_slug}).\n\n"
        f"1. Start by validating the first interface and iterator assumptions in this {artifact_name}.\n"
        "2. Trace where token rate, window extent, vector width, or accumulator assumptions diverge from expected contracts.\n"
        "3. Confirm the first divergence with a focused simulation trace and one minimal instrumentation point near the boundary.\n"
        "4. Apply the smallest possible fix that restores the violated contract without introducing new mismatches elsewhere.\n\n"
        f"Reference architecture summary:\n{build_response(info)}"
    )


def mutator_for_bug_slug(bug_slug: str):
    table = {
        "stream_deadlock_unbalanced_tokens": mutate_stream_deadlock,
        "missing_output_write": mutate_missing_output_write,
        "off_by_one_oob": mutate_window_oob,
        "missing_iterator_increment": mutate_missing_iterator_increment,
        "runtime_ratio_zero": mutate_runtime_ratio_zero,
        "mismatched_plio_width": mutate_plio_width_mismatch,
        "reversed_connect_direction": mutate_reversed_connect_direction,
        "wrong_vector_lane_width": mutate_wrong_vector_lane_width,
        "graph_buffer_dimension_mismatch": mutate_graph_dimension_mismatch,
        # Extended mutators - each targets a concrete BUG_TAXONOMY slug so coverage grows.
        "subtraction_instead_of_addition": mutate_subtraction_for_addition,
        "aie_add_used_instead_of_aie_mul": mutate_mul_to_add,
        "to_vector_shift_parameter_is_15_instead_of_0": mutate_tovector_shift_fifteen,
        "acc48_instead_of_acc80_for_int32xint32": mutate_acc48_for_acc80,
        "wrong_loop_count_16_instead_of_32": mutate_loop_count_halved,
        "window_size_uses_wrong_literal_128_instead_of_256": mutate_window_size_halved,
        "accumulator_initialized_with_aie_mul_garbage_instead_of_aie_zeros": mutate_remove_zeros_init,
        "broadcast_width_does_not_match_vector_width_broadcast_int16_4_with_vector_int16_8": mutate_broadcast_width_mismatch,
        "reading_from_wrong_stream_variable_in_a_twice_instead_of_in_a_then_in_b": mutate_duplicate_stream_read,
        "stream_kernel_drops_last_sample_in_each_block_from_n_1_loop_bound": mutate_drop_last_sample,
        # New round: graph-level and scheduling mutators.
        "missing_chess_prepare_for_pipelining_causing_3x_throughput_loss": mutate_remove_chess_pipelining,
        "readincr_from_output_stream_instead_of_input_stream": mutate_readincr_from_output,
        "break_statement_in_pipelined_loop_chess_compiler_cannot_pipeline_with_early_exits": mutate_break_in_pipelined_loop,
        "runtime_ratios_do_not_add_up_three_kernels_on_same_tile_sum_to_1_0": mutate_runtime_ratio_overflow,
        "port_index_out_of_range_k1_in_2_when_kernel_only_has_2_inputs": mutate_port_index_oob,
        "graph_wait_missing_after_graph_run_host_reads_output_before_kernels_finish": mutate_missing_graph_wait,
        "missing_connection_kernel_port_declared_but_never_connected_in_graph_constructor": mutate_missing_connect,
        "self_loop_kernel_output_connected_back_to_its_own_input_without_intermediate_buffer": mutate_self_loop_connect,
        "to_vector_output_type_does_not_match_buffer_type_int32_vs_int16": mutate_wrong_to_vector_output_type,
        "missing_adf_source_assignment": mutate_missing_adf_source,
        "missing_adf_runtime_line_entirely": mutate_missing_runtime_ratio,
        "window_margin_larger_than_window_size_invalid_configuration": mutate_window_margin_to_size,
        "bfloat16_kernel_on_aie1_tile_bfloat16_only_supported_on_aie_ml": mutate_bfloat16_on_aie1,
        "signed_unsigned_mismatch_uint16_data_processed_as_int16_causing_sign_extension_errors": mutate_signed_unsigned_mismatch,
        "unaligned_access_pointer_not_aligned_to_128_bit_boundary_for_vector_load": mutate_unaligned_load,
        "modulo_operation_in_loop_for_circular_buffer_should_use_chess_circular_buffer_pragma": mutate_modulo_in_loop,
        "accumulator_not_reset_between_output_blocks_dc_offset_accumulates_across_calls": mutate_break_accumulator_reset,
        "begin_vector_width_not_a_power_of_2_e_g_begin_vector_6_which_is_invalid_on_aie": mutate_begin_vector_non_power_of_two,
        # V2 dedicated: declaration-vs-dereference scope - moves the dereference
        # OUT of the loop, creating a stale-data bug. This teaches the model the
        # distinction between iterator declaration site (outside) and dereference
        # site (inside), which was weakly covered before.
        "data_read_once_outside_loop_instead_of_inside_stale_data": mutate_dereference_moved_outside_loop,
        "input_data_read_once_used_in_all_loop_iterations_not_refreshed": mutate_dereference_moved_outside_loop,
    }
    return table.get(bug_slug)


def bug_pattern_hint(bug_label: str) -> str:
    text = bug_label.lower()
    hints: list[tuple[str, str]] = [
        ("deadlock", "missing or unbalanced stream reads/writes that would create producer-consumer backpressure"),
        ("plio", "PLIO declarations, their bit widths, and the kernel ports they connect to"),
        ("runtime ratio", "runtime<ratio> annotations and the tile budget they imply"),
        ("connect", "adf::connect calls and the direction of their source/destination ports"),
        ("cascade", "cascade stream declarations and the adjacency they require"),
        ("window", "window<...> sizes, margins, and iterator strides"),
        ("vector", "aie::vector<> lane counts and begin_vector<> widths"),
        ("accumulator", "aie::accum types, lane counts, and to_vector shifts"),
        ("iterator", "iterator increments inside the kernel loop"),
        ("loop", "loop bounds, pipelining pragmas, and trip counts"),
        ("bank", "memory bank and placement annotations"),
        ("tile", "explicit tile coordinates and placement constraints"),
        ("cint", "complex-number vector types and their multiply/accumulate APIs"),
        ("float", "floating-point accumulator usage (accfloat)"),
        ("int8", "int8 vector width and broadcast/load APIs"),
        ("bfloat16", "bfloat16 usage and AIE-ML vs AIE1 tile compatibility"),
    ]
    for key, hint in hints:
        if key in text:
            return hint
    return "the specific interface, vector, or scheduling construct named by the bug pattern"


def _synth_variant_transform(buggy: str, correct: str, idx: int) -> tuple[str, str]:
    """Deterministically perturb kernel names and a few safe constants so that
    repeated synthesis for one taxonomy slug produces textually distinct rows
    while preserving the injected bug signal."""
    suffixes = ["", "_v2", "_v3", "_stage_a", "_stage_b", "_pass1", "_pass2", "_cycle3",
                "_blk0", "_blk1", "_blk2", "_blk3", "_alt", "_retry", "_probe"]
    loop_bounds = [32, 24, 40, 48, 16, 56, 28, 20, 36, 44, 52, 60, 12, 64, 8]
    names = ["fir_kernel", "scale_kernel", "stream_kernel",
             "cascade_stage1", "cascade_stage2", "rtp_kernel", "lock_kernel"]
    sfx = suffixes[idx % len(suffixes)]
    bound = loop_bounds[idx % len(loop_bounds)]

    def swap(src: str) -> str:
        if sfx:
            for n in names:
                src = re.sub(rf"\b{re.escape(n)}\b", n + sfx, src)
        if bound != 32:
            src = re.sub(r"(?<=for \(int i = 0; i < )32(?=;)", str(bound), src)
        return src

    return swap(buggy), swap(correct)


def build_synthesized_code_debug_entry(bug_entry: dict, scenario_index: int) -> dict | None:
    """Emit a V2 debug row backed by `synthesize_for_slug` output. Used as the
    fallback for taxonomy slugs that have no natural mutator, so every slug
    produces real buggy/correct code (no description-only rows)."""
    # Lazy import to avoid a circular import: synthesize_taxonomy_bugs imports
    # BUG_TAXONOMY_ENTRIES from this module.
    from synthesize_taxonomy_bugs import synthesize_for_slug as _synth_for_slug
    slug = bug_entry["slug"]
    label = bug_entry["label"]
    tier = bug_entry.get("tier") or "normal"
    try:
        buggy, correct = _synth_for_slug(slug, label, tier)
    except Exception:
        return None
    if not buggy or not correct or buggy == correct:
        return None
    buggy, correct = _synth_variant_transform(buggy, correct, scenario_index)
    if buggy == correct:
        return None

    rel = f"synthetic/taxonomy/{slug}/variant_{scenario_index}.cc"
    group_key = f"synth:{slug}"
    symptom = pick_symptom(slug, group_key + ":" + str(scenario_index))
    diff_text = build_unified_diff(buggy, correct, "buggy", "correct")
    seed_key = rel + "|synth:" + slug + "|" + str(scenario_index)
    # Split synthetic rows deterministically by slug so no (buggy, correct)
    # pair for the same slug ever appears on both sides of the train/val line.
    split = "validation" if deterministic_index(group_key, 5) == 0 else "train"

    if V2_MODE:
        instruction = build_v2_instruction(seed_key, tier, symptom)
        response = build_v2_code_response(correct, rel)
    else:
        instruction = (
            f"Debug scenario {scenario_index}: the buggy AIE source below exhibits the `{label}` pattern. "
            "Return the minimal fix as a unified diff."
        )
        response = (
            f"Verdict: this source matches `{label}` ({slug}). Observed symptom: {symptom}.\n\n"
            "Minimal fix (unified diff, buggy -> correct):\n"
            "```diff\n" + diff_text + "\n```\n\n"
            f"Why the correction restores the contract: {minimal_fix_text({'bug_type': slug}, slug)}"
        )

    return {
        "instruction": instruction,
        "context": (
            f"Scenario bug pattern: {label}\n"
            f"Source: {rel}\n\n"
            "Buggy version:\n"
            + buggy.rstrip()
            + "\n\nCorrect version:\n"
            + correct
        ),
        "response": response,
        "metadata": {
            "source": rel,
            "source_repo": f"synthetic_taxonomy:{slug}",
            "source_branch": None,
            "source_path": rel,
            "relative_path": rel,
            "type": "kernel_type",
            "category": "synthetic_taxonomy",
            "hardware": "Versal AIE",
            "interfaces": [],
            "vector_types": [],
            "intrinsics": [],
            "split": split,
            "variant": "synthetic_taxonomy_bug_fix",
            "bug_type": slug,
            "bug_label": label,
            "symptom": symptom,
            "difficulty_tier": tier,
            "taxonomy_group": bug_entry.get("group"),
            "source_group": group_key,
            "synthetic": True,
            "verdict": "present",
        },
    }


def build_taxonomy_inspection_entry(info: dict, bug_entry: dict, scenario_index: int) -> dict:
    bug_label = bug_entry["label"]
    bug_slug = bug_entry["slug"]
    anchors = extract_code_anchors(info["context"])
    anchor_text = anchor_phrase(anchors)
    hint = bug_pattern_hint(bug_label)
    rel = str(info["relative_path"]).replace("\\", "/")

    instruction = (
        f"Inspection task {scenario_index}: examine the AIE source below and determine whether it exhibits the bug pattern "
        f"`{bug_label}`. If the pattern is present, point to the exact lines and explain the minimal fix. "
        "If the pattern is not evident in this source, say so explicitly and justify your answer from the code."
    )
    if V2_MODE:
        seed_key = rel + "|insp:" + bug_slug + "|" + str(scenario_index)
        instruction = build_v2_inspection_instruction(seed_key, bug_label, multi_file=False)
        response = build_v2_inspection_verdict(seed_key, bug_label, multi_file=False)
    else:
        response = (
            f"Verdict: the `{bug_label}` pattern is not evident in this source. "
            f"{anchor_text}To judge this, look specifically at {hint}; the source does not show the characteristic "
            "construction that would produce this failure mode.\n\n"
            "What the source actually does (for context):\n"
            f"{build_response(info)}\n\n"
            "What such a bug would look like if it were present: an explicit, localized mismatch in "
            f"{hint} near the kernel or graph boundary. Because that mismatch is absent here, no fix is warranted."
        )

    return {
        "instruction": instruction,
        "context": (
            f"Candidate bug pattern to check: {bug_label}\n"
            f"Source: {rel}\n\n"
            f"{info['context']}"
        ),
        "response": response,
        "metadata": {
            "source": info["source_url"] or rel,
            "source_repo": info["source_repo"],
            "source_branch": info["source_branch"],
            "source_path": info["source_path"],
            "relative_path": rel,
            "type": info["artifact_type"],
            "category": info["domain"],
            "hardware": "Versal AIE",
            "interfaces": info["interfaces"],
            "vector_types": info["vector_types"],
            "intrinsics": info["intrinsics"],
            "split": info["split"],
            "variant": "taxonomy_inspection_negative",
            "bug_type": bug_slug,
            "bug_label": bug_label,
            "difficulty_tier": bug_entry["tier"],
            "taxonomy_group": bug_entry["group"],
            "source_group": source_group_key(info),
            "verdict": "not_present",
        },
    }


def build_taxonomy_mutated_entry(info: dict, bug_entry: dict, scenario_index: int) -> dict | None:
    mutator = mutator_for_bug_slug(bug_entry["slug"])
    if mutator is None:
        return None
    buggy_context = mutator(info["context"])
    if not buggy_context or buggy_context == info["context"]:
        return None

    bug_slug = bug_entry["slug"]
    bug_label = bug_entry["label"]
    symptom = pick_symptom(bug_slug, source_group_key(info) + ":tax:" + bug_slug)
    anchors = extract_code_anchors(info["context"])
    anchor_text = anchor_phrase(anchors)
    rel = str(info["relative_path"]).replace("\\", "/")

    diff_text = build_unified_diff(buggy_context, info["context"], "buggy", "correct")

    if V2_MODE:
        seed_key = rel + "|tax:" + bug_slug + "|" + str(scenario_index)
        instruction = build_v2_instruction(seed_key, bug_entry.get("tier") or "normal", symptom)
        response = build_v2_code_response(info["context"], info.get("relative_path"))
    else:
        instruction = (
            f"Debug scenario {scenario_index}: the buggy AIE source below exhibits the `{bug_label}` pattern. "
            "Locate the offending lines and provide the minimal fix as a unified diff."
        )
        response = (
            f"Verdict: this source matches `{bug_label}` ({bug_slug}). {anchor_text}Observed symptom: {symptom}.\n\n"
            "Minimal fix (unified diff, buggy -> correct):\n"
            "```diff\n"
            + diff_text
            + "\n```\n\n"
            f"Why the correction restores the contract: {minimal_fix_text(info, bug_slug)}"
        )

    return {
        "instruction": instruction,
        "context": (
            f"Scenario bug pattern: {bug_label}\n"
            f"Source: {rel}\n\n"
            "Buggy version:\n"
            + buggy_context.rstrip()
            + "\n\nCorrect version:\n"
            + info["context"]
        ),
        "response": response,
        "metadata": {
            "source": info["source_url"] or rel,
            "source_repo": info["source_repo"],
            "source_branch": info["source_branch"],
            "source_path": info["source_path"],
            "relative_path": rel,
            "type": info["artifact_type"],
            "category": info["domain"],
            "hardware": "Versal AIE",
            "interfaces": info["interfaces"],
            "vector_types": info["vector_types"],
            "intrinsics": info["intrinsics"],
            "split": info["split"],
            "variant": "taxonomy_debug_scenario",
            "bug_type": bug_slug,
            "bug_label": bug_label,
            "symptom": symptom,
            "difficulty_tier": bug_entry["tier"],
            "taxonomy_group": bug_entry["group"],
            "source_group": source_group_key(info),
            "synthetic": True,
            "verdict": "present",
        },
    }


def build_taxonomy_debug_scenario_entries(file_infos: list[dict], scenarios_per_bug_type: int = TAXONOMY_SCENARIOS_PER_BUG_TYPE) -> list[dict]:
    rows: list[dict] = []
    if scenarios_per_bug_type <= 0:
        return rows

    for bug_entry in BUG_TAXONOMY_ENTRIES:
        tier = bug_entry.get("tier") or "normal"
        scenarios = TAXONOMY_SCENARIOS_PER_TIER.get(tier, scenarios_per_bug_type)
        if scenarios <= 0:
            continue
        has_mutator = mutator_for_bug_slug(bug_entry["slug"]) is not None
        # For mutator-backed slugs, sweep a much larger pool to fill the mutated quota;
        # otherwise we fall back to synthesizer-backed code debug rows (no file pool needed).
        pool_size = max(scenarios * 20, 64) if has_mutator else scenarios * 2
        contexts = select_taxonomy_contexts(file_infos, bug_entry["slug"], pool_size)
        produced = 0
        scenario_index = 0
        for info in contexts:
            if produced >= scenarios:
                break
            scenario_index += 1
            mutated_entry = build_taxonomy_mutated_entry(info, bug_entry, scenario_index)
            if mutated_entry is not None:
                rows.append(mutated_entry)
                produced += 1
                continue
            if has_mutator:
                # This file didn't apply cleanly; skip it and let the next context try.
                # Do NOT emit inspection-negative rows — the dataset is code-debug only.
                continue
            # No mutator for this slug: fall back to a synthesized code-debug pair.
            synth_entry = build_synthesized_code_debug_entry(bug_entry, scenario_index)
            if synth_entry is not None:
                rows.append(synth_entry)
                produced += 1
        # If we couldn't find any natural context (no files matched) AND there's no
        # mutator, still emit synthesized rows so the slug gets coverage.
        while not has_mutator and produced < scenarios:
            scenario_index += 1
            synth_entry = build_synthesized_code_debug_entry(bug_entry, scenario_index)
            if synth_entry is None:
                break
            rows.append(synth_entry)
            produced += 1

        # Safety net: a mutator-backed slug where NO context matched cleanly would
        # otherwise produce zero rows. Fall back to synthesis so every taxonomy
        # slug has real code-debug training data.
        if produced == 0:
            for fallback_idx in range(1, max(scenarios, 1) + 1):
                synth_entry = build_synthesized_code_debug_entry(bug_entry, fallback_idx)
                if synth_entry is None:
                    break
                rows.append(synth_entry)

    return rows


def minimal_fix_text(info: dict, bug_type: str | None) -> str:
    bug = bug_type or info.get("bug_type") or "generic_aie_mismatch"
    fix_map = {
        "stream_deadlock_unbalanced_tokens": "Restore token balance by reintroducing the missing stream read or write at the exact point where one side of the edge stopped participating.",
        "off_by_one_oob": "Tighten the loop bound or iterator update so the last legal element is processed exactly once and no access crosses the buffer boundary.",
        "graph_buffer_dimension_mismatch": "Make the graph-side dimensions or window extents match the kernel-side consumption pattern so producer and consumer sizes agree again.",
        "synthetic mutation": "Undo the injected mismatch at the first broken interface or loop boundary rather than compensating later in the pipeline.",
    }
    return fix_map.get(bug, "Reinstate the original data-movement or synchronization assumption at the first point where the buggy version diverges from the correct implementation.")


def canonical_bug_type(bug_type: str | None) -> str | None:
    if not bug_type:
        return None

    raw = str(bug_type).strip()
    normalized = normalize_bug_text(raw)
    if normalized in BUG_LABEL_TO_SLUG:
        return BUG_LABEL_TO_SLUG[normalized]
    if normalized in BUG_TYPE_TIER_MAP:
        return normalized

    slug = slugify_bug_type(normalized)
    if slug in BUG_TYPE_TIER_MAP:
        return slug
    return slug


def infer_bug_tier(bug_type: str | None) -> str:
    if not bug_type:
        return "normal"

    normalized = canonical_bug_type(bug_type)
    if normalized in BUG_TYPE_TIER_MAP:
        return BUG_TYPE_TIER_MAP[normalized]

    if any(token in normalized for token in ["deadlock", "overflow", "mismatch", "vector", "window", "plio"]):
        return "normal"
    if any(token in normalized for token in ["lock", "bank", "placement", "schedule"]):
        return "hard"
    return "medium"


def build_tiered_bug_instruction(info: dict, bug_type: str | None, symptom: str | None) -> tuple[str, str]:
    tier = infer_bug_tier(bug_type)
    bug_label = info.get("bug_label") or bug_type or info.get("bug_type") or "aie_integration_bug"
    symptom_label = symptom or info.get("symptom") or "the design hangs, corrupts output, or underperforms"

    if V2_MODE:
        seed_key = (
            str(info.get("relative_path") or "")
            + "|" + str(bug_type or "")
            + "|" + str(symptom or "")
        )
        return build_v2_instruction(seed_key, tier, symptom), tier

    if tier == "easy":
        instruction = (
            f"This AIE source has a `{bug_label}` issue. The bug is explicit. "
            "Locate the exact wrong line and fix it with the minimal change."
        )
    elif tier == "normal":
        instruction = (
            f"This AIE source shows symptom: {symptom_label}. "
            f"The likely area is related to `{bug_label}`. Find and fix the root cause."
        )
    elif tier == "medium":
        instruction = (
            f"This AIE source compiles but shows runtime symptom: {symptom_label}. "
            "Diagnose the true integration bug and provide the minimal correction."
        )
    elif tier == "hard":
        instruction = (
            f"During AIE simulation or deployment this design exhibits: {symptom_label}. "
            "The code looks plausible; use architecture-level reasoning to identify and fix the hidden bug."
        )
    else:
        instruction = (
            f"This production-like AIE design is unstable with symptom: {symptom_label}. "
            "Investigate subtle scheduling or architecture interactions and fix the actual root cause."
        )

    return instruction, tier


def infer_clean_filename_from_buggy(buggy_filename: str | None, bug_type: str | None) -> str | None:
    if not buggy_filename:
        return None

    filename = str(buggy_filename)
    path = Path(filename)
    stem = path.stem
    suffix = path.suffix

    if bug_type:
        bug_suffix = f"_{bug_type}"
        if stem.endswith(bug_suffix):
            return stem[: -len(bug_suffix)] + suffix

    cleaned = re.sub(r"_(?:buggy|deadlock|oob|mismatch|overrun|overflow|locking|issue)$", "", stem)
    if cleaned != stem:
        return cleaned + suffix

    return filename


def build_contrastive_instruction(info: dict, bug_type: str | None) -> str:
    key = f"{source_group_key(info)}:contrastive"
    template = choose_template(CONTRASTIVE_BUG_TEMPLATES, key)
    interfaces = ", ".join(info.get("interfaces", [])) if info.get("interfaces") else "unknown"
    bug_label = info.get("bug_label") or bug_type or info.get("bug_type") or "unspecified"
    return template.format(bug_type=bug_label, interfaces=interfaces)


def build_contrastive_response(
    buggy: dict,
    correct: dict,
    bug_type: str | None,
    symptom: str | None,
    anchors: dict | None = None,
) -> str:
    failure_mode = buggy.get("bug_label") or bug_type or buggy.get("bug_type") or "unspecified bug"
    observed_symptom = symptom or buggy.get("symptom") or "stalls, deadlock, corrupted outputs, or incorrect graph behavior"
    if anchors is None:
        anchors = extract_code_anchors(buggy.get("context", ""))
    anchor_text = anchor_phrase(anchors)

    if V2_MODE:
        return build_v2_code_response(correct.get("context", ""), correct.get("relative_path"))

    diff_text = build_unified_diff(
        buggy.get("context", ""),
        correct.get("context", ""),
        buggy_label="buggy",
        correct_label="correct",
    )

    sentence_variants = [
        f"The buggy source violates `{failure_mode}`.",
        f"This source exhibits the `{failure_mode}` defect.",
        f"The buggy version breaks the `{failure_mode}` contract.",
        f"Classification: `{failure_mode}`.",
    ]
    key = str(buggy.get("relative_path") or "") + "|" + (bug_type or "")
    lead = sentence_variants[deterministic_index(key, len(sentence_variants))]

    parts = [
        f"{lead} {anchor_text}Observed symptom: {observed_symptom}.",
        "",
        "Minimal fix (unified diff, buggy -> correct):",
        "```diff",
        diff_text if diff_text else "(no textual difference recoverable - inspect the buggy section manually)",
        "```",
        "",
        f"Why the corrected version works: {minimal_fix_text(buggy, failure_mode)}",
    ]
    return "\n".join(parts)


def build_bug_pair_records(file_infos: list[dict]) -> list[tuple[dict, dict, str]]:
    by_relative = {str(info["relative_path"]).replace("\\", "/"): info for info in file_infos}
    records: list[tuple[dict, dict, str]] = []

    pair_candidates = [
        ("aie_dataset/debug_pairs/data_shuffle_BUGGY_deadlock.cc", "aie_dataset/debug_pairs/data_shuffle_CORRECT.cc"),
        ("aie_dataset/debug_pairs/peak_detect_BUGGY_oob.cc", "aie_dataset/debug_pairs/peak_detect.cc"),
    ]

    for buggy_key, correct_key in pair_candidates:
        buggy = by_relative.get(buggy_key)
        correct = by_relative.get(correct_key)
        if not buggy or not correct:
            continue
        records.append((buggy, correct, "curated_debug_pairs"))

    clean_by_filename: dict[str, list[dict]] = {}
    for info in file_infos:
        category = info.get("row_category")
        row_filename = info.get("row_filename")
        if category in {"kernel", "graph"} and row_filename:
            clean_by_filename.setdefault(str(row_filename), []).append(info)

    for buggy in file_infos:
        if buggy.get("row_category") not in {"buggy_kernel", "buggy_graph"}:
            continue

        clean_filename = infer_clean_filename_from_buggy(buggy.get("row_filename"), buggy.get("bug_type"))
        if not clean_filename:
            continue

        clean_candidates = clean_by_filename.get(clean_filename, [])
        if not clean_candidates:
            continue

        correct = next(
            (candidate for candidate in clean_candidates if candidate.get("artifact_type") == buggy.get("artifact_type")),
            clean_candidates[0],
        )
        records.append((buggy, correct, "expanded_source_pairs"))

    return records


def build_bug_pair_entries_from_records(records: list[tuple[dict, dict, str]]) -> list[dict]:
    pairs = []
    for buggy, correct, pair_source in records:
        symptom = buggy.get("symptom")
        if not symptom or symptom == "synthetic mutation":
            symptom = pick_symptom(
                buggy.get("bug_type"),
                source_group_key(buggy) + ":" + str(buggy.get("bug_type") or ""),
            )
        instruction, tier = build_tiered_bug_instruction(buggy, buggy.get("bug_type"), symptom)
        anchors = extract_code_anchors(buggy.get("context", ""))
        response = build_contrastive_response(buggy, correct, buggy.get("bug_type"), symptom, anchors=anchors)

        metadata = {
            "source": buggy["source_url"] or str(buggy["relative_path"]).replace("\\", "/"),
            "source_repo": buggy["source_repo"],
            "source_branch": buggy["source_branch"],
            "source_path": buggy["source_path"],
            "relative_path": str(buggy["relative_path"]).replace("\\", "/"),
            "type": buggy["artifact_type"],
            "category": buggy["domain"],
            "hardware": "Versal AIE",
            "interfaces": buggy["interfaces"],
            "vector_types": buggy["vector_types"],
            "intrinsics": buggy["intrinsics"],
            "split": buggy["split"],
            "variant": "bug_fix_pair",
            "bug_type": buggy.get("bug_type"),
            "bug_label": buggy.get("bug_label") or buggy.get("bug_type"),
            "symptom": symptom,
            "difficulty_tier": tier,
            "pair_source": pair_source,
            "source_group": source_group_key(buggy),
            "reference_correct_path": str(correct["relative_path"]).replace("\\", "/"),
        }

        pairs.append(
            {
                "instruction": instruction,
                "context": "Buggy version:\n" + buggy["context"].rstrip() + "\n\nCorrect version:\n" + correct["context"],
                "response": response,
                "metadata": metadata,
            }
        )

    return pairs


def source_parent_key(info: dict) -> str:
    repo = info.get("source_repo") or "local"
    source_path = info.get("source_path") or str(info["relative_path"]).replace("\\", "/")
    parent = str(Path(source_path).parent).replace("\\", "/")
    return f"{repo}:{parent}"


def find_related_multi_file_context(primary: dict, correct: dict, parent_index: dict[str, list[dict]]) -> dict | None:
    candidates = find_related_multi_file_candidates(primary, correct, parent_index, limit=1)
    return candidates[0] if candidates else None


def find_related_multi_file_candidates(
    primary: dict,
    correct: dict,
    parent_index: dict[str, list[dict]],
    limit: int = MULTI_FILE_RELATED_VARIANTS_PER_RECORD,
) -> list[dict]:
    related_candidates = parent_index.get(source_parent_key(primary), [])
    if not related_candidates:
        return []

    primary_path = str(primary["relative_path"]).replace("\\", "/")
    correct_path = str(correct["relative_path"]).replace("\\", "/")
    compatible = [
        candidate
        for candidate in related_candidates
        if str(candidate["relative_path"]).replace("\\", "/") not in {primary_path, correct_path}
    ]
    if not compatible:
        return []

    opposite_type = "graph_type" if primary.get("artifact_type") == "kernel_type" else "kernel_type"
    prioritized = [candidate for candidate in compatible if candidate.get("artifact_type") == opposite_type]
    # Keep the opposite-type candidates first, then fall back to same-type siblings if we still need more.
    ordered: list[dict] = []
    seen_paths: set[str] = set()
    for pool in (prioritized, compatible):
        sorted_pool = sorted(pool, key=lambda item: hashlib.md5(str(item["relative_path"]).encode("utf-8")).hexdigest())
        for candidate in sorted_pool:
            rel = str(candidate["relative_path"]).replace("\\", "/")
            if rel in seen_paths:
                continue
            seen_paths.add(rel)
            ordered.append(candidate)
            if len(ordered) >= limit:
                return ordered
    return ordered


def build_multi_file_bug_pair_entries(file_infos: list[dict], records: list[tuple[dict, dict, str]]) -> list[dict]:
    parent_index: dict[str, list[dict]] = {}
    for info in file_infos:
        parent_index.setdefault(source_parent_key(info), []).append(info)

    rows = []
    for buggy, correct, pair_source in records:
        related_list = find_related_multi_file_candidates(
            buggy, correct, parent_index, limit=MULTI_FILE_RELATED_VARIANTS_PER_RECORD
        )
        if not related_list:
            continue

        symptom = buggy.get("symptom")
        if not symptom or symptom == "synthetic mutation":
            symptom = pick_symptom(
                buggy.get("bug_type"),
                source_group_key(buggy) + ":mf:" + str(buggy.get("bug_type") or ""),
            )
        _, tier = build_tiered_bug_instruction(buggy, buggy.get("bug_type"), symptom)
        bug_type = buggy.get("bug_type") or "aie_cross_file_bug"
        anchors = extract_code_anchors(buggy.get("context", ""))
        anchor_text = anchor_phrase(anchors)

        diff_text = build_unified_diff(
            buggy.get("context", ""),
            correct.get("context", ""),
            buggy_label=str(buggy["relative_path"]).replace("\\", "/"),
            correct_label=str(correct["relative_path"]).replace("\\", "/"),
        )

        for variant_index, related in enumerate(related_list, start=1):
            related_rel = str(related["relative_path"]).replace("\\", "/")
            if V2_MODE:
                seed_key = (
                    str(buggy.get("relative_path") or "")
                    + "|mf:" + str(bug_type)
                    + "|" + related_rel
                    + "|" + str(variant_index)
                )
                instruction = build_v2_multi_file_instruction(seed_key, tier, symptom)
            else:
                instruction = (
                    f"Multi-file debug task (variant {variant_index}): fix `{bug_type}` by reasoning across the primary buggy source, "
                    f"the related connected file `{related_rel}`, and the reference-correct primary file. "
                    "Identify the first cross-file contract violation and provide the minimal corrected code."
                )
            if V2_MODE:
                response = build_v2_code_response(correct.get("context", ""), correct.get("relative_path"))
            else:
                response = (
                    f"Cross-file root cause: `{bug_type}`. {anchor_text}Observed symptom: {symptom}. "
                    f"The related file `{related_rel}` defines the shared contract that the primary buggy file violates.\n\n"
                    "Minimal fix (unified diff, buggy primary -> correct primary):\n"
                    "```diff\n"
                    + (diff_text if diff_text else "(no textual difference recoverable - inspect the buggy primary manually)")
                    + "\n```\n\n"
                    f"Why this fix restores cross-file agreement: {minimal_fix_text(buggy, bug_type)}"
                )

            rows.append(
                {
                    "instruction": instruction,
                    "context": (
                        "Primary buggy file ("
                        + str(buggy["relative_path"]).replace("\\", "/")
                        + "):\n"
                        + buggy["context"].rstrip()
                        + "\n\nRelated file ("
                        + related_rel
                        + "):\n"
                        + related["context"].rstrip()
                        + "\n\nReference-correct primary file ("
                        + str(correct["relative_path"]).replace("\\", "/")
                        + "):\n"
                        + correct["context"]
                    ),
                    "response": response,
                    "metadata": {
                        "source": buggy["source_url"] or str(buggy["relative_path"]).replace("\\", "/"),
                        "source_repo": buggy["source_repo"],
                        "source_branch": buggy["source_branch"],
                        "source_path": buggy["source_path"],
                        "relative_path": str(buggy["relative_path"]).replace("\\", "/"),
                        "type": buggy["artifact_type"],
                        "category": buggy["domain"],
                        "hardware": "Versal AIE",
                        "interfaces": buggy["interfaces"],
                        "vector_types": buggy["vector_types"],
                        "intrinsics": buggy["intrinsics"],
                        "split": buggy["split"],
                        "variant": "multi_file_bug_fix_pair",
                        "bug_type": buggy.get("bug_type"),
                        "bug_label": buggy.get("bug_label") or buggy.get("bug_type"),
                        "symptom": symptom,
                        "difficulty_tier": tier,
                        "pair_source": pair_source,
                        "source_group": source_group_key(buggy),
                        "related_context_path": related_rel,
                        "reference_correct_path": str(correct["relative_path"]).replace("\\", "/"),
                        "related_variant_index": variant_index,
                    },
                }
            )

    return rows


def build_taxonomy_multi_file_debug_entries(
    file_infos: list[dict],
    scenarios_per_bug_type: int = TAXONOMY_MULTI_FILE_SCENARIOS_PER_BUG_TYPE,
) -> list[dict]:
    rows: list[dict] = []
    if scenarios_per_bug_type <= 0 or not file_infos:
        return rows

    parent_index: dict[str, list[dict]] = {}
    for info in file_infos:
        parent_index.setdefault(source_parent_key(info), []).append(info)

    for bug_entry in BUG_TAXONOMY_ENTRIES:
        bug_label = bug_entry["label"]
        bug_slug = bug_entry["slug"]
        mutator = mutator_for_bug_slug(bug_slug)

        tier = bug_entry.get("tier") or "normal"
        per_bug = TAXONOMY_MULTI_FILE_SCENARIOS_PER_TIER.get(tier, scenarios_per_bug_type)
        if per_bug <= 0:
            continue

        primary_candidates = [
            info
            for info in file_infos
            if info.get("context") and len(parent_index.get(source_parent_key(info), [])) > 1
        ]
        if not primary_candidates and mutator is not None:
            # We need real file pairs for mutator-backed multi-file scenarios; without
            # any eligible pair, skip. Synth-only slugs handle themselves below.
            continue

        produced = 0

        # For slugs with no mutator we don't need real file pairs; emit synthesized
        # code-debug rows directly so the dataset stays code-only (no inspection rows).
        if mutator is None:
            while produced < per_bug:
                synth_entry = build_synthesized_code_debug_entry(bug_entry, produced + 1)
                if synth_entry is None:
                    break
                rows.append(synth_entry)
                produced += 1
            continue

        # Widen the primary search pool when a mutator exists so we can actually hit the quota.
        pool_size = max(per_bug * 20, 64) if mutator is not None else per_bug
        primaries = select_taxonomy_contexts(primary_candidates, f"{bug_slug}:multi", pool_size)

        for scenario_index, primary in enumerate(primaries, start=1):
            if produced >= per_bug:
                break
            siblings = [
                candidate
                for candidate in parent_index.get(source_parent_key(primary), [])
                if str(candidate["relative_path"]) != str(primary["relative_path"]) and candidate.get("context")
            ]
            if not siblings:
                continue

            opposite_type = "graph_type" if primary.get("artifact_type") == "kernel_type" else "kernel_type"
            prioritized = [cand for cand in siblings if cand.get("artifact_type") == opposite_type]
            pool = prioritized or siblings
            pool = sorted(pool, key=lambda item: hashlib.md5(str(item["relative_path"]).encode("utf-8")).hexdigest())
            related = pool[deterministic_index(f"{bug_slug}:{scenario_index}", len(pool))]

            primary_rel = str(primary["relative_path"]).replace("\\", "/")
            related_rel = str(related["relative_path"]).replace("\\", "/")
            anchors = extract_code_anchors(primary["context"])
            anchor_text = anchor_phrase(anchors)

            buggy_context = mutator(primary["context"]) if mutator else None
            if mutator and buggy_context and buggy_context != primary["context"]:
                symptom = pick_symptom(bug_slug, source_group_key(primary) + ":mftx:" + bug_slug)
                diff_text = build_unified_diff(buggy_context, primary["context"], "buggy " + primary_rel, "correct " + primary_rel)
                if V2_MODE:
                    seed_key = primary_rel + "|mftx:" + bug_slug + "|" + related_rel + "|" + str(scenario_index)
                    instruction = build_v2_multi_file_instruction(seed_key, bug_entry.get("tier") or "normal", symptom)
                    response = build_v2_code_response(primary["context"], primary.get("relative_path"))
                else:
                    instruction = (
                        f"Multi-file debug scenario {scenario_index}: the primary buggy AIE source and the related file below together "
                        f"exhibit the `{bug_label}` pattern. Identify the cross-file contract violation and provide the minimal fix as a unified diff."
                    )
                    response = (
                        f"Verdict: this multi-file scenario matches `{bug_label}`. {anchor_text}Observed symptom: {symptom}. "
                        f"The related file `{related_rel}` defines the shared contract that the primary buggy file violates.\n\n"
                        "Minimal fix (unified diff on the primary file):\n"
                        "```diff\n" + diff_text + "\n```\n\n"
                        f"Why the correction restores cross-file agreement: {minimal_fix_text(primary, bug_slug)}"
                    )
                context_block = (
                    f"Scenario bug pattern: {bug_label}\n\n"
                    f"Primary buggy source ({primary_rel}):\n{buggy_context.rstrip()}\n\n"
                    f"Related source ({related_rel}):\n{related['context'].rstrip()}\n\n"
                    f"Reference-correct primary ({primary_rel}):\n{primary['context']}"
                )
                variant_name = "taxonomy_multi_file_debug_scenario"
                verdict = "present"
                symptom_meta = symptom
                synthetic_flag = True
            else:
                # No mutator produced a change; fall back to a synthesized single-file
                # code-debug row so the slug is still represented by real buggy/correct
                # code. We deliberately do NOT emit inspection-negative (description-only)
                # rows — the dataset is code-debug only.
                if mutator is not None:
                    continue
                synth_entry = build_synthesized_code_debug_entry(bug_entry, produced + 1)
                if synth_entry is not None:
                    rows.append(synth_entry)
                    produced += 1
                continue

            rows.append(
                {
                    "instruction": instruction,
                    "context": context_block,
                    "response": response,
                    "metadata": {
                        "source": primary["source_url"] or primary_rel,
                        "source_repo": primary["source_repo"],
                        "source_branch": primary["source_branch"],
                        "source_path": primary["source_path"],
                        "relative_path": primary_rel,
                        "type": primary["artifact_type"],
                        "category": primary["domain"],
                        "hardware": "Versal AIE",
                        "interfaces": primary["interfaces"],
                        "vector_types": primary["vector_types"],
                        "intrinsics": primary["intrinsics"],
                        "split": primary["split"],
                        "variant": variant_name,
                        "bug_type": bug_slug,
                        "bug_label": bug_label,
                        "symptom": symptom_meta,
                        "difficulty_tier": bug_entry["tier"],
                        "taxonomy_group": bug_entry["group"],
                        "source_group": source_group_key(primary),
                        "related_context_path": related_rel,
                        "synthetic": synthetic_flag,
                        "verdict": verdict,
                    },
                }
            )
            produced += 1

    return rows


def diversify_bug_rows(rows: list[dict], max_per_bug_type: int = MAX_BUG_ROWS_PER_BUG_TYPE, max_per_source_group: int = MAX_BUG_ROWS_PER_SOURCE_GROUP) -> list[dict]:
    selected = []
    seen_fingerprints: set[str] = set()
    by_bug_type: dict[str, int] = {}
    by_source_group: dict[str, int] = {}

    for row in sorted(rows, key=row_stable_key):
        metadata = row.get("metadata", {})
        bug_type = str(metadata.get("bug_type") or "unknown")
        source_group = str(metadata.get("source_group") or metadata.get("relative_path") or "unknown")
        # For synthetic taxonomy rows, namespace the fingerprint by bug_type so
        # that different slugs routing to the same template are not collapsed.
        # (normalize_text_for_diversity strips comments and digits, which would
        # otherwise fold many synth variants into one.)
        fp_key = row.get("context", "")
        if metadata.get("variant") == "synthetic_taxonomy_bug_fix":
            fp_key = f"synth:{bug_type}::" + fp_key
        fingerprint = context_diversity_fingerprint(fp_key)

        if fingerprint in seen_fingerprints:
            continue
        if by_bug_type.get(bug_type, 0) >= max_per_bug_type:
            continue
        if by_source_group.get(source_group, 0) >= max_per_source_group:
            continue

        seen_fingerprints.add(fingerprint)
        by_bug_type[bug_type] = by_bug_type.get(bug_type, 0) + 1
        by_source_group[source_group] = by_source_group.get(source_group, 0) + 1
        selected.append(row)

    return selected


def build_bug_pair_entries(file_infos: list[dict]) -> list[dict]:
    records = build_bug_pair_records(file_infos)
    return build_bug_pair_entries_from_records(records)

def find_first_match_span(text: str, pattern: str) -> tuple[int, int] | None:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return None
    return match.start(), match.end()


def replace_span_with_line_comment(text: str, span: tuple[int, int], comment: str) -> str:
    start, end = span
    original = text[start:end]
    line = original.strip()
    return text[:start] + f"// BUG_INJECTED: {comment}\n// ORIGINAL: {line}\n" + text[end:]


def mutate_stream_deadlock(context: str) -> str | None:
    span = find_first_match_span(context, r"^\s*writeincr(?:_v\d+)?\s*\([^\n;]*\)\s*;\s*$")
    if span:
        return replace_span_with_line_comment(
            context,
            span,
            "Removed output token write to create producer/consumer imbalance and potential stream deadlock.",
        )

    span = find_first_match_span(context, r"^\s*\*out\+\+\s*=\s*[^\n;]+;\s*$")
    if span:
        return replace_span_with_line_comment(
            context,
            span,
            "Removed output store to simulate dropped stream/window writes and possible downstream stall.",
        )
    return None


def mutate_window_oob(context: str) -> str | None:
    mutated, count = re.subn(r"(<\s*[^\n;\)]+)\)", r"<=\g<1>)", context, count=1)
    if count == 1:
        return mutated

    mutated, count = re.subn(r"for\s*\(([^;]+);\s*([^;<>!=]+)\s*<\s*([^;]+);", r"for(\1; \2 <= \3;", context, count=1)
    if count == 1:
        return mutated
    return None


def mutate_missing_iterator_increment(context: str) -> str | None:
    mutated, count = re.subn(r"\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\+\+\s*=", r"*\1 =", context, count=1)
    if count == 1 and mutated != context:
        return mutated
    return None


def mutate_dereference_moved_outside_loop(context: str) -> str | None:
    """Find an `auto x = *y++;` line that lives inside a for-loop body and hoist it
    out of the loop. Creates a stale-data bug: the dereference happens once before
    the loop, so every iteration sees the same value instead of advancing.

    This mutator teaches the model the distinction between where an iterator is
    DECLARED (outside the loop - legitimate) and where it is DEREFERENCED (inside
    the loop - the actual fix).
    """
    lines = context.split("\n")
    # Scan top-down for `for (...) {` and look for the first `auto x = *y++;`
    # in the body before any closing brace at the same depth.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("for ") and not stripped.startswith("for("):
            continue
        # Require brace on same line (covers the common AIE kernel style).
        if "{" not in line:
            continue
        # Walk body until matching close brace or hoist candidate.
        depth = line.count("{") - line.count("}")
        if depth <= 0:
            continue
        for j in range(i + 1, min(i + 30, len(lines))):
            body_line = lines[j]
            depth += body_line.count("{") - body_line.count("}")
            if depth <= 0:
                break
            m = re.match(r"^(\s*)(auto\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*\*\s*[A-Za-z_][A-Za-z0-9_]*\s*\+\+\s*;)\s*$", body_line)
            if m:
                # Hoist this line to just before the `for` line.
                indent_match = re.match(r"^(\s*)", line)
                hoisted_indent = indent_match.group(1) if indent_match else ""
                hoisted_line = hoisted_indent + m.group(2)
                new_lines = list(lines)
                del new_lines[j]
                new_lines.insert(i, hoisted_line)
                mutated = "\n".join(new_lines)
                if mutated != context:
                    return mutated
                return None
    return None


def mutate_runtime_ratio_zero(context: str) -> str | None:
    mutated, count = re.subn(
        r"(runtime\s*<\s*ratio\s*>\s*\([^\)]+\)\s*=\s*)([0-9]*\.?[0-9]+)",
        r"\g<1>0",
        context,
        count=1,
    )
    if count == 1 and mutated != context:
        return mutated
    return None


def mutate_plio_width_mismatch(context: str) -> str | None:
    width_map = {
        "plio_16_bits": "plio_32_bits",
        "plio_32_bits": "plio_128_bits",
        "plio_64_bits": "plio_32_bits",
        "plio_128_bits": "plio_32_bits",
    }

    match = re.search(r"\bplio_(16|32|64|128)_bits\b", context)
    if not match:
        return None

    token = match.group(0)
    replacement = width_map.get(token, "plio_32_bits")
    mutated = context[: match.start()] + replacement + context[match.end() :]
    if mutated != context:
        return mutated
    return None


def mutate_reversed_connect_direction(context: str) -> str | None:
    match = re.search(r"connect<([^>]+)>\s*\(\s*([^,\n]+?)\s*,\s*([^\)\n]+?)\s*\)", context)
    if not match:
        return None

    swapped = f"connect<{match.group(1)}>({match.group(3).strip()}, {match.group(2).strip()})"
    mutated = context[: match.start()] + swapped + context[match.end() :]
    if mutated != context:
        return mutated
    return None


def mutate_missing_output_write(context: str) -> str | None:
    span = find_first_match_span(context, r"^\s*writeincr(?:_v\d+)?\s*\([^\n;]*\)\s*;\s*$")
    if span:
        return replace_span_with_line_comment(
            context,
            span,
            "Removed output write to create a silent producer-consumer mismatch.",
        )

    span = find_first_match_span(context, r"^\s*window_writeincr\s*\([^\n;]*\)\s*;\s*$")
    if span:
        return replace_span_with_line_comment(
            context,
            span,
            "Removed window write to create dropped output data and downstream inconsistency.",
        )

    span = find_first_match_span(context, r"^\s*\*out\+\+\s*=\s*[^\n;]+;\s*$")
    if span:
        return replace_span_with_line_comment(
            context,
            span,
            "Removed output store to trigger missing-output behavior.",
        )
    return None


def mutate_wrong_vector_lane_width(context: str) -> str | None:
    vector_match = re.search(r"begin_vector<\s*(\d+)\s*>", context)
    if vector_match:
        current = int(vector_match.group(1))
        replacement = 16 if current == 8 else 8
        mutated = context[: vector_match.start(1)] + str(replacement) + context[vector_match.end(1) :]
        if mutated != context:
            return mutated

    vector_type_match = re.search(r"aie::vector<\s*([^,>]+)\s*,\s*(\d+)\s*>", context)
    if vector_type_match:
        current = int(vector_type_match.group(2))
        replacement = 16 if current == 8 else 8
        mutated = context[: vector_type_match.start(2)] + str(replacement) + context[vector_type_match.end(2) :]
        if mutated != context:
            return mutated

    return None


def mutate_graph_dimension_mismatch(context: str) -> str | None:
    def _bump_dimension(match: re.Match) -> str:
        body = match.group(1)
        numbers = re.findall(r"\d+", body)
        if not numbers:
            return match.group(0)
        first = numbers[0]
        bumped = str(int(first) + 16)
        body_bumped = body.replace(first, bumped, 1)
        return "{" + body_bumped + "}"

    mutated, count = re.subn(r"\{([^{}]+)\}", _bump_dimension, context, count=1)
    if count == 1 and mutated != context:
        return mutated
    return None


def mutate_subtraction_for_addition(context: str) -> str | None:
    # Swap a + in an arithmetic expression to - while avoiding ++, +=, and preprocessor lines.
    pattern = re.compile(r"(?<![+\-=*/<>!&|])\s\+\s(?!\+|=)")
    lines = context.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if "++" in line or "+=" in line:
            continue
        new_line, n = pattern.subn(" - ", line, count=1)
        if n == 1 and new_line != line:
            lines[idx] = new_line
            return "".join(lines)
    return None


def mutate_mul_to_add(context: str) -> str | None:
    mutated, count = re.subn(r"\baie::mul\b", "aie::add", context, count=1)
    if count == 1 and mutated != context:
        return mutated
    return None


def mutate_tovector_shift_fifteen(context: str) -> str | None:
    # to_vector(acc, 0) -> to_vector(acc, 15), or .to_vector<T>(0) -> .to_vector<T>(15)
    mutated, count = re.subn(
        r"(to_vector\s*(?:<[^<>]*>)?\s*\([^(),]+?,\s*)0(\s*\))",
        r"\g<1>15\g<2>",
        context,
        count=1,
    )
    if count == 1 and mutated != context:
        return mutated

    mutated, count = re.subn(
        r"(\.to_vector\s*(?:<[^<>]*>)?\s*\()\s*0\s*(\))",
        r"\g<1>15\g<2>",
        context,
        count=1,
    )
    if count == 1 and mutated != context:
        return mutated
    return None


def mutate_acc48_for_acc80(context: str) -> str | None:
    # Taxonomy says the bug is "acc48 instead of acc80" - so buggy has acc48 where correct has acc80.
    # We swap acc80 -> acc48 in the clean source to synthesize the bug.
    mutated, count = re.subn(r"\bacc80\b", "acc48", context, count=1)
    if count == 1 and mutated != context:
        return mutated
    return None


def mutate_loop_count_halved(context: str) -> str | None:
    # for (int i = 0; i < N; i++) where N is a numeric literal >= 4; halve N.
    pattern = re.compile(
        r"(for\s*\(\s*(?:int|unsigned|unsigned\s+int|size_t|uint32_t|int32_t)?\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*0\s*;\s*[A-Za-z_][A-Za-z0-9_]*\s*<\s*)(\d+)(\s*;)"
    )
    for match in pattern.finditer(context):
        value = int(match.group(2))
        if value < 4:
            continue
        halved = value // 2
        start, end = match.span(2)
        return context[:start] + str(halved) + context[end:]
    return None


def mutate_window_size_halved(context: str) -> str | None:
    # window<T, SIZE> or input_window<T, SIZE> -> halve SIZE
    pattern = re.compile(r"(\b(?:input_window|output_window|window)\s*<\s*[A-Za-z_][A-Za-z0-9_:]*\s*,\s*)(\d+)(\s*>)")
    match = pattern.search(context)
    if not match:
        return None
    value = int(match.group(2))
    if value < 4:
        return None
    halved = value // 2
    start, end = match.span(2)
    return context[:start] + str(halved) + context[end:]


def mutate_remove_zeros_init(context: str) -> str | None:
    # Replace aie::zeros<...>() with aie::broadcast<...>(1) so the accumulator starts with garbage-like values.
    pattern = re.compile(r"aie::zeros\s*<([^<>]*)>\s*\(\s*\)")
    match = pattern.search(context)
    if not match:
        return None
    template_args = match.group(1).strip()
    # Use broadcast of a non-zero constant so the buggy version looks like "aie::mul(garbage)"-style init.
    replacement = f"aie::broadcast<{template_args}>(1)"
    return context[: match.start()] + replacement + context[match.end() :]


def mutate_broadcast_width_mismatch(context: str) -> str | None:
    # aie::broadcast<T, N> -> aie::broadcast<T, N/2 or N*2> (whichever stays in {4,8,16,32})
    pattern = re.compile(r"(aie::broadcast\s*<\s*[A-Za-z_][A-Za-z0-9_:]*\s*,\s*)(\d+)(\s*>)")
    match = pattern.search(context)
    if not match:
        return None
    value = int(match.group(2))
    if value == 8:
        replacement = "4"
    elif value == 16:
        replacement = "8"
    elif value == 4:
        replacement = "8"
    elif value == 32:
        replacement = "16"
    else:
        return None
    start, end = match.span(2)
    return context[:start] + replacement + context[end:]


def mutate_duplicate_stream_read(context: str) -> str | None:
    # Find two distinct readincr(...) calls and make the second read from the first's argument.
    # readincr(s, in_b) ... readincr(s, in_b) duplicates; or readincr(s, in_a) twice.
    pattern = re.compile(r"readincr(?:_v\d+)?\s*\(\s*([A-Za-z_][A-Za-z0-9_\.\->]*)\s*\)")
    matches = list(pattern.finditer(context))
    if len(matches) < 2:
        return None
    first_arg = matches[0].group(1)
    second = matches[1]
    second_arg = second.group(1)
    if second_arg == first_arg:
        return None
    # Replace only the second match's argument with the first's.
    start, end = second.span(1)
    return context[:start] + first_arg + context[end:]


def mutate_drop_last_sample(context: str) -> str | None:
    # for (int i = 0; i < N; i++) -> for (int i = 0; i < N - 1; i++) to drop last sample in block.
    pattern = re.compile(
        r"(for\s*\(\s*(?:int|unsigned|unsigned\s+int|size_t|uint32_t|int32_t)?\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*0\s*;\s*[A-Za-z_][A-Za-z0-9_]*\s*<\s*)([A-Za-z_][A-Za-z0-9_]*|\d+)(\s*;)"
    )
    match = pattern.search(context)
    if not match:
        return None
    bound = match.group(2)
    if bound.isdigit() and int(bound) < 2:
        return None
    replacement = f"{bound} - 1"
    start, end = match.span(2)
    return context[:start] + replacement + context[end:]


def mutate_remove_chess_pipelining(context: str) -> str | None:
    span = find_first_match_span(context, r"^\s*chess_prepare_for_pipelining\s*;?\s*$")
    if not span:
        return None
    return replace_span_with_line_comment(
        context,
        span,
        "Removed chess_prepare_for_pipelining pragma so the compiler can no longer schedule the inner loop as VLIW.",
    )


def mutate_readincr_from_output(context: str) -> str | None:
    # readincr(stream, out) or readincr_v8(stream, out) - swap an input-stream read to use an output symbol.
    match = re.search(r"readincr(?:_v\d+)?\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", context)
    if not match:
        return None
    arg = match.group(1)
    # Find an output-looking identifier elsewhere in the source.
    out_match = re.search(r"\b(out|out_stream|out_iter|output)[A-Za-z0-9_]*\b", context)
    if not out_match or out_match.group(0) == arg:
        return None
    start, end = match.span(1)
    return context[:start] + out_match.group(0) + context[end:]


def mutate_break_in_pipelined_loop(context: str) -> str | None:
    # Inject a break; right after the first for( ... ) { in an inner body; prevents chess pipelining.
    match = re.search(r"(for\s*\([^)]*\)\s*\{\s*)", context)
    if not match:
        return None
    insert_at = match.end()
    payload = "if (/* injected defensive exit */ false) { break; }\n    "
    return context[:insert_at] + payload + context[insert_at:]


def mutate_runtime_ratio_overflow(context: str) -> str | None:
    # runtime<ratio>(k) = 0.5 -> runtime<ratio>(k) = 2.0 so three kernels exceed 1.0 on a tile.
    mutated, count = re.subn(
        r"(runtime\s*<\s*ratio\s*>\s*\([^\)]+\)\s*=\s*)([0-9]*\.?[0-9]+)",
        r"\g<1>2.0",
        context,
        count=1,
    )
    if count == 1 and mutated != context:
        return mutated
    return None


def mutate_port_index_oob(context: str) -> str | None:
    # k.in[2] -> k.in[8] or k.out[1] -> k.out[9] to walk off the declared port count.
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\.(in|out)\s*\[\s*(\d+)\s*\]", context)
    if not match:
        return None
    idx = int(match.group(3))
    # Bump by 4 so we reliably exceed any reasonable kernel port count.
    start, end = match.span(3)
    return context[:start] + str(idx + 4) + context[end:]


def mutate_missing_graph_wait(context: str) -> str | None:
    span = find_first_match_span(context, r"^\s*[A-Za-z_][A-Za-z0-9_\.]*\.wait\s*\([^\)]*\)\s*;\s*$")
    if not span:
        return None
    return replace_span_with_line_comment(
        context,
        span,
        "Removed graph.wait() call so the host may read output before kernels complete.",
    )


def mutate_missing_connect(context: str) -> str | None:
    span = find_first_match_span(context, r"^\s*adf::connect\s*<[^>]*>\s*\([^)]*\)\s*;\s*$")
    if not span:
        span = find_first_match_span(context, r"^\s*connect\s*<[^>]*>\s*\([^)]*\)\s*;\s*$")
    if not span:
        return None
    return replace_span_with_line_comment(
        context,
        span,
        "Removed an adf::connect call so one graph port is declared but never wired.",
    )


def mutate_self_loop_connect(context: str) -> str | None:
    # connect<...>(k.out[0], k.in[0]) self-loop on same kernel instance.
    match = re.search(
        r"(connect\s*<[^>]*>\s*\(\s*)([A-Za-z_][A-Za-z0-9_]*)\.out\s*\[\s*(\d+)\s*\]\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\.in\s*\[\s*(\d+)\s*\]\s*\)",
        context,
    )
    if not match:
        return None
    src_kernel = match.group(2)
    src_idx = match.group(3)
    dst_idx = match.group(5)
    # Rewrite so destination uses the same kernel as source - a self-loop.
    replacement = f"{match.group(1)}{src_kernel}.out[{src_idx}], {src_kernel}.in[{dst_idx}])"
    start, end = match.span()
    return context[:start] + replacement + context[end:]


def mutate_acc48_for_acc80_reverse(context: str) -> str | None:
    # Complement to mutate_acc48_for_acc80: swap acc48 -> acc80 to model the "acc80 where acc48 would fit but width convention violated" variant.
    # We choose a narrow behavior: only trigger when the source explicitly uses accum<acc48, N>.
    match = re.search(r"\baccum\s*<\s*acc48\s*,", context)
    if not match:
        return None
    mutated, count = re.subn(r"\baccum\s*<\s*acc48\s*,", "accum<acc80,", context, count=1)
    if count == 1 and mutated != context:
        return mutated
    return None


def mutate_wrong_to_vector_output_type(context: str) -> str | None:
    # to_vector<int16>(...) -> to_vector<int32>(...) so output type does not match buffer.
    match = re.search(r"to_vector\s*<\s*(int16|int32|cint16|cint32)\s*>", context)
    if not match:
        return None
    current = match.group(1)
    swap = {
        "int16": "int32",
        "int32": "int16",
        "cint16": "cint32",
        "cint32": "cint16",
    }
    replacement = swap.get(current, current)
    if replacement == current:
        return None
    start, end = match.span(1)
    return context[:start] + replacement + context[end:]


def mutate_missing_adf_source(context: str) -> str | None:
    # Remove an adf::source(...) = ... assignment inside a graph constructor.
    span = find_first_match_span(context, r"^\s*adf::source\s*\([^)]*\)\s*=\s*[^\n;]+;\s*$")
    if not span:
        return None
    return replace_span_with_line_comment(
        context,
        span,
        "Removed adf::source() assignment so the kernel function binding is lost at build time.",
    )


def mutate_missing_runtime_ratio(context: str) -> str | None:
    span = find_first_match_span(context, r"^\s*runtime\s*<\s*ratio\s*>\s*\([^\)]+\)\s*=\s*[^\n;]+;\s*$")
    if not span:
        return None
    return replace_span_with_line_comment(
        context,
        span,
        "Removed runtime<ratio> line so the kernel lacks a scheduler hint entirely.",
    )


def mutate_window_margin_to_size(context: str) -> str | None:
    # window<T, SIZE, MARGIN> -> MARGIN set >= SIZE (invalid). Works only if the template has 3 args.
    match = re.search(
        r"\b(?:input_window|output_window|window)\s*<\s*[A-Za-z_][A-Za-z0-9_:]*\s*,\s*(\d+)\s*,\s*(\d+)\s*>",
        context,
    )
    if not match:
        return None
    size = int(match.group(1))
    start, end = match.span(2)
    replacement = str(size + 8)
    return context[:start] + replacement + context[end:]


def mutate_bfloat16_on_aie1(context: str) -> str | None:
    # Swap aie::accum<accfloat, N> on an integer kernel to bfloat16 accumulator to create AIE1 incompatibility.
    # Guard: only apply if file mentions int16/int32 (i.e., appears to be AIE1 integer code).
    if "int16" not in context and "int32" not in context:
        return None
    match = re.search(r"aie::vector\s*<\s*int16\s*,\s*(\d+)\s*>", context)
    if not match:
        return None
    start, end = match.span()
    width = match.group(1)
    replacement = f"aie::vector<bfloat16, {width}>"
    return context[:start] + replacement + context[end:]


def mutate_signed_unsigned_mismatch(context: str) -> str | None:
    # Swap int16 to uint16 in a function signature to cause sign-extension mismatches.
    match = re.search(r"\b(int16)\b\s*(?=[*&]|\s+[A-Za-z_])", context)
    if not match:
        return None
    start, end = match.span(1)
    return context[:start] + "uint16" + context[end:]


def mutate_unaligned_load(context: str) -> str | None:
    # aie::load_v<8>(ptr) -> aie::load_v<8>(ptr + 1) introduces an unaligned offset.
    match = re.search(r"aie::load_v\s*<\s*\d+\s*>\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", context)
    if not match:
        return None
    ptr = match.group(1)
    start, end = match.span()
    replacement = match.group(0).replace(ptr, f"{ptr} + 1")
    return context[:start] + replacement + context[end:]


def mutate_modulo_in_loop(context: str) -> str | None:
    # Inject `% N` into an array index inside a loop body. We replace the first occurrence of
    # a simple `arr[i]` pattern with `arr[i % 32]` to inject modulo overhead the pipeliner hates.
    match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]", context)
    if not match:
        return None
    arr = match.group(1)
    idx = match.group(2)
    # Skip if it's already clearly wrapping (contains '%').
    if "%" in context[match.start() : match.end()]:
        return None
    # Avoid touching out-parameter assignments and common "out[i]" writes where adding modulo hides the bug.
    if arr in {"out", "output"}:
        return None
    start, end = match.span()
    replacement = f"{arr}[{idx} % 32]"
    return context[:start] + replacement + context[end:]


def mutate_break_accumulator_reset(context: str) -> str | None:
    # Remove a `acc = aie::zeros<...>();` reset line so accumulator carries DC offset across calls.
    span = find_first_match_span(context, r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*aie::zeros\s*<[^>]*>\s*\(\s*\)\s*;\s*$")
    if not span:
        return None
    return replace_span_with_line_comment(
        context,
        span,
        "Removed accumulator reset so DC offset accumulates across kernel invocations.",
    )


def mutate_begin_vector_non_power_of_two(context: str) -> str | None:
    match = re.search(r"begin_vector\s*<\s*(\d+)\s*>", context)
    if not match:
        return None
    current = int(match.group(1))
    replacement = 6 if current != 6 else 10
    start, end = match.span(1)
    return context[:start] + str(replacement) + context[end:]


BUG_SYMPTOM_STRINGS: dict[str, list[str]] = {
    "stream_deadlock_unbalanced_tokens": [
        "simulator hangs after the first window; producer kernel blocks on stream FIFO full",
        "graph stalls indefinitely; consumer kernel is starved while producer backpressures",
        "pipeline deadlocks on iteration 2 because one stream direction stopped producing tokens",
    ],
    "missing_output_write": [
        "output file contains only leading zeros; downstream kernel sees a silent producer",
        "no token is ever written to the output stream, so aiesimulator reports 0 samples produced",
        "consumer times out waiting on input because the writeincr call was removed",
    ],
    "off_by_one_oob": [
        "aiesimulator reports an out-of-bounds access at the final loop iteration",
        "last output sample is corrupted and subsequent buffer contents are overwritten",
        "memory fault at the tile when the loop bound is exceeded by one element",
    ],
    "missing_iterator_increment": [
        "output buffer contains the same value repeated across every lane",
        "all output samples equal the first computed sample; iterator never advances",
        "write pointer stays at offset 0, so only the first vector position receives data",
    ],
    "runtime_ratio_zero": [
        "graph compiler reports schedule infeasible: runtime ratio of zero for an active kernel",
        "aiecompiler error: runtime<ratio> = 0 prevents the kernel from being scheduled on any tile",
        "kernel is never scheduled, so its outputs remain uninitialized",
    ],
    "mismatched_plio_width": [
        "PLIO width mismatch detected during graph compilation; connected port expects a different bit width",
        "streamed data is byte-swapped or truncated because the PLIO bit width no longer matches the kernel port",
        "simulation produces zeroed or shifted samples due to the PLIO width reconfiguration",
    ],
    "reversed_connect_direction": [
        "adf::connect error: trying to drive an input port with another input port",
        "graph compile fails with mismatched port direction on the connect call",
        "compilation succeeds but no data flows because source and destination are swapped",
    ],
    "wrong_vector_lane_width": [
        "kernel produces scrambled lanes: output vector has the wrong number of active elements",
        "vector intrinsic dispatched on wrong lane count; results are shifted by one vector stride",
        "chess compiler warns about lane-count mismatch; output is half or double the expected size",
    ],
    "graph_buffer_dimension_mismatch": [
        "aiecompiler reports buffer dimension mismatch between graph-level and kernel-level extents",
        "kernel overruns its declared window because the graph dimension no longer matches the kernel trip count",
        "simulation corrupts neighboring buffers when the kernel writes past the reduced graph dimension",
    ],
    "subtraction_instead_of_addition": [
        "output has inverted sign on the DC component; accumulator trends negative over time",
        "filter output is magnitude-correct but phase-inverted relative to the reference",
        "unit test diff shows each output sample is (expected - 2*offset) instead of expected",
    ],
    "aie_add_used_instead_of_aie_mul": [
        "output magnitude is linear in the input when it should scale multiplicatively",
        "MAC chain collapses to a running sum; filter taps produce identical response for all inputs",
        "functional simulation shows output tracks input plus coefficients rather than input times coefficients",
    ],
    "to_vector_shift_parameter_is_15_instead_of_0": [
        "all output samples are zero or near-zero even though the accumulator held the correct result",
        "output magnitude is attenuated by ~32768x; every MSB is shifted off the vector",
        "post-shift vector is effectively flushed because a 15-bit right shift of a 16-bit value leaves one bit",
    ],
    "acc48_instead_of_acc80_for_int32xint32": [
        "int32xint32 MAC chain wraps to a negative value after roughly 256 accumulations",
        "output is correct for small inputs but saturates or wraps once the product exceeds 48 bits",
        "accumulator precision loss causes the last output block to drift by a few LSBs on every iteration",
    ],
    "wrong_loop_count_16_instead_of_32": [
        "simulation produces exactly half the expected output samples and leaves the tail of the buffer untouched",
        "downstream kernel starves after the first half-frame because the producer exits early",
        "aiesimulator reports fewer iterations than the graph-level rate expects",
    ],
    "window_size_uses_wrong_literal_128_instead_of_256": [
        "graph-compile error: kernel reads 256 samples but graph window only provides 128",
        "kernel iterates past the declared window and overruns the next buffer in the bank",
        "alternate frames are garbage because the window halves between graph and kernel views",
    ],
    "accumulator_initialized_with_aie_mul_garbage_instead_of_aie_zeros": [
        "first output block has an unexplained DC offset that stays constant across runs",
        "output is correct after the first call only when the previous accumulator happened to be zero",
        "unit test for the first frame fails while subsequent frames look fine",
    ],
    "broadcast_width_does_not_match_vector_width_broadcast_int16_4_with_vector_int16_8": [
        "chess compiler error: broadcast lane count does not match destination vector lanes",
        "half the vector lanes receive the broadcast constant while the other half hold stale data",
        "output has a repeating 4-lane pattern superimposed on an 8-lane expected result",
    ],
    "reading_from_wrong_stream_variable_in_a_twice_instead_of_in_a_then_in_b": [
        "kernel consumes in_a twice per iteration; in_b backpressures and eventually deadlocks the graph",
        "output is computed entirely from the first input stream; the second input is never drained",
        "simulation deadlocks after the FIFO on in_b fills because the kernel never issues a readincr on it",
    ],
    "stream_kernel_drops_last_sample_in_each_block_from_n_1_loop_bound": [
        "last sample of every block is missing; output file is 1/N shorter than the reference",
        "downstream kernel sees a periodic gap every N samples, shifting the phase of the signal",
        "loop bound is N-1, so the final iteration that should process sample index N-1 never runs",
    ],
    "missing_chess_prepare_for_pipelining_causing_3x_throughput_loss": [
        "aiecompiler reports loop is not pipelined; effective throughput is 3x worse than expected",
        "kernel iteration latency doubles because the VLIW slots are not packed across the loop body",
        "chess compiler emits a missed-pipelining diagnostic and inserts sequential scheduling",
    ],
    "readincr_from_output_stream_instead_of_input_stream": [
        "graph compile fails: readincr target is declared as an output stream",
        "kernel drains the output FIFO and produces garbage because the read direction is inverted",
        "simulator errors with wrong stream direction on the readincr call",
    ],
    "break_statement_in_pipelined_loop_chess_compiler_cannot_pipeline_with_early_exits": [
        "chess compiler refuses to pipeline the inner loop due to an early exit branch",
        "kernel throughput collapses because the VLIW schedule is serialized around the break",
        "compiler warning: loop contains break; software pipelining disabled",
    ],
    "runtime_ratios_do_not_add_up_three_kernels_on_same_tile_sum_to_1_0": [
        "aiecompiler reports tile overcommitted: sum of runtime ratios exceeds 1.0",
        "mapper cannot co-locate the kernels because their scheduler budgets exceed the tile",
        "one of the kernels is silently dropped from the schedule and never executes",
    ],
    "port_index_out_of_range_k1_in_2_when_kernel_only_has_2_inputs": [
        "graph compile fails: port index exceeds the declared input count on the kernel",
        "mapper reports invalid port reference at the connect site",
        "connect call references a port index that is outside the kernel's declared port array",
    ],
    "graph_wait_missing_after_graph_run_host_reads_output_before_kernels_finish": [
        "host reads output buffer before the kernel finishes and sees garbage or partial results",
        "intermittent test failures because the host races the AIE array",
        "output snapshot depends on host timing; runs on faster machines are empty",
    ],
    "missing_connection_kernel_port_declared_but_never_connected_in_graph_constructor": [
        "kernel port is declared but never wired; aiecompiler reports unconnected port",
        "data never flows to that kernel input and the consumer blocks on stream empty",
        "simulation deadlocks because the declared port expects tokens that are never produced",
    ],
    "self_loop_kernel_output_connected_back_to_its_own_input_without_intermediate_buffer": [
        "graph compile fails: direct self-loop without an intermediate buffer is illegal",
        "simulation deadlocks immediately because the kernel feeds itself before any token is produced",
        "mapper cannot schedule the kernel because its output port is also its input source",
    ],
    "to_vector_output_type_does_not_match_buffer_type_int32_vs_int16": [
        "output buffer stores the wrong width and downstream kernel reads scrambled samples",
        "compile error: to_vector template type does not match the buffer element type",
        "every other output sample is garbage because the store stride is twice the buffer element width",
    ],
    "missing_adf_source_assignment": [
        "graph compile fails: kernel has no source function binding",
        "mapper cannot locate the kernel implementation because adf::source() is missing",
        "linker error: kernel declared but no source file associated with it",
    ],
    "missing_adf_runtime_line_entirely": [
        "scheduler cannot place the kernel because its runtime ratio is never declared",
        "aiecompiler assumes a default runtime ratio that does not match the kernel's actual cost",
        "kernel never reaches steady state because its schedule budget is unspecified",
    ],
    "window_margin_larger_than_window_size_invalid_configuration": [
        "graph compile rejects the window template: margin exceeds the total window size",
        "kernel attempts to read a margin region that does not fit in the declared window",
        "mapper reports invalid window configuration and refuses to proceed",
    ],
    "bfloat16_kernel_on_aie1_tile_bfloat16_only_supported_on_aie_ml": [
        "chess compiler rejects bfloat16 intrinsics on an AIE1 tile",
        "kernel fails to generate code because bfloat16 is only available on AIE-ML",
        "compile error: bfloat16 vector type not supported on target architecture",
    ],
    "signed_unsigned_mismatch_uint16_data_processed_as_int16_causing_sign_extension_errors": [
        "large input values wrap to negatives because the kernel sign-extends unsigned data",
        "output shows a sign-extension artifact at the MSB boundary",
        "unit test fails for inputs above 0x8000 because of the implicit signed interpretation",
    ],
    "unaligned_access_pointer_not_aligned_to_128_bit_boundary_for_vector_load": [
        "aie::load_v faults on hardware because the pointer crosses a 128-bit boundary unaligned",
        "simulator tolerates the load but hardware raises an alignment fault",
        "intermittent corruption: first vector load after each block returns shifted data",
    ],
    "modulo_operation_in_loop_for_circular_buffer_should_use_chess_circular_buffer_pragma": [
        "inner loop throughput tanks because the modulo operation is not pipelined",
        "chess compiler cannot vectorize the loop due to the explicit modulo arithmetic",
        "kernel misses its latency budget because % is serialized per iteration",
    ],
    "accumulator_not_reset_between_output_blocks_dc_offset_accumulates_across_calls": [
        "output grows an unexplained DC offset over successive kernel invocations",
        "first block is correct but every subsequent block accumulates an additional bias",
        "unit tests pass on the first call but fail when the kernel is invoked back-to-back",
    ],
    "begin_vector_width_not_a_power_of_2_e_g_begin_vector_6_which_is_invalid_on_aie": [
        "chess compiler error: begin_vector template width must be a power of two",
        "iterator declared with a non-power-of-two width fails type checking on AIE",
        "compile error: unsupported lane count for begin_vector instantiation",
    ],
}


def pick_symptom(bug_type: str | None, seed_key: str) -> str:
    if not bug_type:
        return "stalls, corrupted output, or throughput collapse"
    options = BUG_SYMPTOM_STRINGS.get(bug_type)
    if not options:
        return "stalls, corrupted output, or throughput collapse"
    return options[deterministic_index(seed_key, len(options))]


def extract_code_anchors(context: str) -> dict:
    anchors: dict = {"class": None, "function": None, "identifier": None, "line_count": 0}
    if not context:
        return anchors

    anchors["line_count"] = context.count("\n") + 1

    stripped = re.sub(r"^\s*#\s*include\b.*$", "", context, flags=re.MULTILINE)

    class_match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
    if class_match:
        anchors["class"] = class_match.group(1)

    func_match = re.search(
        r"\b(?:void|int|float|cint32|cint16|int16|int32|uint16|uint32|bfloat16|bool)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        stripped,
    )
    if func_match:
        anchors["function"] = func_match.group(1)

    ident_match = re.search(r"\b(in_iter|out_iter|in_stream|out_stream|buf|coeff|delay_line|acc|window|stream)[A-Za-z0-9_]*", stripped)
    if ident_match:
        anchors["identifier"] = ident_match.group(0)

    return anchors


def anchor_phrase(anchors: dict) -> str:
    parts = []
    if anchors.get("function"):
        parts.append(f"function `{anchors['function']}`")
    if anchors.get("class"):
        parts.append(f"class `{anchors['class']}`")
    if anchors.get("identifier"):
        parts.append(f"identifier `{anchors['identifier']}`")
    if not parts:
        return ""
    return "Look at " + " and ".join(parts) + " in the source below. "


def build_unified_diff(buggy: str, correct: str, buggy_label: str = "buggy", correct_label: str = "correct") -> str:
    buggy_lines = buggy.rstrip("\n").splitlines(keepends=True)
    correct_lines = correct.rstrip("\n").splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        buggy_lines,
        correct_lines,
        fromfile=buggy_label,
        tofile=correct_label,
        n=3,
    )
    diff_text = "".join(diff_iter).rstrip("\n")
    if not diff_text:
        return ""
    # Keep diffs bounded to avoid enormous responses when files are very different.
    if len(diff_text) > 4000:
        diff_text = diff_text[:4000].rstrip() + "\n... (diff truncated)"
    return diff_text


def build_mutation_candidates(info: dict) -> list[tuple[str, callable]]:
    interfaces = info["interfaces"]
    artifact_type = info["artifact_type"]

    mutation_candidates: list[tuple[str, callable]] = []
    if artifact_type == "graph_type":
        mutation_candidates.extend(
            [
                ("graph_buffer_dimension_mismatch", mutate_graph_dimension_mismatch),
                ("runtime_ratio_zero", mutate_runtime_ratio_zero),
                ("mismatched_plio_width", mutate_plio_width_mismatch),
                ("reversed_connect_direction", mutate_reversed_connect_direction),
                ("window_size_uses_wrong_literal_128_instead_of_256", mutate_window_size_halved),
                # New graph-level mutators.
                ("runtime_ratios_do_not_add_up_three_kernels_on_same_tile_sum_to_1_0", mutate_runtime_ratio_overflow),
                ("port_index_out_of_range_k1_in_2_when_kernel_only_has_2_inputs", mutate_port_index_oob),
                ("graph_wait_missing_after_graph_run_host_reads_output_before_kernels_finish", mutate_missing_graph_wait),
                ("missing_connection_kernel_port_declared_but_never_connected_in_graph_constructor", mutate_missing_connect),
                ("self_loop_kernel_output_connected_back_to_its_own_input_without_intermediate_buffer", mutate_self_loop_connect),
                ("missing_adf_source_assignment", mutate_missing_adf_source),
                ("missing_adf_runtime_line_entirely", mutate_missing_runtime_ratio),
                ("window_margin_larger_than_window_size_invalid_configuration", mutate_window_margin_to_size),
            ]
        )
    else:
        mutation_candidates.extend(
            [
                ("wrong_vector_lane_width", mutate_wrong_vector_lane_width),
                ("missing_iterator_increment", mutate_missing_iterator_increment),
                ("off_by_one_oob", mutate_window_oob),
                ("missing_output_write", mutate_missing_output_write),
                # Kernel-level arithmetic mutators.
                ("subtraction_instead_of_addition", mutate_subtraction_for_addition),
                ("aie_add_used_instead_of_aie_mul", mutate_mul_to_add),
                ("to_vector_shift_parameter_is_15_instead_of_0", mutate_tovector_shift_fifteen),
                ("acc48_instead_of_acc80_for_int32xint32", mutate_acc48_for_acc80),
                ("wrong_loop_count_16_instead_of_32", mutate_loop_count_halved),
                ("accumulator_initialized_with_aie_mul_garbage_instead_of_aie_zeros", mutate_remove_zeros_init),
                ("broadcast_width_does_not_match_vector_width_broadcast_int16_4_with_vector_int16_8", mutate_broadcast_width_mismatch),
                ("stream_kernel_drops_last_sample_in_each_block_from_n_1_loop_bound", mutate_drop_last_sample),
                # New kernel-level structural/scheduling mutators.
                ("missing_chess_prepare_for_pipelining_causing_3x_throughput_loss", mutate_remove_chess_pipelining),
                ("break_statement_in_pipelined_loop_chess_compiler_cannot_pipeline_with_early_exits", mutate_break_in_pipelined_loop),
                ("to_vector_output_type_does_not_match_buffer_type_int32_vs_int16", mutate_wrong_to_vector_output_type),
                ("signed_unsigned_mismatch_uint16_data_processed_as_int16_causing_sign_extension_errors", mutate_signed_unsigned_mismatch),
                ("unaligned_access_pointer_not_aligned_to_128_bit_boundary_for_vector_load", mutate_unaligned_load),
                ("modulo_operation_in_loop_for_circular_buffer_should_use_chess_circular_buffer_pragma", mutate_modulo_in_loop),
                ("accumulator_not_reset_between_output_blocks_dc_offset_accumulates_across_calls", mutate_break_accumulator_reset),
                ("begin_vector_width_not_a_power_of_2_e_g_begin_vector_6_which_is_invalid_on_aie", mutate_begin_vector_non_power_of_two),
                ("bfloat16_kernel_on_aie1_tile_bfloat16_only_supported_on_aie_ml", mutate_bfloat16_on_aie1),
                ("data_read_once_outside_loop_instead_of_inside_stale_data", mutate_dereference_moved_outside_loop),
            ]
        )

    if "Stream" in interfaces:
        mutation_candidates.append(("stream_deadlock_unbalanced_tokens", mutate_stream_deadlock))
        mutation_candidates.append(
            ("reading_from_wrong_stream_variable_in_a_twice_instead_of_in_a_then_in_b", mutate_duplicate_stream_read)
        )
        mutation_candidates.append(("readincr_from_output_stream_instead_of_input_stream", mutate_readincr_from_output))

    return mutation_candidates


def synthesize_bug_variants(info: dict, max_variants: int = MAX_SYNTHETIC_MUTATIONS_PER_SOURCE) -> list[tuple[str, str]]:
    context = info["context"]
    mutation_candidates = build_mutation_candidates(info)

    if not mutation_candidates:
        return []

    start = deterministic_index(source_group_key(info), len(mutation_candidates))
    variants = []
    seen_hashes = set()
    for offset in range(len(mutation_candidates)):
        bug_type, mutator = mutation_candidates[(start + offset) % len(mutation_candidates)]
        mutated = mutator(context)
        if mutated and mutated != context:
            mutation_hash = hashlib.md5((bug_type + "\n" + normalize_text_for_diversity(mutated)).encode("utf-8")).hexdigest()
            if mutation_hash in seen_hashes:
                continue
            seen_hashes.add(mutation_hash)
            variants.append((bug_type, mutated))
            if len(variants) >= max_variants:
                break

    return variants


def build_synthetic_bug_pair_entries(file_infos: list[dict], target_count: int = SYNTHETIC_BUG_PAIR_TARGET) -> list[dict]:
    candidates = []
    for info in file_infos:
        if not info.get("context"):
            continue
        # Keep synthetic mutations focused on clean sources so each pair represents a single, isolated defect.
        if info.get("bug_type"):
            continue
        mutations = synthesize_bug_variants(info)
        if not mutations:
            continue
        for bug_type, buggy_context in mutations:
            candidates.append((source_group_key(info), info, bug_type, buggy_context))

    # Stable deterministic ordering prevents random drift between rebuilds.
    candidates.sort(key=lambda item: hashlib.md5(item[0].encode("utf-8")).hexdigest())

    synthetic = []
    for _, info, bug_type, buggy_context in candidates[:target_count]:
        symptom = pick_symptom(bug_type, source_group_key(info) + ":" + bug_type)
        instruction, tier = build_tiered_bug_instruction(info, bug_type, symptom)
        anchors = extract_code_anchors(info["context"])

        metadata = {
            "source": info["source_url"] or str(info["relative_path"]).replace("\\", "/"),
            "source_repo": info["source_repo"],
            "source_branch": info["source_branch"],
            "source_path": info["source_path"],
            "relative_path": str(info["relative_path"]).replace("\\", "/"),
            "type": info["artifact_type"],
            "category": info["domain"],
            "hardware": "Versal AIE",
            "interfaces": info["interfaces"],
            "vector_types": info["vector_types"],
            "intrinsics": info["intrinsics"],
            "split": info["split"],
            "variant": "bug_fix_pair",
            "bug_type": bug_type,
            "symptom": symptom,
            "source_group": source_group_key(info),
            "synthetic": True,
            "difficulty_tier": tier,
        }

        buggy_info = {**info, "bug_type": bug_type, "symptom": symptom, "context": buggy_context}
        response = build_contrastive_response(buggy_info, info, bug_type, symptom, anchors=anchors)

        synthetic.append(
            {
                "instruction": instruction,
                "context": "Buggy version:\n" + buggy_context.rstrip() + "\n\nCorrect version:\n" + info["context"],
                "response": response,
                "metadata": metadata,
            }
        )

    return synthetic


def is_bug_focused_row(row: dict) -> bool:
    metadata = row.get("metadata", {})
    variant = str(metadata.get("variant", ""))
    if bool(metadata.get("bug_type")):
        return True
    if variant in {"causal_debugging", "bug_fix_pair", "multi_file_bug_fix_pair"}:
        return True
    return "debug" in variant


def row_stable_key(row: dict) -> str:
    metadata = row.get("metadata", {})
    key = {
        "source": metadata.get("source"),
        "variant": metadata.get("variant"),
        "bug_type": metadata.get("bug_type"),
        "instruction": row.get("instruction"),
    }
    return hashlib.md5(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()


def rebalance_bug_ratio(rows: list[dict], target_ratio: float) -> list[dict]:
    if not rows or target_ratio <= 0 or target_ratio >= 1:
        return rows

    bug_rows = [row for row in rows if is_bug_focused_row(row)]
    non_bug_rows = [row for row in rows if not is_bug_focused_row(row)]
    if not bug_rows:
        return rows

    bug_rows = diversify_bug_rows(bug_rows)

    current_ratio = len(bug_rows) / len(rows)
    if current_ratio >= target_ratio:
        return sorted(bug_rows + non_bug_rows, key=row_stable_key)

    max_non_bug = int((len(bug_rows) * (1 - target_ratio)) / target_ratio)
    selected_non_bug = pick_diverse_non_bug_rows(non_bug_rows, max_non_bug)
    balanced = bug_rows + selected_non_bug
    return sorted(balanced, key=row_stable_key)


def pick_diverse_non_bug_rows(non_bug_rows: list[dict], max_non_bug: int) -> list[dict]:
    if max_non_bug <= 0:
        return []

    ordered_rows = sorted(non_bug_rows, key=row_stable_key)
    by_source_group: dict[str, list[dict]] = {}
    for row in ordered_rows:
        metadata = row.get("metadata", {})
        source_group = str(metadata.get("source_group") or metadata.get("relative_path") or "unknown")
        by_source_group.setdefault(source_group, []).append(row)

    selected: list[dict] = []
    source_groups = sorted(by_source_group.keys())
    for source_group in source_groups:
        if len(selected) >= max_non_bug:
            break
        selected.append(by_source_group[source_group][0])

    if len(selected) >= max_non_bug:
        return selected[:max_non_bug]

    selected_keys = {row_stable_key(row) for row in selected}
    for row in ordered_rows:
        if len(selected) >= max_non_bug:
            break
        key = row_stable_key(row)
        if key in selected_keys:
            continue
        selected.append(row)
        selected_keys.add(key)

    return selected[:max_non_bug]


def rebalance_bug_ratio_by_split(rows: list[dict], target_ratio: float) -> list[dict]:
    split_buckets: dict[str, list[dict]] = {"train": [], "validation": []}
    for row in rows:
        split = row.get("metadata", {}).get("split", "train")
        split_buckets.setdefault(split, []).append(row)

    balanced_rows = []
    for split in sorted(split_buckets.keys()):
        balanced_rows.extend(rebalance_bug_ratio(split_buckets[split], target_ratio))
    return balanced_rows


TIER_TARGET_DISTRIBUTION = {
    "easy": 0.20,
    "normal": 0.30,
    "medium": 0.25,
    "hard": 0.15,
    "extra_hard": 0.10,
}


def balance_tier_distribution(rows: list[dict], target: dict[str, float] | None = None) -> list[dict]:
    if not rows:
        return rows
    target = target or TIER_TARGET_DISTRIBUTION

    bug_rows = [row for row in rows if is_bug_focused_row(row)]
    non_bug_rows = [row for row in rows if not is_bug_focused_row(row)]
    if not bug_rows:
        return rows

    by_tier: dict[str, list[dict]] = {}
    for row in bug_rows:
        tier = str(row.get("metadata", {}).get("difficulty_tier") or "normal")
        by_tier.setdefault(tier, []).append(row)

    total_bugs = len(bug_rows)
    kept: list[dict] = []
    # Cap each tier to its target share of the current bug-row total. Within a
    # tier, round-robin across bug_types so no single slug is systematically
    # dropped by alphabetic truncation (important for full-taxonomy coverage).
    for tier, pool in by_tier.items():
        cap = int(total_bugs * target.get(tier, 1.0)) if tier in target else len(pool)
        cap = max(cap, 0)
        if tier not in target:
            kept.extend(sorted(pool, key=row_stable_key))
            continue
        by_slug: dict[str, list[dict]] = {}
        for r in sorted(pool, key=row_stable_key):
            slug_key = str(r.get("metadata", {}).get("bug_type") or "unknown")
            by_slug.setdefault(slug_key, []).append(r)
        selected: list[dict] = []
        slug_iters = {s: iter(rows_) for s, rows_ in by_slug.items()}
        while len(selected) < cap and slug_iters:
            exhausted = []
            for s, it in list(slug_iters.items()):
                if len(selected) >= cap:
                    break
                try:
                    selected.append(next(it))
                except StopIteration:
                    exhausted.append(s)
            for s in exhausted:
                slug_iters.pop(s, None)
        # Always guarantee at least one row survives per slug present in the pool
        # (even if the cap would have been exceeded) so taxonomy coverage is preserved.
        kept_slugs = {str(r.get("metadata", {}).get("bug_type") or "unknown") for r in selected}
        for slug_key, rows_ in by_slug.items():
            if slug_key not in kept_slugs and rows_:
                selected.append(rows_[0])
        kept.extend(selected)

    balanced = kept + non_bug_rows
    return sorted(balanced, key=row_stable_key)


def cap_rows_by_repo(rows: list[dict], max_fraction: float = 0.15) -> list[dict]:
    if not rows or max_fraction <= 0 or max_fraction >= 1:
        return rows

    total = len(rows)
    per_repo_limit = max(1, int(total * max_fraction))

    by_repo: dict[str, list[dict]] = {}
    for row in sorted(rows, key=row_stable_key):
        repo = str(row.get("metadata", {}).get("source_repo") or "local")
        by_repo.setdefault(repo, []).append(row)

    capped: list[dict] = []
    for repo, repo_rows in by_repo.items():
        capped.extend(repo_rows[:per_repo_limit])

    return sorted(capped, key=row_stable_key)


def compute_split(info: dict) -> str:
    return "validation" if deterministic_index(source_group_key(info), 5) == 0 else "train"


def build_entry(info: dict) -> dict:
    repo, branch, source_path, source_url = info["source_repo"], info["source_branch"], info["source_path"], info["source_url"]
    metadata = {
        "source": source_url or str(info["relative_path"]).replace("\\", "/"),
        "source_repo": repo,
        "source_branch": branch,
        "source_path": source_path,
        "relative_path": str(info["relative_path"]).replace("\\", "/"),
        "type": info["artifact_type"],
        "category": info["domain"],
        "hardware": "Versal AIE",
        "interfaces": info["interfaces"],
        "vector_types": info["vector_types"],
        "intrinsics": info["intrinsics"],
        "split": info["split"],
        "source_group": source_group_key(info),
    }
    if info["bug_type"]:
        metadata["bug_type"] = info["bug_type"]
        if info.get("bug_label"):
            metadata["bug_label"] = info["bug_label"]
        metadata["difficulty_tier"] = infer_bug_tier(info["bug_type"])
    if info["symptom"]:
        metadata["symptom"] = info["symptom"]

    return {
        "instruction": build_instruction(info),
        "context": info["context"],
        "response": build_response(info),
        "metadata": metadata,
    }


def gather_file_info(path: Path) -> dict:
    raw_text = path.read_text(encoding="utf-8")
    header, context = split_leading_comment(raw_text)
    if not is_relevant_aie_source(context, path):
        return None
    header_metadata = parse_header_metadata(header)
    artifact_type = classify_artifact_type(context, path)
    vector_types = extract_vector_types(context, header_metadata)
    interfaces = extract_interface_types(context, header_metadata)
    intrinsics = extract_intrinsics(context, header_metadata, artifact_type)
    math_operation = extract_math_operation(path, context)
    summary = summarize_purpose(path, artifact_type, interfaces, vector_types, math_operation)
    raw_bug_type = header_metadata.get("bug type")

    return {
        "path": path,
        "relative_path": path.relative_to(ROOT),
        "header_metadata": header_metadata,
        "context": context,
        "artifact_type": artifact_type,
        "vector_types": vector_types,
        "interfaces": interfaces,
        "intrinsics": intrinsics,
        "math_operation": math_operation,
        "summary": summary,
        "domain": header_metadata.get("domain") or path.parent.name.replace("_", " "),
        "bug_type": canonical_bug_type(raw_bug_type),
        "bug_label": raw_bug_type,
        "symptom": header_metadata.get("symptom"),
        "split": "train",
    }


def infer_relative_path_from_row(row: dict, index: int) -> Path:
    metadata = row.get("metadata", {}) or {}
    source_path = row.get("source_path") or metadata.get("path")
    if source_path:
        return Path("expanded_sources") / Path(str(source_path).replace("\\", "/"))
    filename = row.get("filename") or f"expanded_source_{index}.cpp"
    return Path("expanded_sources") / filename


def parse_source_row(row: dict, index: int) -> dict | None:
    context = row.get("code", "")
    relative_path = infer_relative_path_from_row(row, index)
    path = ROOT / relative_path
    if not context or not is_relevant_aie_source(context, path):
        return None

    metadata = row.get("metadata", {}) or {}
    category = row.get("category")
    artifact_type = "graph_type" if category in {"graph", "buggy_graph"} else classify_artifact_type(context, path)
    raw_bug_type = row.get("bug_type")
    bug_type = canonical_bug_type(raw_bug_type)
    bug_explanation = row.get("bug_explanation")
    if bug_type and not bug_explanation:
        bug_explanation = row.get("bug_explanation")

    header_metadata = {
        "domain": normalize_metadata_text(metadata.get("compute_pattern"), category or path.parent.name.replace("_", " ")),
        "path": row.get("source_path") or metadata.get("path") or row.get("filename"),
    }

    source_repo = row.get("repo")
    source_branch = row.get("branch")
    source_url = row.get("source_url") or (row.get("source") if row.get("source") != "synthetic" else None)

    vector_types = extract_vector_types(context, header_metadata)
    interfaces = extract_interface_types(context, header_metadata)
    intrinsics = extract_intrinsics(context, header_metadata, artifact_type)
    math_operation = extract_math_operation(path, context)
    summary = summarize_purpose(path, artifact_type, interfaces, vector_types, math_operation)

    return {
        "path": path,
        "relative_path": relative_path,
        "header_metadata": header_metadata,
        "context": context,
        "artifact_type": artifact_type,
        "vector_types": vector_types,
        "interfaces": interfaces,
        "intrinsics": intrinsics,
        "math_operation": math_operation,
        "summary": summary,
        "domain": normalize_metadata_text(metadata.get("compute_pattern"), category or path.parent.name.replace("_", " ")),
        "bug_type": bug_type,
        "bug_label": raw_bug_type,
        "symptom": bug_explanation,
        "split": "train",
        "source_repo": source_repo,
        "source_branch": source_branch,
        "source_path": row.get("source_path") or metadata.get("path") or row.get("filename"),
        "source_url": source_url,
        "source_origin": row.get("source"),
        "expanded_source": True,
        "row_category": row.get("category"),
        "row_filename": row.get("filename"),
    }


def dedupe_file_infos(file_infos: list[dict]) -> list[dict]:
    deduped = []
    seen_hashes = set()
    for info in file_infos:
        content_hash = hashlib.sha256(info["context"].encode("utf-8")).hexdigest()
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        deduped.append(info)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the AIE instruction dataset from local sources and optional expanded JSONL sources.")
    parser.add_argument("--expanded-source-jsonl", type=Path, default=DEFAULT_EXPANDED_SOURCE_JSONL)
    parser.add_argument("--skip-expanded-source-jsonl", action="store_true")
    parser.add_argument(
        "--v2",
        action="store_true",
        help="Emit the v2 dataset: debug responses are the full corrected source code (no diffs, no explanatory prose). Outputs go to *_v2_* filenames.",
    )
    return parser


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def main() -> None:
    args = build_parser().parse_args()

    global V2_MODE
    V2_MODE = bool(args.v2)

    if V2_MODE:
        # Crank up per-tier scenario quotas so every taxonomy entry gets many more
        # debug examples, broadening the model's exposure to distinct bug patterns.
        TAXONOMY_SCENARIOS_PER_TIER.update({
            "easy": 24,
            "normal": 24,
            "medium": 30,
            "hard": 80,
            "extra_hard": 80,
            "cross_cutting": 24,
        })
        TAXONOMY_MULTI_FILE_SCENARIOS_PER_TIER.update({
            "easy": 8,
            "normal": 8,
            "medium": 10,
            "hard": 20,
            "extra_hard": 20,
            "cross_cutting": 8,
        })

    file_infos = [info for info in (gather_file_info(path) for path in load_source_files()) if info is not None]
    if not args.skip_expanded_source_jsonl:
        expanded_rows = load_expanded_source_rows(args.expanded_source_jsonl)
        file_infos.extend(
            info for info in (parse_source_row(row, index) for index, row in enumerate(expanded_rows)) if info is not None
        )

    file_infos = dedupe_file_infos(file_infos)
    source_index = build_source_index(file_infos)

    for info in file_infos:
        repo, branch, source_path, source_url = build_source_url(info, source_index)
        info["source_repo"] = repo
        info["source_branch"] = branch
        info["source_path"] = source_path
        info["source_url"] = source_url
        info["split"] = compute_split(info)

    rows = []
    for info in file_infos:
        rows.extend(build_entries_for_info(info))

    # V2 tightens caps. bug_pair/multi/synthetic rows often carry bug_type="unknown",
    # so an aggressive cap would collapse them; we keep those generous and tighten
    # only the taxonomy buckets where bug_type is always populated.
    bug_pair_records = build_bug_pair_records(file_infos)
    if V2_MODE:
        v2_diversify_kwargs = {"max_per_bug_type": 6_000, "max_per_source_group": 10_000}
        v2_taxonomy_cap = 400
        v2_taxonomy_multi_cap = 300
    else:
        v2_diversify_kwargs = {}
        v2_taxonomy_cap = TAXONOMY_SCENARIOS_PER_BUG_TYPE
        v2_taxonomy_multi_cap = TAXONOMY_MULTI_FILE_SCENARIOS_PER_BUG_TYPE

    bug_pair_rows = diversify_bug_rows(build_bug_pair_entries_from_records(bug_pair_records), **v2_diversify_kwargs)
    multi_file_bug_rows = diversify_bug_rows(build_multi_file_bug_pair_entries(file_infos, bug_pair_records), **v2_diversify_kwargs)
    synthetic_bug_rows = diversify_bug_rows(build_synthetic_bug_pair_entries(file_infos), **v2_diversify_kwargs)
    taxonomy_bug_rows = diversify_bug_rows(
        build_taxonomy_debug_scenario_entries(file_infos),
        max_per_bug_type=v2_taxonomy_cap,
        max_per_source_group=10_000,
    )
    taxonomy_multi_file_rows = diversify_bug_rows(
        build_taxonomy_multi_file_debug_entries(file_infos),
        max_per_bug_type=v2_taxonomy_multi_cap,
        max_per_source_group=10_000,
    )

    rows.extend(bug_pair_rows)
    rows.extend(multi_file_bug_rows)
    rows.extend(synthetic_bug_rows)
    rows.extend(taxonomy_bug_rows)
    rows.extend(taxonomy_multi_file_rows)

    if V2_MODE:
        # Cropped companion variants: same bug, tight context window.
        cropped = build_v2_cropped_variants(
            bug_pair_rows + synthetic_bug_rows + multi_file_bug_rows + taxonomy_bug_rows,
            max_variants=1200,
        )
        rows.extend(cropped)
        # Clean-code distractors: balance the "not present" examples with
        # legitimate "looks fine" responses so the model doesn't always refuse.
        clean_rows = build_v2_clean_code_entries(file_infos, target_rows=500)
        rows.extend(clean_rows)
        # Compiler-error-first rows: real users paste aiecompiler / chess /
        # aiesimulator error fragments, not prose. Rewrite a subset of bug rows
        # to lead with a realistic error message.
        err_rows = build_v2_compiler_error_rows(
            bug_pair_rows + synthetic_bug_rows + taxonomy_bug_rows,
            max_rows=800,
        )
        rows.extend(err_rows)

    rows = rebalance_bug_ratio_by_split(rows, BUG_FOCUSED_TARGET_RATIO)

    if V2_MODE:
        # Shift tier targets toward harder cases - the 494 benchmark's failure
        # modes concentrate at hard/extra_hard, but the default weighting leaves
        # them at 25% combined. Bump to 45% combined.
        v2_tier_targets = {
            "easy": 0.12,
            "normal": 0.22,
            "medium": 0.21,
            "hard": 0.25,
            "extra_hard": 0.20,
        }
        rows = balance_tier_distribution(rows, target=v2_tier_targets)
    else:
        rows = balance_tier_distribution(rows)

    rows = cap_rows_by_repo(rows, max_fraction=0.15)
    rows = rebalance_bug_ratio_by_split(rows, BUG_FOCUSED_TARGET_RATIO)

    if V2_MODE:
        # Cap inspection-negative share so the model doesn't collapse to
        # "Verdict: not present" as a default answer.
        rows = cap_negative_ratio(rows, max_ratio=0.18)
        # Drop near-identical short-response duplicates (verdicts, not full files).
        rows = dedup_near_identical_responses(rows, max_per_response_key=40, short_threshold=500)

    train_rows = [row for row in rows if row["metadata"]["split"] == "train"]
    validation_rows = [row for row in rows if row["metadata"]["split"] == "validation"]

    if V2_MODE:
        # 3% general-AIE mix-in from v1 (explanation / extraction / causal rows)
        # to keep the model from overfitting to "return fixed code" as its only
        # behavior. v1 rows are already group-split and filtered to their train
        # side, so no v1 source leaks into the V2 held-out set.
        mixin_target = max(200, int(len(train_rows) * 0.03))
        mixin_rows = build_v2_general_code_mixin(OUTPUT_ALL, target_rows=mixin_target)
        # Drop any mixin row whose group_id collides with the V2 validation groups
        # (defense in depth - v1 split already excludes these, but belt-and-braces).
        val_groups = {_row_group_id(r) for r in validation_rows}
        mixin_rows = [r for r in mixin_rows if r["metadata"].get("group_id") not in val_groups]
        train_rows = train_rows + mixin_rows
    else:
        mixin_rows = []

    # Stamp group_id on every row so downstream trainers can do group-aware splits.
    stamp_group_ids(train_rows)
    stamp_group_ids(validation_rows)

    if V2_MODE:
        out_all, out_train, out_val, out_upload = OUTPUT_ALL_V2, OUTPUT_TRAIN_V2, OUTPUT_VALIDATION_V2, OUTPUT_UPLOAD_V2
    else:
        out_all, out_train, out_val, out_upload = OUTPUT_ALL, OUTPUT_TRAIN, OUTPUT_VALIDATION, OUTPUT_UPLOAD

    # In V2 the `_all` file is the "give this single file to the trainer"
    # artifact: it contains ONLY train-side rows (+ mix-in). The held-out
    # `_validation.jsonl` file never overlaps any source in `_all`, so when
    # axolotl / Unsloth splits `_all` internally, its internal eval rows come
    # from the same train-side source files - the separate `_validation.jsonl`
    # remains a clean, source-grouped holdout for post-training evaluation.
    if V2_MODE:
        write_jsonl(out_all, train_rows)
    else:
        write_jsonl(out_all, rows)
    write_jsonl(out_train, train_rows)
    write_jsonl(out_val, validation_rows)
    if V2_MODE:
        write_jsonl(out_upload, train_rows)
    else:
        write_jsonl(out_upload, rows)

    unique_source_groups = len({source_group_key(info) for info in file_infos})
    bug_rows = [row for row in rows if is_bug_focused_row(row)]
    bug_ratio = (len(bug_rows) / len(rows)) if rows else 0.0
    debug_variant_types = sorted({str(row.get("metadata", {}).get("variant", "")) for row in bug_rows})
    distinct_bug_types = sorted(
        {
            str(row.get("metadata", {}).get("bug_type"))
            for row in bug_rows
            if row.get("metadata", {}).get("bug_type")
        }
    )
    taxonomy_variants = {
        "taxonomy_debug_scenario",
        "taxonomy_multi_file_debug_scenario",
        "taxonomy_inspection_negative",
        "taxonomy_multi_file_inspection_negative",
        "synthetic_taxonomy_bug_fix",
    }
    taxonomy_bug_type_coverage = len(
        {
            str(row.get("metadata", {}).get("bug_type"))
            for row in rows
            if row.get("metadata", {}).get("variant") in taxonomy_variants
        }
    )

    print(
        f"Processed {len(file_infos)} unique source files across {unique_source_groups} unique source groups. "
        f"Wrote {len(rows)} total rows, {len(train_rows)} train rows, and {len(validation_rows)} validation rows. "
        f"Bug-focused rows: {len(bug_rows)} ({bug_ratio:.2%}). "
        f"Debug variant types: {len(debug_variant_types)}. Distinct bug types: {len(distinct_bug_types)}. "
        f"Taxonomy bug types covered: {taxonomy_bug_type_coverage}/{len(BUG_TAXONOMY_ENTRIES)}."
    )
    if V2_MODE:
        print(
            f"V2: _all file contains train-side rows only (no source overlap with _validation.jsonl). "
            f"General-AIE v1 mix-in: {len(mixin_rows)} rows "
            f"({(len(mixin_rows)/max(1,len(train_rows))):.2%} of train)."
        )


if __name__ == "__main__":
    main()