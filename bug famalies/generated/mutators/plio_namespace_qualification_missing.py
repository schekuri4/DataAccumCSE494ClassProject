import re
import copy

BUG_FAMILY = {
    "family_id": "BF080",
    "bug_type": "plio_namespace_qualification_missing",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_plio",
        "adf::output_plio",
        "adf::plio_32_bits",
        "adf::plio_64_bits",
        "adf::plio_128_bits"
    ],
    "mutation_strategy": "Remove the adf:: namespace qualifier from PLIO-related types and enums (e.g., change adf::input_plio to input_plio, or adf::plio_128_bits to plio_128_bits) in a file that does not have 'using namespace adf;' declared, causing unresolved symbol errors.",
    "repair_expectation": "Add the adf:: namespace qualifier back to the PLIO types and enums, or add 'using namespace adf;' at the appropriate scope.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about undeclared identifier 'input_plio', 'output_plio', or 'plio_128_bits'.",
    "tags": ["namespace", "plio", "plio_ports", "qualification", "undeclared_identifier"]
}

# Targets to look for: the fully qualified names
_MATCH_TARGETS = [
    "adf::input_plio",
    "adf::output_plio",
    "adf::plio_32_bits",
    "adf::plio_64_bits",
    "adf::plio_128_bits"
]

# File extensions that qualify as graph header or graph source
_GRAPH_EXTENSIONS = ('.h', '.hpp', '.hxx', '.cpp', '.cc', '.cxx', '.c')


def _is_graph_file(filepath):
    """Heuristic: file is a graph header or source if it has relevant extension
    and 'graph' appears in the path/name (case-insensitive), or it contains
    adf graph-related content."""
    lower = filepath.lower()
    if not any(lower.endswith(ext) for ext in _GRAPH_EXTENSIONS):
        return False
    # Be permissive: any C++ header/source could be a graph file
    return True


def _has_using_namespace_adf(content):
    """Check if file has 'using namespace adf;' which would make unqualified names valid."""
    pattern = re.compile(r'\busing\s+namespace\s+adf\s*;')
    return pattern.search(content) is not None


def find_mutation_candidates(project_files):
    candidates = []

    # Build regex to find all match targets
    # We need to match "adf::" followed by the identifier, but not preceded by another ::
    # to avoid matching things like "some_ns::adf::input_plio"
    target_identifiers = {
        "adf::input_plio": "input_plio",
        "adf::output_plio": "output_plio",
        "adf::plio_32_bits": "plio_32_bits",
        "adf::plio_64_bits": "plio_64_bits",
        "adf::plio_128_bits": "plio_128_bits"
    }

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Only mutate files that do NOT have 'using namespace adf;'
        if _has_using_namespace_adf(content):
            continue

        for qualified, unqualified in target_identifiers.items():
            # Pattern: match "adf::<identifier>" not preceded by another "::" or alphanumeric
            # Use word boundary or lookbehind to avoid partial matches
            pattern = re.compile(
                r'(?<!:)(?<!\w)' + re.escape(qualified) + r'(?!\w)'
            )

            for match in pattern.finditer(content):
                start = match.start()
                end = match.end()
                original = match.group(0)
                replacement = unqualified

                candidates.append({
                    "file_path": filepath,
                    "bug_type": "plio_namespace_qualification_missing",
                    "category": "plio_ports",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Remove 'adf::' namespace qualifier from '{original}' "
                        f"changing it to '{replacement}' in '{filepath}', "
                        f"causing an undeclared identifier error."
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = new_files[filepath]

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

    new_files[filepath] = new_content
    return new_files
