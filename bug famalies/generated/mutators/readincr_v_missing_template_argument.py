import re
import copy

BUG_FAMILY = {
    "family_id": "BF120",
    "bug_type": "readincr_v_missing_template_argument",
    "category": "stream_vector_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v<",
        "readincr_v(",
        "writeincr_v<",
        "writeincr_v("
    ],
    "mutation_strategy": "Remove the template argument from readincr_v<N> or writeincr_v<N>, calling it as readincr_v(stream_ptr) without specifying the vector lane count, or provide a non-power-of-2 or unsupported lane count like readincr_v<3> or readincr_v<5>.",
    "repair_expectation": "Provide the correct template argument N (a supported power-of-2 lane count such as 4, 8, 16, or 32) to readincr_v<N> or writeincr_v<N>.",
    "validation_signal": "WSL Vitis/AIE compile failure with template argument deduction failure or static assertion about unsupported vector size.",
    "tags": [
        "compile_error",
        "lane_count",
        "readincr_v",
        "stream_vector_interfaces",
        "template_argument"
    ]
}

# Pattern matches readincr_v<N>(...) or writeincr_v<N>(...)
# Captures: function name, template argument, and the opening paren with arguments
_PATTERN_WITH_TEMPLATE = re.compile(
    r'\b(readincr_v|writeincr_v)\s*<\s*(\d+)\s*>\s*(\()'
)

# Pattern matches readincr_v(...) without template argument (already buggy - skip)
_PATTERN_WITHOUT_TEMPLATE = re.compile(
    r'\b(readincr_v|writeincr_v)\s*(\()(?!.*<)'
)


def _is_kernel_source(file_path):
    """Heuristic to identify kernel source files for AIE."""
    extensions = ('.cpp', '.cc', '.c', '.h', '.hpp', '.cxx')
    return any(file_path.endswith(ext) for ext in extensions)


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Find calls with template arguments that we can mutate
        for match in _PATTERN_WITH_TEMPLATE.finditer(content):
            func_name = match.group(1)
            template_arg = match.group(2)
            full_match = match.group(0)
            start = match.start()
            end = match.end()

            # Mutation strategy 1: Remove template argument entirely
            # readincr_v<8>(stream) -> readincr_v(stream)
            replacement_no_template = func_name + "("
            candidates.append({
                "file_path": file_path,
                "bug_type": "readincr_v_missing_template_argument",
                "category": "stream_vector_interfaces",
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement_no_template,
                "description": f"Remove template argument from {func_name}<{template_arg}>, calling it as {func_name}() without specifying vector lane count."
            })

            # Mutation strategy 2: Replace with unsupported lane count (non-power-of-2)
            bad_lane_counts = [3, 5, 7]
            for bad_count in bad_lane_counts:
                if str(bad_count) != template_arg:
                    replacement_bad = f"{func_name}<{bad_count}>("
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "readincr_v_missing_template_argument",
                        "category": "stream_vector_interfaces",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": replacement_bad,
                        "description": f"Replace {func_name}<{template_arg}> with unsupported lane count {func_name}<{bad_count}>."
                    })
                    break  # Only one bad lane count candidate per site

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
