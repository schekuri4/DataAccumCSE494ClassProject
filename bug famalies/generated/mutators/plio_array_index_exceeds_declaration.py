import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF062",
    "bug_type": "plio_array_index_exceeds_declaration",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_plio plin[",
        "output_plio plout[",
        "adf::input_plio",
        "adf::output_plio"
    ],
    "mutation_strategy": "Declare a PLIO array (e.g., input_plio plin[2]) but connect or initialize an index beyond the declared size (e.g., plin[2] = input_plio::create(...) when only indices 0 and 1 are valid).",
    "repair_expectation": "Reduce the index to be within the valid range or increase the PLIO array declaration size.",
    "validation_signal": "WSL Vitis/AIE compile failure with out-of-bounds access error or static assertion failure during graph compilation.",
    "tags": [
        "array_index",
        "graph_endpoint_indices",
        "input_plio",
        "out_of_range",
        "output_plio",
        "plio"
    ]
}


def _is_graph_file(filepath: str) -> bool:
    """Check if file is likely a graph header or source."""
    lower = filepath.lower()
    # Accept .h, .hpp, .cpp, .cc files that might be graph files
    extensions = ('.h', '.hpp', '.cpp', '.cc', '.c')
    return any(lower.endswith(ext) for ext in extensions)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to find PLIO array declarations with a numeric size
    # Matches: input_plio varname[N] or adf::input_plio varname[N] etc.
    decl_pattern = re.compile(
        r'(?:adf::)?(input_plio|output_plio)\s+(\w+)\s*\[\s*(\d+)\s*\]'
    )

    # Pattern to find usage of PLIO array with an index: varname[index]
    usage_pattern_template = r'{name}\s*\[\s*(\d+)\s*\]'

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Check if file contains any PLIO-related content
        has_plio = any(mt in content for mt in BUG_FAMILY["match_targets"])
        if not has_plio:
            continue

        # Find all PLIO array declarations in this file
        declarations: dict[str, int] = {}
        for m in decl_pattern.finditer(content):
            var_name = m.group(2)
            array_size = int(m.group(3))
            declarations[var_name] = array_size

        if not declarations:
            continue

        # For each declared array, find usages where we can mutate the index
        # to exceed the declared size
        for var_name, array_size in declarations.items():
            # Find all usages of this array with an index
            usage_re = re.compile(r'(' + re.escape(var_name) + r')\s*\[\s*(\d+)\s*\]')

            for m in usage_re.finditer(content):
                used_index = int(m.group(2))
                # Only mutate valid accesses (index < array_size)
                # We'll change the max valid index to array_size (one beyond valid)
                if used_index < array_size:
                    # Find the highest valid index usage to mutate
                    # Prefer mutating the highest index as it's closest to boundary
                    original_text = m.group(0)
                    # Replace the index with array_size (out of bounds)
                    new_index = array_size
                    replacement_text = re.sub(
                        r'\[\s*' + str(used_index) + r'\s*\]',
                        '[' + str(new_index) + ']',
                        original_text
                    )

                    start = m.start()
                    end = m.end()

                    candidates.append({
                        "file_path": filepath,
                        "bug_type": "plio_array_index_exceeds_declaration",
                        "category": "graph_endpoint_indices",
                        "start": start,
                        "end": end,
                        "original": original_text,
                        "replacement": replacement_text,
                        "description": (
                            f"Changed {var_name}[{used_index}] to {var_name}[{new_index}], "
                            f"exceeding declared array size of {array_size}."
                        )
                    })

    # Deduplicate: prefer highest index mutations (most likely to be the last valid one)
    # Sort by file_path and start position for determinism
    candidates.sort(key=lambda c: (c["file_path"], c["start"]))

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    content = new_files[filepath]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[filepath] = new_content
    return new_files
