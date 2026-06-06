import re
import copy

BUG_FAMILY = {
    "family_id": "BF116",
    "bug_type": "vector_stream_width_plio_mismatch",
    "category": "stream_vector_interfaces",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::PLIO",
        "plio_32_bits",
        "plio_64_bits",
        "plio_128_bits",
        "input_plio",
        "output_plio"
    ],
    "mutation_strategy": "Change the PLIO bit width declaration (e.g., plio_128_bits to plio_32_bits) without updating the kernel's readincr_v/writeincr_v lane count, creating an inconsistency between the declared PLIO width and the vector size used in the kernel.",
    "repair_expectation": "Align the PLIO width declaration with the kernel's vector access pattern, or adjust the kernel's readincr_v<N> lane count to match the new PLIO width.",
    "validation_signal": "WSL Vitis/AIE compile failure or linker error indicating stream width mismatch between PLIO declaration and kernel port expectations.",
    "tags": [
        "graph_kernel_mismatch",
        "plio_width",
        "stream_vector",
        "stream_vector_interfaces"
    ]
}

# The three valid PLIO width identifiers
PLIO_WIDTHS = ["plio_32_bits", "plio_64_bits", "plio_128_bits"]

# Pattern to match PLIO width declarations in various contexts
PLIO_WIDTH_PATTERN = re.compile(r'\b(plio_32_bits|plio_64_bits|plio_128_bits)\b')


def _is_graph_file(filepath):
    """Heuristic to determine if a file is a graph header or graph source."""
    lower = filepath.lower()
    # Check for common graph file patterns
    if 'graph' in lower:
        return True
    # Also consider .h/.hpp/.cpp files that might contain PLIO declarations
    extensions = ('.h', '.hpp', '.cpp', '.cc', '.cxx')
    return lower.endswith(extensions)


def _get_replacement_width(original_width):
    """Return a different PLIO width to create a mismatch.
    Strategy: downgrade width to create mismatch with kernel vector access."""
    if original_width == "plio_128_bits":
        return "plio_32_bits"
    elif original_width == "plio_64_bits":
        return "plio_32_bits"
    elif original_width == "plio_32_bits":
        return "plio_128_bits"
    return "plio_32_bits"


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Search for PLIO width declarations
        for match in PLIO_WIDTH_PATTERN.finditer(content):
            original_width = match.group(1)
            replacement_width = _get_replacement_width(original_width)

            start = match.start()
            end = match.end()

            # Determine description based on context
            # Check if it's in an input_plio or output_plio context
            line_start = content.rfind('\n', 0, start) + 1
            line_end = content.find('\n', end)
            if line_end == -1:
                line_end = len(content)
            line_content = content[line_start:line_end]

            context = "PLIO"
            if "input_plio" in line_content or "in" in line_content.lower():
                context = "input PLIO"
            elif "output_plio" in line_content or "out" in line_content.lower():
                context = "output PLIO"

            candidate = {
                "file_path": filepath,
                "bug_type": "vector_stream_width_plio_mismatch",
                "category": "stream_vector_interfaces",
                "start": start,
                "end": end,
                "original": original_width,
                "replacement": replacement_width,
                "description": (
                    f"Changed {context} width from {original_width} to "
                    f"{replacement_width} to create mismatch with kernel's "
                    f"vector stream access pattern."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)

    filepath = candidate["file_path"]
    content = new_project_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_project_files[filepath] = new_content
    return new_project_files
