#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


ROOT = Path(__file__).resolve().parent
DEFAULT_SECRETS_PATH = ROOT / "secrets.json"
DEFAULT_OUT_DIR = ROOT / "generated"
DEFAULT_OUT_PATH = DEFAULT_OUT_DIR / "claude_bug_families.jsonl"
DEFAULT_SUMMARY_PATH = DEFAULT_OUT_DIR / "claude_bug_families_summary.json"
DEFAULT_MUTATOR_DIR = DEFAULT_OUT_DIR / "mutators"

AREA_SPECS: list[tuple[str, str]] = [
    ("include_headers", "AIE and ADF includes, local headers, header guards, case sensitivity, relative include paths."),
    ("header_guards_and_preprocessor", "Include guards, macro collisions, conditional compilation, architecture guards, missing or inverted preprocessor branches."),
    ("graph_kernel_binding", "kernel::create, prototypes, kernel member declarations, templated kernels, namespace qualification."),
    ("kernel_prototypes_and_signatures", "Free kernel declarations, argument order, missing parameters, incompatible stream/window/buffer signatures, constness."),
    ("kernel_source_paths", "adf::source assignments, relative file paths, source filename mismatches, missing files, extension mismatches."),
    ("graph_connections", "connect templates, endpoint directionality, graph object wiring, missing semicolons, wrong object references."),
    ("graph_endpoint_indices", "in[N], out[N], kernel arrays, PLIO arrays, GMIO arrays, RTP arrays, out-of-range endpoint indexing."),
    ("plio_ports", "input/output PLIO direction, width enums, factories, filenames, port type mismatches, API spelling."),
    ("gmio_ports", "input/output GMIO direction, depth/burst parameters, factories, filename/path mismatches, port type mismatches."),
    ("rtp_parameters", "Runtime parameters, parameter direction, update APIs, async RTP, RTP arrays, kernel signature mismatches."),
    ("stream_scalar_interfaces", "input_stream/output_stream, readincr/writeincr scalar APIs, pointer and const mistakes, namespace errors."),
    ("stream_vector_interfaces", "readincr_v/writeincr_v, vector stream widths, lane mismatches, vector/scalar API confusion, pointer type mismatches."),
    ("window_interfaces", "input_window/output_window, window element type mismatches, read/write API misuse, margins, window sizes."),
    ("buffer_interfaces", "input_buffer/output_buffer, extents, begin_vector/begin_restrict_vector, restrict qualifiers, buffer direction mismatches."),
    ("cascade_streams", "input_cascade, output_cascade, cascade accumulator types, cascade connect templates, cascade API misuse."),
    ("graph_runtime_constraints", "runtime<ratio>, repetition_count, FIFO depth, dimensions, invalid literals, missing semicolons."),
    ("graph_location_constraints", "location<kernel>, tile coordinates, bank placement, buffer location, wrong template kinds, invalid tile arguments."),
    ("graph_lifecycle", "Graph instances, init/run/end calls, main signatures, constructor access, class braces, missing graph instance definitions."),
    ("graph_class_structure", "Graph base classes, constructor naming, member declarations, public/private sections, class semicolons and braces."),
    ("vector_load_store", "aie::load_v, store_v, begin_vector, begin_restrict_vector, pointer types, extract/insert indices."),
    ("vector_lane_widths", "Vector lane counts for int16, int32, float, complex, non-power-of-two widths, zero-width cases."),
    ("vector_shuffles_and_permutations", "shuffle_up, shuffle_down, interleave, concat, pack/unpack, invalid lane indices and unsupported shuffle signatures."),
    ("vector_slice_and_insert_ops", "extract, insert, upd_elem, get_lane, set_lane, subvector operations, invalid lane indices and size mismatches."),
    ("accumulator_types", "aie::accum types, acc48/acc64/acc80, accumulator lane counts, target-specific accumulator restrictions."),
    ("accumulator_initialization", "aie::zeros, broadcast initializers, garbage initializers, accumulator-to-vector conversions, invalid shifts."),
    ("arithmetic_intrinsics", "aie::mul, mac, add, sub, neg, shift intrinsics, operand mismatches and unsupported overloads."),
    ("sliding_mul_and_mac", "sliding_mul, sliding_mac, sliding window templates, tap counts, lane counts, offset mismatches."),
    ("complex_datatypes", "cint16, cint32, cfloat, scalar-vs-complex confusion, real/imag access, complex constructor misuse."),
    ("complex_intrinsics", "Complex multiply/add/mac overloads, conjugation APIs, accumulator mismatches, complex vector width mismatches."),
    ("fixed_width_integer_types", "int16, int32, uint16, uint32, missing headers, AIE typedef spelling, scalar/vector element type mismatches."),
    ("template_arguments", "Template type/value arguments, constexpr requirements, argument count mismatches, dependent template syntax."),
    ("constexpr_and_constants", "constexpr removal, non-constant template arguments, invalid static constants, wrong numeric literal kinds."),
    ("namespaces_and_type_qualification", "adf:: and aie:: qualifiers, using directives, type spelling, missing namespace qualification."),
    ("function_and_member_naming", "Function name typos, member typos, constructor/class name mismatches, wrong overload symbol selection."),
    ("pointer_casts_and_aliasing", "reinterpret_cast misuse, incompatible pointer element types, alignment-sensitive casts, address arithmetic type errors."),
    ("memory_tiling_and_dimensions", "Dimensions, tiling parameters, extents, circular buffers, memory tile assumptions, incompatible shape values."),
    ("alignment_and_dm_resources", "alignas, aligned attributes, aie_dm_resource annotations, bank/resource names, invalid alignment values."),
    ("loop_pragmas_and_chess_macros", "chess_prepare_for_pipelining, loop count/range pragmas, missing macro contexts, unsupported pragma shapes."),
    ("source_file_layout", "Missing kernels/ subdir, wrong relative source layout, split-file assumptions, misplaced declarations across files."),
    ("port_directionality", "Producer/consumer confusion across port<input>/port<output>, PLIO/GMIO directions, stream/window direction mismatches."),
    ("port_datatypes", "Port element type mismatches across graph and kernel boundaries, scalar/complex mismatches, vector-width coupling."),
    ("api_spelling_regressions", "Specific misspellings of real AIE/ADF APIs like connect, runtime, source, kernel::create, gmio::create, plio::create."),
    ("architecture_guards", "AIE vs AIE-ML preprocessor guards, inverted #ifdef logic, wrong target macro checks, mixed architecture branches."),
    ("architecture_specific_apis", "AIE vs AIE-ML API availability, target-specific headers, datatypes, vector widths, accumulator restrictions."),
    ("compiler_diagnostics_driven_patterns", "Bug families explicitly shaped around prior compile errors such as acc48-vs-acc80, invalid broadcast, invalid shuffle calls, readincr_v width mismatch, wrong source path, wrong kernel symbol, and PLIO or GMIO direction mismatches."),
]


