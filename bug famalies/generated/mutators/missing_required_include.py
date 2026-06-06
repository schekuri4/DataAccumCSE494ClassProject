import re
import copy

BUG_FAMILY = {
    "family_id": "BF451",
    "bug_type": "missing_required_include",
    "category": "include_headers",
    "target_files": [
        "kernel source",
        "kernel header",
        "graph header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include",
        "aie::",
        "adf::",
        "readincr_v",
        "writeincr_v",
        "shuffle_up",
        "shuffle_down"
    ],
    "mutation_strategy": "Remove a required include from a kernel, kernel header, or graph file that uses AIE or ADF APIs so the compiler can no longer see the needed declaration. This is aimed at common missing-header failures where the file compiles only because another include previously pulled in the symbol transitively.",
    "repair_expectation": "Re-add the missing include at the top of the file or include the direct header that declares the missing API or type.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared identifier, unknown type, or missing declaration errors for the removed API.",
    "tags": [
        "compile_error",
        "include",
        "include_headers",
        "missing_include",
        "undeclared"
    ]
}


def _is_target_file(file_path):
    lower = file_path.lower()
    return lower.endswith(('.cpp', '.cc', '.c', '.cxx', '.h', '.hpp', '.hxx'))


def _is_target_include(line):
    return line.startswith('#include')


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_target_file(file_path):
            continue

        lines = content.splitlines(keepends=True)
        offset = 0
        for line in lines:
            line_start = offset
            line_end = offset + len(line)
            stripped = line.strip()
            if _is_target_include(stripped):
                if any(target in content for target in BUG_FAMILY["match_targets"] if target != "#include"):
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": BUG_FAMILY["bug_type"],
                        "category": BUG_FAMILY["category"],
                        "start": line_start,
                        "end": line_end,
                        "original": line,
                        "replacement": "",
                        "description": f"Removed required include in {file_path} to expose a missing declaration failure."
                    })
            offset = line_end

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        new_content = content.replace(original, replacement, 1)
    new_files[file_path] = new_content
    return new_files
