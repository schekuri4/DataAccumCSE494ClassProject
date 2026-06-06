import re
import copy

BUG_FAMILY = {
    "family_id": "BF072",
    "bug_type": "plio_width_enum_invalid_value",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["plio_32_bits", "plio_64_bits", "plio_128_bits"],
    "mutation_strategy": "Replace a valid PLIO width enum (e.g., adf::plio_128_bits) with an invalid or nonexistent enum value such as adf::plio_256_bits, adf::plio_16_bits, or a raw integer literal where an enum is expected.",
    "repair_expectation": "Replace the invalid width enum with a valid one: plio_32_bits, plio_64_bits, or plio_128_bits as appropriate for the design.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about undeclared identifier or no matching constructor for PLIO width parameter.",
    "tags": ["compile_error", "invalid_value", "plio", "plio_ports", "width_enum"]
}

# Invalid replacements to use when mutating valid PLIO width enums
_INVALID_REPLACEMENTS = [
    "adf::plio_256_bits",
    "adf::plio_16_bits",
    "42",
]

# Pattern matches optional adf:: prefix followed by valid plio width enum values
_PLIO_WIDTH_PATTERN = re.compile(
    r'(adf\s*::\s*)?(plio_32_bits|plio_64_bits|plio_128_bits)'
)


def _is_graph_file(filepath):
    """Heuristic to identify graph header or graph source files."""
    lower = filepath.lower()
    # Common patterns for AIE graph files
    if any(ext in lower for ext in ['.h', '.hpp', '.cpp', '.cc']):
        if 'graph' in lower:
            return True
        # Also consider any header/source that might contain PLIO declarations
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        for match in _PLIO_WIDTH_PATTERN.finditer(content):
            original = match.group(0)
            start = match.start()
            end = match.end()

            # Determine the enum value matched
            enum_value = match.group(2)  # e.g., plio_128_bits

            # Choose an invalid replacement that differs from the original
            # Use plio_256_bits as primary invalid replacement
            if "128" in enum_value:
                replacement = "adf::plio_256_bits"
            elif "64" in enum_value:
                replacement = "adf::plio_16_bits"
            elif "32" in enum_value:
                replacement = "adf::plio_256_bits"
            else:
                replacement = "adf::plio_256_bits"

            candidate = {
                "file_path": file_path,
                "bug_type": "plio_width_enum_invalid_value",
                "category": "plio_ports",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Replace valid PLIO width enum '{original}' with invalid "
                    f"enum value '{replacement}' in {file_path} at position {start}."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    # Create a new copy of project_files
    mutated_files = dict(project_files)

    file_path = candidate["file_path"]
    content = mutated_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        mutated_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        mutated_content = content.replace(original, replacement, 1)

    mutated_files[file_path] = mutated_content
    return mutated_files