@dataclass(frozen=True)
class Settings:
    token: str
    region: str
    model_id: str
    model_label: str


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate AIE bug families with AWS Bedrock using a Claude Opus model.")
    ap.add_argument("--secrets", default=str(DEFAULT_SECRETS_PATH))
    ap.add_argument("--out", default=str(DEFAULT_OUT_PATH))
    ap.add_argument("--summary-out", default=str(DEFAULT_SUMMARY_PATH))
    ap.add_argument("--families-in", default=None, help="Existing family JSONL to use instead of generating a fresh family set.")
    ap.add_argument("--mutator-dir", default=str(DEFAULT_MUTATOR_DIR))
    ap.add_argument("--areas-limit", type=int, default=None)
    ap.add_argument("--families-per-area", type=int, default=10)
    ap.add_argument("--max-tokens", type=int, default=3500)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--skip-mutator-code", action="store_true", help="Only generate family JSONL and skip per-family mutator code generation.")
    return ap.parse_args()


def load_settings(path: Path) -> Settings:
    payload = json.loads(path.read_text(encoding="utf-8"))
    token = (
        os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
        or os.environ.get("AWS_BEDROCK_API_KEY")
        or os.environ.get("BEDROCK_API_KEY")
        or payload.get("aws_bearer_token_bedrock")
        or ""
    ).strip()
    region = str(payload.get("region") or "us-east-1").strip()
    model_id = str(payload.get("model_id") or "").strip()
    model_label = str(payload.get("model_label") or "Claude Opus 4.6").strip()
    if not token:
        raise SystemExit(f"Missing aws_bearer_token_bedrock in {path}")
    if not model_id or model_id.startswith("REPLACE_WITH_"):
        raise SystemExit(f"Set the exact Bedrock model_id for {model_label} in {path}")
    return Settings(token=token, region=region, model_id=model_id, model_label=model_label)


