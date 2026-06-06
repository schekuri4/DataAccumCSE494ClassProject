import re
import copy

BUG_FAMILY = {
    "family_id": "BF036",
    "bug_type": "buffer_template_element_type_mismatch",
    "category": "kernel_prototypes_and_signatures",
    "target_files": ["kernel header", "graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_window<cint16>",
        "input_window<int32>",
        "output_window<float>",
        "input_buffer<",
        "output_buffer<"
    ],
    "mutation_strategy": "Change the element type template parameter in the kernel's window/buffer declaration to a different type (e.g., input_window<cint16>* becomes input_window<int32>*) without updating the graph connect<window<...>> which still uses the original byte size calculation for the original type.",
    "repair_expectation": "Align the element type in the kernel parameter with the type used in the graph's connect<> template and window size.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch or window size incompatibility error.",
    "tags": ["element_type", "kernel_prototypes_and_signatures", "template_mismatch", "window_buffer"]
}

# Type substitution map: for each type, provide a different type to swap to
_TYPE_SUBSTITUTIONS = {
    "cint16": "int32",
    "int32": "cint16",
    "float": "int32",
    "int16": "int32",
    "cint32": "int32",
    "int8": "int16",
    "uint8": "int16",
    "uint16": "int32",
    "uint32": "int32",
    "cfloat": "float",
}

# Pattern to match input_window<TYPE>, output_window<TYPE>, input_buffer<TYPE...>, output_buffer<TYPE...>
_WINDOW_BUFFER_PATTERN = re.compile(
    r'((?:input_window|output_window|input_buffer|output_buffer)\s*<\s*)([a-zA-Z_][a-zA-Z0-9_]*)(\s*(?:[,>]))'
)


def _is_kernel_or_graph_header(file_path):
    """Heuristic: target kernel headers and graph headers (typically .h or .hpp files)."""
    lower = file_path.lower()
    if lower.endswith('.h') or lower.endswith('.hpp'):
        return True
    return False


def _get_replacement_type(original_type):
    """Get a different type to substitute for the original."""
    if original_type in _TYPE_SUBSTITUTIONS:
        return _TYPE_SUBSTITUTIONS[original_type]
    # Default fallback: swap to int32 if not already int32, else cint16
    if original_type != "int32":
        return "int32"
    return "cint16"


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_or_graph_header(file_path):
            continue

        for match in _WINDOW_BUFFER_PATTERN.finditer(content):
            original_type = match.group(2)
            replacement_type = _get_replacement_type(original_type)

            if replacement_type == original_type:
                continue

            # Full matched text
            full_original = match.group(0)
            full_replacement = match.group(1) + replacement_type + match.group(3)

            start = match.start()
            end = match.end()

            # Determine which construct we're in for description
            construct = match.group(1).strip().rstrip('<').strip()

            candidate = {
                "file_path": file_path,
                "bug_type": "buffer_template_element_type_mismatch",
                "category": "kernel_prototypes_and_signatures",
                "start": start,
                "end": end,
                "original": full_original,
                "replacement": full_replacement,
                "description": (
                    f"Change {construct} element type from '{original_type}' to "
                    f"'{replacement_type}' in {file_path}, creating a type mismatch "
                    f"with the graph's connect<> template and window size calculation."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
