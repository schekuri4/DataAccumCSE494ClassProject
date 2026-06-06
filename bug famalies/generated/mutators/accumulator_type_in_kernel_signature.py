import re
import copy

BUG_FAMILY = {
    "family_id": "BF038",
    "bug_type": "accumulator_type_in_kernel_signature",
    "category": "kernel_prototypes_and_signatures",
    "target_files": ["kernel header", "kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "acc48",
        "acc80",
        "aie::accum<acc48",
        "aie::accum<acc80",
        "cacc48",
        "cacc80"
    ],
    "mutation_strategy": "Change an accumulator type used in a kernel's internal computation or cascading interface from acc48 to acc80 (or vice versa) without updating the cascade stream port type or the MAC intrinsic that produces/consumes it, creating a bitwidth mismatch in the kernel signature or cascade connection.",
    "repair_expectation": "Use the correct accumulator precision (acc48 vs acc80) that matches the cascade port width and the intrinsic return type.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch between accumulator and cascade stream or incompatible intrinsic operand.",
    "tags": [
        "acc48",
        "acc80",
        "accumulator",
        "cascade_port",
        "kernel_prototypes_and_signatures"
    ]
}

# Patterns to match accumulator types and their swaps
_SWAP_MAP = {
    "acc48": "acc80",
    "acc80": "acc48",
    "cacc48": "cacc80",
    "cacc80": "cacc48",
}

# File extensions that are likely kernel headers or sources
_KERNEL_EXTENSIONS = ('.h', '.hpp', '.hxx', '.cc', '.cpp', '.cxx', '.c')

# Regex that matches accumulator type occurrences
# Matches: aie::accum<acc48, aie::accum<acc80, acc48, acc80, cacc48, cacc80
_ACC_PATTERN = re.compile(
    r'(aie::accum<\s*)(acc48|acc80)|(cacc48|cacc80)|\b(acc48|acc80)\b'
)


def _is_kernel_file(path: str) -> bool:
    """Heuristic: file looks like a kernel header or source."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in _KERNEL_EXTENSIONS)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path):
            continue

        for match in _ACC_PATTERN.finditer(content):
            # Determine which group matched
            if match.group(2):
                # aie::accum<accXX pattern
                # We mutate only the accXX part (group 2)
                original = match.group(2)
                replacement = _SWAP_MAP[original]
                start = match.start(2)
                end = match.end(2)
                full_original = match.group(0)
                full_replacement = match.group(1) + replacement
            elif match.group(3):
                # cacc48 or cacc80
                original = match.group(3)
                replacement = _SWAP_MAP[original]
                start = match.start(3)
                end = match.end(3)
                full_original = original
                full_replacement = replacement
            elif match.group(4):
                # standalone acc48 or acc80
                original = match.group(4)
                replacement = _SWAP_MAP[original]
                start = match.start(4)
                end = match.end(4)
                full_original = original
                full_replacement = replacement
            else:
                continue

            description = (
                f"Change accumulator type '{full_original}' to "
                f"'{full_replacement}' at offset {start} in {file_path}, "
                f"creating a bitwidth mismatch in the kernel signature or cascade connection."
            )

            candidates.append({
                "file_path": file_path,
                "bug_type": "accumulator_type_in_kernel_signature",
                "category": "kernel_prototypes_and_signatures",
                "start": start,
                "end": end,
                "original": content[start:end],
                "replacement": replacement,
                "description": description,
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected location
    if content[start:end] != original:
        # Fallback: try to find first occurrence
        idx = content.find(original)
        if idx == -1:
            return new_files  # Cannot apply mutation
        start = idx
        end = idx + len(original)

    new_content = content[:start] + replacement + content[end:]
    new_files[file_path] = new_content

    return new_files