def auth_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def runtime_endpoint(region: str) -> str:
    return f"https://bedrock-runtime.{region}.amazonaws.com"


def invoke_endpoint(region: str) -> str:
    return f"https://bedrock-runtime.{region}.amazonaws.com/model"


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def extract_python_code(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip() + "\n"
    return cleaned + ("\n" if not cleaned.endswith("\n") else "")


def extract_json_array(text: str) -> list[dict[str, Any]]:
    cleaned = strip_code_fences(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise
        payload = json.loads(cleaned[start : end + 1])
    if not isinstance(payload, list):
        raise ValueError("model response was not a JSON array")
    rows: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def converse(settings: Settings, prompt: str, max_tokens: int, temperature: float, timeout_s: int) -> str:
    model_id = quote(settings.model_id, safe="")
    url = f"{runtime_endpoint(settings.region)}/model/{model_id}/converse"
    body = {
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {
            "maxTokens": max_tokens,
            "temperature": temperature,
        },
    }
    last_error = "unknown error"
    for attempt in range(4):
        try:
            response = requests.post(
                url,
                headers=auth_headers(settings.token),
                json=body,
                timeout=(20, timeout_s),
            )
            if response.ok:
                data = response.json()
                try:
                    return data["output"]["message"]["content"][0]["text"]
                except (KeyError, IndexError, TypeError):
                    pass
                # Some inference-profile routes only support InvokeModel, so fall back below.
                break
            last_error = f"HTTP {response.status_code}: {response.text[:400]}"
            if response.status_code == 429:
                time.sleep((2 ** attempt) * 5)
                continue
            if response.status_code in {400, 401, 403, 404, 422}:
                break
            time.sleep(2 ** attempt)
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(2 ** attempt)

    invoke_url = f"{invoke_endpoint(settings.region)}/{model_id}/invoke"
    invoke_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
    }
    last_invoke_error = last_error
    for attempt in range(4):
        try:
            response = requests.post(
                invoke_url,
                headers=auth_headers(settings.token),
                json=invoke_body,
                timeout=(20, timeout_s),
            )
            if response.ok:
                data = response.json()
                if isinstance(data, dict):
                    if "content" in data and isinstance(data["content"], list):
                        for item in data["content"]:
                            if isinstance(item, dict) and isinstance(item.get("text"), str):
                                return item["text"]
                    if isinstance(data.get("outputText"), str):
                        return data["outputText"]
                last_invoke_error = f"unexpected invoke payload: {response.text[:400]}"
                break
            last_invoke_error = f"HTTP {response.status_code}: {response.text[:400]}"
            if response.status_code == 429:
                time.sleep((2 ** attempt) * 5)
                continue
            if response.status_code in {400, 401, 403, 404, 422}:
                break
            time.sleep(2 ** attempt)
        except requests.RequestException as exc:
            last_invoke_error = str(exc)
            time.sleep(2 ** attempt)
    raise RuntimeError(last_invoke_error)


def probe(settings: Settings, timeout_s: int) -> None:
    reply = converse(settings, "Reply with exactly the word ok.", max_tokens=8, temperature=0.0, timeout_s=timeout_s)
    print(reply.strip())


def build_prompt(area_name: str, area_focus: str, families_per_area: int) -> str:
    return (
        "You are generating compile-time bug family definitions for AMD/Xilinx Versal AIE and ADF code.\n\n"
        f"Target area: {area_name}\n"
        f"Area focus: {area_focus}\n\n"
        f"Return exactly {families_per_area} distinct bug families as a JSON array only.\n"
        "Do not wrap the response in markdown fences.\n"
        "Each object must contain exactly these keys:\n"
        "- bug_type\n"
        "- category\n"
        "- target_files\n"
        "- artifact_handling\n"
        "- match_targets\n"
        "- mutation_strategy\n"
        "- repair_expectation\n"
        "- validation_signal\n"
        "- tags\n\n"
        "Constraints:\n"
        "- These are mutation families, not one-off examples.\n"
        "- Every bug family must be realistic for AIE kernels, ADF graphs, PLIO/GMIO, RTP, vector intrinsics, accumulators, or architecture-specific APIs.\n"
        "- Explicitly cover prior bug themes where relevant: acc48 vs acc80, accumulator initialization with aie::zeros vs invalid broadcast, invalid shuffle_up/shuffle_down usage, readincr_v or writeincr_v lane mismatches, wrong kernel::create targets, wrong adf::source paths, connect template mismatches, and PLIO or GMIO direction bugs.\n"
        "- Prefer compile-time failures over runtime failures.\n"
        "- bug_type must be snake_case and unique within this area.\n"
        f"- category must be exactly '{area_name}'.\n"
        "- target_files must be a JSON array of concrete project file classes such as graph header, graph source, kernel source, kernel header, shared utility header, or data/config path literal inside a graph file.\n"
        "- artifact_handling must be one of: modify_existing_file, reference_missing_file, either.\n"
        "- match_targets must be a JSON array of concrete code patterns, APIs, or symbols to look for.\n"
        "- mutation_strategy must describe how to inject the bug into real code.\n"
        "- repair_expectation must describe the minimal intended fix.\n"
        "- validation_signal must mention that WSL Vitis/AIE compile failure is required.\n"
        "- tags must be a JSON array of short lowercase strings.\n"
        "- Avoid duplicates, vague families, or trivial spelling-only mutations unless the spelling error is specific to a real AIE/ADF API.\n"
        "- target_files should name the files you would actually edit in a small AIE mini-project to create the error.\n"
    )


def build_mutator_prompt(family: dict[str, Any]) -> str:
    family_json = json.dumps(family, indent=2, ensure_ascii=False)
    return (
        "You are writing a single Python mutator module for one AMD/Xilinx Versal AIE bug family.\n\n"
        "Return only raw Python code. Do not use markdown fences.\n"
        "Use only the Python standard library.\n"
        "The module must compile on Python 3.13.\n"
        "The module must define exactly these top-level items:\n"
        "- BUG_FAMILY (dict)\n"
        "- find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]\n"
        "- apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]\n\n"
        "Rules:\n"
        "- project_files maps relative file paths to full file contents.\n"
        "- Do not mutate files outside the listed target_files intent.\n"
        "- Candidates must be deterministic and concrete, with these keys: file_path, bug_type, category, start, end, original, replacement, description.\n"
        "- apply_mutation must return a new mutated copy of project_files and must not mutate the input dict in place.\n"
        "- If artifact_handling is reference_missing_file, the mutation should still happen by editing an existing file's path literal or include string, not by creating a new file.\n"
        "- Prefer regex or exact-string matching anchored on the family's match_targets and mutation_strategy.\n"
        "- If no valid mutation site exists, find_mutation_candidates should return an empty list.\n"
        "- Add only short comments where they truly help.\n\n"
        "Bug family definition:\n"
        f"{family_json}\n"
    )


def infer_target_files(raw: dict[str, Any], area_name: str) -> list[str]:
    values = [str(value).strip() for value in (raw.get("target_files") or []) if str(value).strip()]
    if values:
        return values

    haystack = " ".join(
        [
            area_name,
            str(raw.get("mutation_strategy") or ""),
            str(raw.get("repair_expectation") or ""),
            " ".join(str(value) for value in (raw.get("match_targets") or [])),
        ]
    ).lower()
    targets: list[str] = []
    if any(token in haystack for token in ["graph", "connect", "plio", "gmio", "source(k)", "kernel::create", "runtime<ratio>"]):
        targets.append("graph header")
    if any(token in haystack for token in ["main()", "graph.cpp", "graph source"]):
        targets.append("graph source")
    if any(token in haystack for token in ["aie::", "readincr", "writeincr", "kernel source", "input_stream", "output_stream", "window", "buffer", "accum"]):
        targets.append("kernel source")
    if any(token in haystack for token in ["prototype", "declaration", "kernel header", "shared header"]):
        targets.append("kernel header")
    if any(token in haystack for token in ["include guard", "macro", "header guard", "utility header"]):
        targets.append("shared utility header")
    if any(token in haystack for token in ["filename", "path", "source path", "missing file", "non-existent"]):
        targets.append("path literal inside an existing project file")
    return targets or ["kernel source"]


def infer_artifact_handling(raw: dict[str, Any]) -> str:
    value = str(raw.get("artifact_handling") or "").strip().lower()
    if value in {"modify_existing_file", "reference_missing_file", "either"}:
        return value

    haystack = " ".join(
        [
            str(raw.get("mutation_strategy") or ""),
            str(raw.get("repair_expectation") or ""),
            " ".join(str(value) for value in (raw.get("match_targets") or [])),
        ]
    ).lower()
    if any(token in haystack for token in ["non-existent", "missing file", "missing header", "rename a path", "wrong path", "missing path"]):
        return "reference_missing_file"
    if any(token in haystack for token in ["alternatively", "or remove", "or replace"]):
        return "either"
    return "modify_existing_file"


def normalize_family(raw: dict[str, Any], area_name: str) -> dict[str, Any]:
    bug_type = str(raw.get("bug_type") or "").strip().lower()
    bug_type = re.sub(r"[^a-z0-9_]+", "_", bug_type).strip("_")
    if not bug_type:
        raise ValueError("missing bug_type")
    match_targets = [str(value).strip() for value in (raw.get("match_targets") or []) if str(value).strip()]
    tags = [re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower()).strip("_") for value in (raw.get("tags") or [])]
    tags = [tag for tag in tags if tag]
    validation_signal = str(raw.get("validation_signal") or "").strip()
    if "wsl" not in validation_signal.lower() or "compile" not in validation_signal.lower():
        validation_signal = "The mutated project must fail WSL Vitis/AIE compilation before it can become a dataset row."
    return {
        "bug_type": bug_type,
        "category": area_name,
        "target_files": infer_target_files(raw, area_name),
        "artifact_handling": infer_artifact_handling(raw),
        "match_targets": match_targets,
        "mutation_strategy": str(raw.get("mutation_strategy") or "").strip(),
        "repair_expectation": str(raw.get("repair_expectation") or "").strip(),
        "validation_signal": validation_signal,
        "tags": sorted(set(tags + [area_name])),
    }


