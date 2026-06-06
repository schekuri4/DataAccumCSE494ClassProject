import re
import copy

BUG_FAMILY = {
    "family_id": "BF095",
    "bug_type": "rtp_type_mismatch_between_graph_and_kernel",
    "category": "rtp_parameters",
    "target_files": [
        "kernel source",
        "kernel header",
        "graph header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "int32",
        "float",
        "int16",
        "cint16",
        "port<direction::in>",
        "connect<parameter>"
    ],
    "mutation_strategy": "Change the data type of the RTP parameter in the kernel function signature (e.g., from int32 to float) without updating the graph's connect<parameter> template type or update() call type, causing a compile-time type mismatch.",
    "repair_expectation": "Align the RTP data type in the kernel signature with the type used in the graph connection and update API calls.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about incompatible types in RTP parameter connection or template instantiation.",
    "tags": [
        "compile_time",
        "graph",
        "kernel_signature",
        "rtp",
        "rtp_parameters",
        "type_mismatch"
    ]
}

# Type replacement mapping: for each type, pick a different type to cause mismatch
_TYPE_REPLACEMENTS = {
    "int32": "float",
    "float": "int32",
    "int16": "float",
    "cint16": "int32",
}

# All RTP-related scalar types we look for in kernel signatures
_RTP_TYPES = ["int32", "float", "int16", "cint16"]


def _is_kernel_file(path):
    """Heuristic: kernel source or header (not graph header)."""
    lower = path.lower()
    # Exclude graph files
    if "graph" in lower:
        return False
    # Include .h, .hpp, .cc, .cpp files that likely contain kernel code
    return lower.endswith(('.h', '.hpp', '.cc', '.cpp', '.c'))


def _is_graph_file(path):
    """Heuristic: graph header file."""
    lower = path.lower()
    return "graph" in lower and lower.endswith(('.h', '.hpp'))


def _has_rtp_context(project_files):
    """Check if any graph file has connect<parameter> or port<direction::in> indicating RTP usage."""
    for path, content in project_files.items():
        if _is_graph_file(path):
            if "connect<parameter>" in content or "port<direction::in>" in content:
                return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # We look for RTP parameters in kernel function signatures in kernel files
    # A typical kernel signature: void my_kernel(..., int32 rtp_param, ...)
    # We mutate the type of scalar RTP-like parameters in kernel source/header files

    # Build a regex that matches RTP types as function parameter types
    # Pattern: a known type followed by whitespace and an identifier, in a function parameter context
    type_pattern = r'\b(' + '|'.join(re.escape(t) for t in _RTP_TYPES) + r')\b'

    # We look for function declarations/definitions with these types as parameters
    # Match: type followed by identifier, within parentheses (function params)
    param_pattern = re.compile(
        r'(?<=[\(,\s])(\s*)(' + '|'.join(re.escape(t) for t in _RTP_TYPES) + r')(\s+\w+)'
    )

    for path, content in project_files.items():
        if not _is_kernel_file(path):
            continue

        # Find function-like contexts (lines with parentheses containing known types)
        for match in param_pattern.finditer(content):
            original_type = match.group(2)
            if original_type not in _TYPE_REPLACEMENTS:
                continue

            replacement_type = _TYPE_REPLACEMENTS[original_type]

            # Full matched text
            full_match = match.group(0)
            # Replace the type within the match
            new_match = full_match.replace(original_type, replacement_type, 1)

            start = match.start()
            end = match.end()

            # Verify this is inside a function signature (look for surrounding parens)
            # Simple heuristic: check there's an open paren before this on the same logical block
            preceding = content[max(0, start - 200):start]
            if '(' not in preceding:
                continue

            candidates.append({
                "file_path": path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": new_match,
                "description": (
                    f"Changed RTP parameter type from '{original_type}' to '{replacement_type}' "
                    f"in kernel file '{path}' without updating the graph's connect<parameter> "
                    f"template type, causing a compile-time type mismatch."
                )
            })

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
