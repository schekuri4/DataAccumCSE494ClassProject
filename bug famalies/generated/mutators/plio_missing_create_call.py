import re
import copy

BUG_FAMILY = {
    "family_id": "BF078",
    "bug_type": "plio_missing_create_call",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_plio::create",
        "output_plio::create",
        "= adf::input_plio",
        "= adf::output_plio"
    ],
    "mutation_strategy": "Remove or comment out the PLIO create() factory call while leaving the PLIO port declaration and its usage in connect<> statements, resulting in use of an uninitialized or default-constructed PLIO object.",
    "repair_expectation": "Restore the PLIO create() call with appropriate parameters (name, width, filename) before the port is used in connections.",
    "validation_signal": "WSL Vitis/AIE compile failure or linker error indicating uninitialized PLIO port or missing platform port configuration.",
    "tags": ["create", "missing_call", "plio", "plio_ports", "uninitialized"]
}


def _is_graph_file(filepath):
    """Heuristic to identify graph header or source files."""
    lower = filepath.lower()
    # Common patterns for graph files
    if 'graph' in lower:
        return True
    # Also check for .h/.hpp/.cpp files that might contain PLIO create calls
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match PLIO create() assignment statements
    # Matches lines like:
    #   varname = input_plio::create("name", plio_64_bits, "file.txt");
    #   varname = adf::input_plio::create("name", plio_64_bits, "file.txt");
    #   varname = output_plio::create(...);
    # Also handles cases where the assignment spans the full statement
    plio_create_pattern = re.compile(
        r'^([ \t]*)'                          # leading whitespace
        r'(\w[\w\.\->\[\]]*)'                 # variable name (lhs)
        r'\s*=\s*'                            # assignment operator
        r'((?:adf::)?(?:input_plio|output_plio)::create\s*\([^;]*\))\s*;' # create call
        r'([ \t]*(?://.*)?)?$',               # optional trailing comment
        re.MULTILINE
    )

    # Alternative pattern: standalone create call not in assignment context
    # e.g., in constructor initializer or direct call
    standalone_create_pattern = re.compile(
        r'^([ \t]*)'
        r'((?:adf::)?(?:input_plio|output_plio)::create\s*\([^;]*\))\s*;'
        r'([ \t]*(?://.*)?)?$',
        re.MULTILINE
    )

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Check if file contains any PLIO-related content
        has_plio = any(target in content for target in BUG_FAMILY["match_targets"])
        if not has_plio:
            continue

        # Find assignment-style create calls
        for match in plio_create_pattern.finditer(content):
            original_line = match.group(0)
            indent = match.group(1)
            var_name = match.group(2)
            create_call = match.group(3)

            start = match.start()
            end = match.end()

            # Comment out the entire line
            replacement = f"{indent}// {original_line.strip()}  // MUTATION: PLIO create removed"

            candidates.append({
                "file_path": filepath,
                "bug_type": "plio_missing_create_call",
                "category": "plio_ports",
                "start": start,
                "end": end,
                "original": original_line,
                "replacement": replacement,
                "description": (
                    f"Commented out PLIO create() call for '{var_name}' "
                    f"({create_call[:60]}...), leaving the port uninitialized."
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

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: find by exact string match
        idx = content.find(original)
        if idx == -1:
            # Cannot apply mutation, return unchanged
            return new_files
        new_content = content[:idx] + candidate["replacement"] + content[idx + len(original):]

    new_files[filepath] = new_content
    return new_files
