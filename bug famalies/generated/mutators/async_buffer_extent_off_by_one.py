import re


BUG_FAMILY = {
    "family_id": "BF_MANUAL_BUF_001",
    "bug_type": "async_buffer_extent_off_by_one",
    "category": "buffer_interfaces",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["input_async_buffer", "output_async_buffer", "extents<"],
    "mutation_strategy": (
        "Change the extents<> expression on an async buffer parameter by one so "
        "the kernel signature no longer matches the graph/runtime buffer contract."
    ),
    "repair_expectation": "Restore the original extents expression for the async buffer parameter.",
    "validation_signal": "WSL Vitis/AIE compile failure with buffer extent or kernel signature mismatch.",
    "tags": ["async_buffer", "buffer_interfaces", "extent", "kernel_signature", "single_span"],
}


_EXTENT_PATTERN = re.compile(
    r'((?:input_async_buffer|output_async_buffer)\s*<[^>]*?extents\s*<\s*)([^>]+?)(\s*>)'
)


def _is_kernel_file(path):
    return path.lower().endswith((".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh"))


def _mutate_extent(expr):
    stripped = expr.strip()
    if stripped.isdigit():
        return str(int(stripped) + 1)
    if "+ 1" in stripped or "- 1" in stripped:
        return f"({stripped}) + 2"
    return f"({stripped}) + 1"


def find_mutation_candidates(project_files):
    candidates = []
    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path):
            continue
        if "async_buffer" not in content or "extents" not in content:
            continue
        for match in _EXTENT_PATTERN.finditer(content):
            original = match.group(2)
            replacement = _mutate_extent(original)
            if replacement == original:
                continue
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(2),
                "end": match.end(2),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed async buffer extent from '{original.strip()}' to "
                    f"'{replacement}', creating an extent mismatch."
                ),
            })
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
        new_files[file_path] = content[:start] + replacement + content[end:]
    else:
        new_files[file_path] = content.replace(original, replacement, 1)
    return new_files
