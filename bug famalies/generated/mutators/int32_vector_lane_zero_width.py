import re
import copy

BUG_FAMILY = {
    "family_id": "BF202",
    "bug_type": "int32_vector_lane_zero_width",
    "category": "vector_lane_widths",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::vector<int32,8>",
        "aie::vector<int32,16>",
        "aie::vector<int32,4>"
    ],
    "mutation_strategy": "Replace the lane count in an aie::vector<int32,N> declaration with 0, creating aie::vector<int32,0>. This produces a zero-width vector which is invalid and cannot be instantiated.",
    "repair_expectation": "Restore the lane count to a valid non-zero power-of-two value (4, 8, or 16) appropriate for int32 on AIE.",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert or template error indicating zero-size vector is not supported.",
    "tags": [
        "compile_error",
        "int32",
        "vector_declaration",
        "vector_lane_widths",
        "zero_width"
    ]
}

# Pattern matches aie::vector<int32, N> where N is 4, 8, or 16
# Allows optional whitespace around the comma and before >
_PATTERN = re.compile(
    r'aie::vector<\s*int32\s*,\s*(4|8|16)\s*>'
)


def _is_kernel_source(file_path: str) -> bool:
    """Heuristic to identify kernel source files."""
    kernel_extensions = ('.cpp', '.cc', '.cxx', '.c', '.h', '.hpp', '.hxx')
    return file_path.lower().endswith(kernel_extensions)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []
    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue
        for match in _PATTERN.finditer(content):
            original_text = match.group(0)
            lane_count = match.group(1)
            # Build the replacement: replace the lane count with 0
            replacement_text = re.sub(
                r'(aie::vector<\s*int32\s*,\s*)' + lane_count + r'(\s*>)',
                r'\g<1>0\2',
                original_text
            )
            start = match.start()
            end = match.end()
            candidates.append({
                "file_path": file_path,
                "bug_type": "int32_vector_lane_zero_width",
                "category": "vector_lane_widths",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Replace lane count {lane_count} with 0 in "
                    f"'{original_text}' at offset {start}, creating an invalid "
                    f"zero-width vector declaration."
                )
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

    # Verify the original text is at the expected position
    if content[start:end] != original:
        raise ValueError(
            f"Expected '{original}' at positions {start}:{end} in {file_path}, "
            f"but found '{content[start:end]}'"
        )

    # Apply the mutation
    new_content = content[:start] + replacement + content[end:]
    new_files[file_path] = new_content
    return new_files
