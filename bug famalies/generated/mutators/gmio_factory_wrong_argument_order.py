import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF087",
    "bug_type": "gmio_factory_wrong_argument_order",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["gmio::create", "adf::GMIO::create"],
    "mutation_strategy": "Swap the order of the string name, burst_length, and bandwidth arguments in the gmio::create factory call so that a numeric value appears where a string is expected or vice versa, causing a compile-time type error.",
    "repair_expectation": "Restore the correct argument order: name (string), burst_length (int), bandwidth (int) in the gmio::create call.",
    "validation_signal": "WSL Vitis/AIE compile failure with type conversion error or no matching function for gmio::create.",
    "tags": ["argument_order", "compile_error", "factory", "gmio", "gmio_ports"]
}


def _is_graph_header(path):
    """Heuristic: graph headers are .h or .hpp files, often containing 'graph' in name."""
    lower = path.lower()
    if lower.endswith('.h') or lower.endswith('.hpp') or lower.endswith('.hxx'):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    # Pattern matches gmio::create(...) or adf::GMIO::create(...)
    # Arguments: name (string literal or variable), burst_length (numeric/variable), bandwidth (numeric/variable)
    # We capture the full call to parse arguments
    pattern = re.compile(
        r'((?:adf::)?(?:GMIO|gmio)::create)\s*\(\s*'
        r'([^,]+?)\s*,\s*'   # arg1 (name - typically string)
        r'([^,]+?)\s*,\s*'   # arg2 (burst_length - typically int)
        r'([^)]+?)\s*\)'      # arg3 (bandwidth - typically int)
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        for match in pattern.finditer(content):
            factory_call = match.group(1)
            arg1 = match.group(2).strip()
            arg2 = match.group(3).strip()
            arg3 = match.group(4).strip()

            original_text = match.group(0)
            start = match.start()
            end = match.end()

            # Determine if arg1 looks like a string (quoted or string variable)
            # and arg2/arg3 look numeric. We swap arg1 and arg2 to cause type error.
            # Mutation: swap name with burst_length -> (burst_length, name, bandwidth)
            mutated_text = f"{factory_call}({arg2}, {arg1}, {arg3})"

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": mutated_text,
                "description": (
                    f"Swapped the string name argument and burst_length numeric argument "
                    f"in {factory_call}() call, placing a numeric value where a string is "
                    f"expected, causing a compile-time type error."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
