import re
import copy

BUG_FAMILY = {
    "family_id": "BF102",
    "bug_type": "writeincr_const_output_stream",
    "category": "stream_scalar_interfaces",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "writeincr(",
        "output_stream_int32*",
        "output_stream_float*",
        "output_stream_int16*",
    ],
    "mutation_strategy": "Add a const qualifier to the output_stream pointer parameter in the kernel signature (e.g., const output_stream_int32* sout), making writeincr fail because it cannot write to a const-qualified stream.",
    "repair_expectation": "Remove the const qualifier from the output_stream pointer parameter.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about passing const-qualified pointer to writeincr which expects a non-const output_stream pointer.",
    "tags": [
        "const_correctness",
        "output_stream",
        "qualifier",
        "stream_scalar_interfaces",
        "writeincr",
    ],
}

# Pattern to match output_stream pointer parameters that are NOT already const-qualified.
# Matches things like: output_stream_int32* varname  or  output_stream_float * varname
# We avoid matching if 'const' already precedes the output_stream type.
_OUTPUT_STREAM_PARAM_RE = re.compile(
    r'(?<!const\s)(?<!const)'  # negative lookbehind (limited, we'll filter in code)
    r'\b(output_stream_(?:int32|int16|float)\s*\*)'
    r'(\s*\w+)'
)

# More robust: find non-const output_stream parameters
_PARAM_RE = re.compile(
    r'((?:^|[,(])\s*)'  # leading context: start of params, comma, or open paren
    r'(output_stream_(?:int32|int16|float)\s*\*\s*\w+)',
    re.MULTILINE
)


def _is_kernel_file(filepath):
    """Heuristic: kernel source or header files typically end in .cpp, .cc, .h, .hpp"""
    lower = filepath.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hh'))


def _has_writeincr(content):
    """Check if file uses writeincr"""
    return 'writeincr(' in content or 'writeincr (' in content


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_kernel_file(filepath):
            continue

        # We look for output_stream pointer parameters that are not const-qualified
        # and the file should use writeincr (or be a header declaring such a function)
        # For headers, we allow even without writeincr since they declare the signature

        has_writeincr_usage = _has_writeincr(content)

        # Check if file has any output_stream type reference
        has_output_stream = any(
            t.replace('*', '') in content
            for t in ['output_stream_int32', 'output_stream_float', 'output_stream_int16']
        )

        if not has_output_stream:
            continue

        # If it's a source file, require writeincr usage or output_stream params
        # If it's a header, output_stream params are enough
        is_header = filepath.lower().endswith(('.h', '.hpp', '.hh'))
        if not is_header and not has_writeincr_usage:
            continue

        # Find all output_stream parameter occurrences not already const
        # Use a regex that captures the full parameter with surrounding context
        pattern = re.compile(
            r'(?<!\bconst\s)'
            r'(\boutput_stream_(?:int32|int16|float)\s*\*\s*\w+)',
        )

        for match in pattern.finditer(content):
            param_text = match.group(1)
            start = match.start(1)
            end = match.end(1)

            # Verify no 'const' immediately before this match
            prefix = content[max(0, start - 20):start]
            if re.search(r'\bconst\s*$', prefix):
                continue

            replacement = 'const ' + param_text
            description = (
                f"Add 'const' qualifier to output_stream parameter '{param_text.strip()}' "
                f"making writeincr fail due to const-qualified stream pointer."
            )

            candidates.append({
                "file_path": filepath,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": param_text,
                "replacement": replacement,
                "description": description,
            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    filepath = candidate["file_path"]
    content = new_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: try to find it
        idx = content.find(original)
        if idx == -1:
            return new_files  # cannot apply
        start = idx
        end = idx + len(original)

    new_content = content[:start] + candidate["replacement"] + content[end:]
    new_files[filepath] = new_content
    return new_files
