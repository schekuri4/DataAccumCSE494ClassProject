import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF244",
    "bug_type": "accum_from_vector_missing_shift",
    "category": "accumulator_initialization",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "from_vector<acc48>",
        "from_vector<acc80>",
        "aie::accum",
        ".from_vector("
    ],
    "mutation_strategy": "Remove the shift parameter from accum.from_vector(vec, shift) call or pass no argument where the API requires a shift value, causing a compile-time error due to missing required argument.",
    "repair_expectation": "Supply the required integer shift parameter to from_vector() specifying the number of bits to upshift the vector data into the accumulator.",
    "validation_signal": "WSL Vitis/AIE compile failure with too few arguments to function or no matching member function.",
    "tags": [
        "accumulator",
        "accumulator_initialization",
        "from_vector",
        "missing_argument",
        "shift"
    ]
}


def _is_kernel_source(file_path: str) -> bool:
    """Heuristic to identify kernel source files (C/C++ for AIE)."""
    extensions = ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx', '.cxx')
    return file_path.lower().endswith(extensions)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    # Pattern 1: method call style - something.from_vector(vec, shift) or something.from_vector<type>(vec, shift)
    # We want to match .from_vector<...>(arg1, arg2) or .from_vector(arg1, arg2) with at least 2 arguments
    # The key is to find from_vector calls that have a comma (indicating shift parameter)
    
    # Pattern for .from_vector<optional_template>(args_with_comma)
    pattern_method = re.compile(
        r'(\.from_vector\s*(?:<[^>]*>)?\s*\()'  # group 1: .from_vector<...>(
        r'([^,)]+)'                               # group 2: first argument (vector)
        r'(\s*,\s*)'                              # group 3: comma and whitespace
        r'([^)]+)'                                # group 4: second argument (shift)
        r'(\))'                                   # group 5: closing paren
    )

    # Pattern for free function style: from_vector<acc48>(vec, shift) or from_vector<acc80>(vec, shift)
    pattern_free = re.compile(
        r'(from_vector\s*<[^>]*>\s*\()'           # group 1: from_vector<type>(
        r'([^,)]+)'                               # group 2: first argument
        r'(\s*,\s*)'                              # group 3: comma
        r'([^)]+)'                                # group 4: shift argument
        r'(\))'                                   # group 5: closing paren
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Check if file has any relevant content
        has_relevant = any(target in content for target in BUG_FAMILY["match_targets"])
        if not has_relevant:
            continue

        # Search with method pattern
        for match in pattern_method.finditer(content):
            original = match.group(0)
            # Replacement: remove the comma and shift argument
            replacement = match.group(1) + match.group(2).rstrip() + match.group(5)
            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": "accum_from_vector_missing_shift",
                "category": "accumulator_initialization",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": f"Remove shift parameter from from_vector() call, leaving only the vector argument. This will cause a compile error due to missing required shift argument."
            })

        # Search with free function pattern (only if not already a method call, i.e., no dot before)
        for match in pattern_free.finditer(content):
            # Skip if this is actually a method call (already caught above)
            if match.start() > 0 and content[match.start() - 1] == '.':
                continue

            original = match.group(0)
            replacement = match.group(1) + match.group(2).rstrip() + match.group(5)
            start = match.start()
            end = match.end()

            candidates.append({
                "file_path": file_path,
                "bug_type": "accum_from_vector_missing_shift",
                "category": "accumulator_initialization",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": f"Remove shift parameter from from_vector() call, leaving only the vector argument. This will cause a compile error due to missing required shift argument."
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