def load_families_jsonl(path: Path) -> list[dict[str, Any]]:
    families: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            families.append(json.loads(line))
    return families


def uniquify_bug_type(bug_type: str, area_name: str, seen_bug_types: set[str]) -> str:
    if bug_type not in seen_bug_types:
        return bug_type

    area_prefix = f"{area_name}__{bug_type}"
    if area_prefix not in seen_bug_types:
        return area_prefix

    suffix = 2
    while True:
        candidate = f"{area_prefix}__{suffix}"
        if candidate not in seen_bug_types:
            return candidate
        suffix += 1


def generate_families(args: argparse.Namespace, settings: Settings) -> list[dict[str, Any]]:
    areas = AREA_SPECS[: args.areas_limit] if args.areas_limit is not None else AREA_SPECS
    families: list[dict[str, Any]] = []
    seen_bug_types: set[str] = set()
    total_areas = len(areas)
    for index, (area_name, area_focus) in enumerate(areas, start=1):
        print(f"[bug-families] [{index}/{total_areas}] generating {area_name}...", flush=True)
        prompt = build_prompt(area_name, area_focus, args.families_per_area)
        response_text = converse(settings, prompt, max_tokens=args.max_tokens, temperature=args.temperature, timeout_s=args.timeout)
        rows = extract_json_array(response_text)
        if len(rows) != args.families_per_area:
            raise RuntimeError(
                f"Area {area_name} returned {len(rows)} families, expected {args.families_per_area}"
            )
        for raw in rows:
            family = normalize_family(raw, area_name)
            bug_type = uniquify_bug_type(family["bug_type"], area_name, seen_bug_types)
            family["bug_type"] = bug_type
            if not family["match_targets"]:
                raise RuntimeError(f"Family {bug_type} has no match_targets")
            if not family["mutation_strategy"] or not family["repair_expectation"]:
                raise RuntimeError(f"Family {bug_type} is missing required text fields")
            seen_bug_types.add(bug_type)
            families.append(family)
        print(
            f"[bug-families] [{index}/{total_areas}] completed {area_name} -> {len(rows)} families",
            flush=True,
        )
    for index, family in enumerate(families, start=1):
        family["family_id"] = f"BF{index:03d}"
        ordered = {
            "family_id": family["family_id"],
            "bug_type": family["bug_type"],
            "category": family["category"],
            "target_files": family["target_files"],
            "artifact_handling": family["artifact_handling"],
            "match_targets": family["match_targets"],
            "mutation_strategy": family["mutation_strategy"],
            "repair_expectation": family["repair_expectation"],
            "validation_signal": family["validation_signal"],
            "tags": family["tags"],
        }
        families[index - 1] = ordered
    return families


