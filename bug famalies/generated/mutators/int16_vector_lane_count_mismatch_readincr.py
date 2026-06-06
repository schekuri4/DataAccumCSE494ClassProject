import re
import copy

BUG_FAMILY = {
    "family_id": "BF201",
    "bug_type": "int16_vector_lane_count_mismatch_readincr",
    "category": "vector_lane_widths",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v<16>",
        "readincr_v<32>",
        "aie::vector<int16,16>",
        "aie::vector<int16,32>"
    ],
    "mutation_strategy": "Change the lane count template parameter of readincr_v for int16 streams from a valid width (e.g., 16 or 32) to an invalid non-power-of-two value (e.g., 12, 24, or 48) that does not match the 256-bit or 512-bit vector register widths supported by AIE for int16.",
    "repair_expectation": "Restore the lane count to a valid power-of-two value that matches the AIE vector register width for int16 (8, 16, or 32 depending on architecture).",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or static_assert about unsupported vector size for readincr_v.",
    "tags": [
        "int16",
        "lane_count",
        "non_power_of_two",
        "readincr_v",
        "stream",
        "vector_lane_widths"
    ]
}

# Mapping from valid lane counts to invalid non-power-of-two replacements
_INVALID_REPLACEMENTS = {
    "16": "12",
    "32": "24",
    "8": "12",
}


def _is_kernel_source(file_path):
    """Heuristic to identify kernel source files."""
    extensions = ('.cpp', '.cc', '.cxx', '.c', '.h', '.hpp', '.hxx')
    return file_path.lower().endswith(extensions)


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match readincr_v<N> where N is a valid lane count for int16
    readincr_pattern = re.compile(r'readincr_v\s*<\s*(8|16|32)\s*>')

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file likely deals with int16 streams (heuristic: contains int16 reference or readincr_v)
        # We look for readincr_v with valid lane counts
        for match in readincr_pattern.finditer(content):
            lane_count = match.group(1)
            # Verify this is in an int16 context by checking nearby lines for int16 references
            # Get surrounding context (200 chars before and after)
            context_start = max(0, match.start() - 300)
            context_end = min(len(content), match.end() + 300)
            context = content[context_start:context_end]

            # Check if int16 is mentioned in context or if we should just mutate any readincr_v
            # For robustness, we check for int16 in the surrounding context
            has_int16_context = bool(re.search(r'int16', context))

            if not has_int16_context:
                # Also accept if the file broadly uses int16
                has_int16_context = bool(re.search(r'int16', content))

            if not has_int16_context:
                continue

            original = match.group(0)
            invalid_lane = _INVALID_REPLACEMENTS.get(lane_count, "12")
            # Reconstruct replacement preserving whitespace
            replacement = re.sub(r'<\s*' + lane_count + r'\s*>', '<' + invalid_lane + '>', original)

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Change readincr_v lane count from {lane_count} to {invalid_lane} "
                    f"(invalid non-power-of-two for int16 AIE vector register width) "
                    f"in {file_path}"
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] != original:
        # Fallback: find first occurrence
        idx = content.find(original)
        if idx == -1:
            return new_project_files
        start = idx
        end = idx + len(original)

    new_content = content[:start] + candidate["replacement"] + content[end:]
    new_project_files[file_path] = new_content

    return new_project_files
