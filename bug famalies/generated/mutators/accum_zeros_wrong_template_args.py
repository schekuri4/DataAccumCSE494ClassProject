import re
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF233",
    "bug_type": "accum_zeros_wrong_template_args",
    "category": "accumulator_types",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::zeros<acc48",
        "aie::zeros<acc80",
        "aie::zeros<acc64",
        "aie::zeros<acc32",
        "aie::zeros<accfloat",
        "::aie::zeros<accfloat",
        "aie::accum",
    ],
    "mutation_strategy": (
        "Replace a valid accumulator zeros factory call with either a "
        "non-accumulator type or an invalid numeric lane count."
    ),
    "repair_expectation": (
        "Restore a valid accumulator tag such as acc48, acc80, accfloat, or "
        "cacc48 and a supported lane count."
    ),
    "validation_signal": (
        "WSL Vitis/AIE compile failure with template argument deduction "
        "failure or no matching function for aie::zeros."
    ),
    "tags": [
        "accum_factory",
        "accumulator_types",
        "aie_zeros",
        "initialization",
        "template_mismatch",
    ],
}

_ZEROS_PATTERN = re.compile(
    r'((?:::)?aie\s*::\s*zeros\s*<\s*)'
    r'(acc(?:32|48|64|80|float)|cacc(?:48|80)|accfloat)'
    r'(\s*,\s*)'
    r'([A-Za-z_][A-Za-z0-9_]*|\d+)'
    r'(\s*>\s*\(\s*\))'
)

_VALID_LANE_COUNTS = {4, 8, 16, 32}


def _is_kernel_source(file_path: str) -> bool:
    return file_path.lower().endswith((".cc", ".cpp", ".c", ".h", ".hpp", ".hxx", ".cxx"))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        for match in _ZEROS_PATTERN.finditer(content):
            prefix = match.group(1)
            acc_type = match.group(2)
            sep = match.group(3)
            lane_count_str = match.group(4)
            suffix = match.group(5)
            original = match.group(0)

            replacement = f"{prefix}int32{sep}{lane_count_str}{suffix}"
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Replace accumulator zeros type {acc_type} with int32, "
                    "which is not a valid accumulator tag for this factory."
                ),
            })

            if lane_count_str.isdigit():
                lane_count = int(lane_count_str)
                invalid_lane = lane_count - 1 if lane_count > 1 else 3
                if invalid_lane in _VALID_LANE_COUNTS:
                    invalid_lane = lane_count + 1
                replacement = f"{prefix}{acc_type}{sep}{invalid_lane}{suffix}"
                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Change accumulator zeros lane count from {lane_count} "
                        f"to unsupported value {invalid_lane}."
                    ),
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