def write_outputs(out_path: Path, summary_path: Path, settings: Settings, families: list[dict[str, Any]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for family in families:
            handle.write(json.dumps(family, ensure_ascii=False) + "\n")
    summary = {
        "model_id": settings.model_id,
        "model_label": settings.model_label,
        "total_families": len(families),
        "areas": sorted({family["category"] for family in families}),
        "first_family_id": families[0]["family_id"] if families else None,
        "last_family_id": families[-1]["family_id"] if families else None,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def generate_mutator_modules(
    args: argparse.Namespace,
    settings: Settings,
    families: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    mutator_dir = Path(args.mutator_dir)
    mutator_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, Any]] = []
    total = len(families)
    for index, family in enumerate(families, start=1):
        bug_type = str(family.get("bug_type") or f"family_{index:03d}")
        print(f"[bug-families] [mutator {index}/{total}] generating {bug_type}...", flush=True)
        prompt = build_mutator_prompt(family)
        code_text = converse(
            settings,
            prompt,
            max_tokens=max(args.max_tokens, 4500),
            temperature=min(args.temperature, 0.1),
            timeout_s=args.timeout,
        )
        module_code = extract_python_code(code_text)
        module_path = mutator_dir / f"{bug_type}.py"
        module_path.write_text(module_code, encoding="utf-8")
        manifest_rows.append(
            {
                "family_id": family.get("family_id"),
                "bug_type": bug_type,
                "module_path": str(module_path),
            }
        )
        print(f"[bug-families] [mutator {index}/{total}] wrote {module_path.name}", flush=True)

    manifest_path = mutator_dir / "mutator_manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in manifest_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return manifest_rows


def main() -> int:
    args = parse_args()
    settings = load_settings(Path(args.secrets))
    if args.probe:
        probe(settings, args.timeout)
        return 0
    if args.families_in:
        families = load_families_jsonl(Path(args.families_in))
    else:
        families = generate_families(args, settings)
        write_outputs(Path(args.out), Path(args.summary_out), settings, families)

    mutator_rows: list[dict[str, Any]] = []
    if not args.skip_mutator_code:
        mutator_rows = generate_mutator_modules(args, settings, families)

    print(f"[bug-families] model      : {settings.model_label}")
    print(f"[bug-families] model_id   : {settings.model_id}")
    print(f"[bug-families] families   : {len(families)}")
    if not args.families_in:
        print(f"[bug-families] output     : {args.out}")
        print(f"[bug-families] summary    : {args.summary_out}")
    if not args.skip_mutator_code:
        print(f"[bug-families] mutators   : {len(mutator_rows)}")
        print(f"[bug-families] mutatordir : {args.mutator_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())