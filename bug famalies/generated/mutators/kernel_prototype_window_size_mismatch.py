import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF023",
    "bug_type": "kernel_prototype_window_size_mismatch",
    "category": "graph_kernel_binding",
    "target_files": ["kernel header", "graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_window<",
        "output_window<",
        "input_buffer<",
        "output_buffer<",
    ],
    "mutation_strategy": (
        "Change the kernel function prototype parameter types so they differ from "
        "what the graph connect<> statements expect. For example, declare the kernel "
        "with input_window<int32> but connect it in the graph as if it takes "
        "input_window<cint16>, or change window to stream parameter type."
    ),
    "repair_expectation": (
        "Align the kernel function prototype parameter types with the graph connection "
        "template types."
    ),
    "validation_signal": (
        "WSL Vitis/AIE compile failure indicating type mismatch between kernel port "
        "and connection template parameter."
    ),
    "tags": [
        "connect",
        "graph_kernel_binding",
        "parameter_mismatch",
        "prototype",
        "window_type",
    ],
}

# Type replacements: map original type to a different but plausible AIE type
_TYPE_REPLACEMENTS: dict[str, list[str]] = {
    "int32": ["cint16", "int16", "float"],
    "cint16": ["int32", "cint32", "int16"],
    "int16": ["int32", "cint16", "float"],
    "cint32": ["cint16", "int32", "float"],
    "float": ["int32", "cint16", "cfloat"],
    "cfloat": ["float", "cint32", "int32"],
    "int8": ["int16", "int32", "cint16"],
    "uint8": ["int16", "int32", "uint16"],
    "uint16": ["int32", "uint8", "int16"],
    "uint32": ["int32", "uint16", "float"],
}

# Pattern to match input_window<type>, output_window<type>, input_buffer<type>, output_buffer<type>
_WINDOW_BUFFER_PATTERN = re.compile(
    r'(input_window|output_window|input_buffer|output_buffer)\s*<\s*(\w+)\s*>'
)

# Alternative mutation: change the wrapper type itself (window <-> buffer, or window -> stream)
_WRAPPER_REPLACEMENTS: dict[str, str] = {
    "input_window": "input_buffer",
    "output_window": "output_buffer",
    "input_buffer": "input_window",
    "output_buffer": "output_window",
}


def _is_kernel_header(path: str) -> bool:
    """Heuristic: kernel headers often have 'kernel' in name or are .h/.hpp files."""
    lower = path.lower()
    # Broad: any header file could be a kernel header
    return lower.endswith(('.h', '.hpp'))


def _get_replacement_type(original_type: str) -> str | None:
    """Get a different type to replace the original."""
    replacements = _TYPE_REPLACEMENTS.get(original_type)
    if replacements:
        return replacements[0]
    # If not in our map, try a generic replacement
    if original_type != "int32":
        return "int32"
    return "cint16"


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_header(file_path):
            continue

        # Look for window/buffer parameter type patterns
        for match in _WINDOW_BUFFER_PATTERN.finditer(content):
            wrapper_type = match.group(1)
            inner_type = match.group(2)
            full_match = match.group(0)
            start = match.start()
            end = match.end()

            # Strategy 1: Change the inner type (e.g., int32 -> cint16)
            new_inner = _get_replacement_type(inner_type)
            if new_inner and new_inner != inner_type:
                replacement = f"{wrapper_type}<{new_inner}>"
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "kernel_prototype_window_size_mismatch",
                    "category": "graph_kernel_binding",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": (
                        f"Changed kernel parameter type from {full_match} to "
                        f"{replacement} to create type mismatch with graph connections."
                    ),
                })

            # Strategy 2: Change the wrapper type (e.g., input_window -> input_buffer)
            new_wrapper = _WRAPPER_REPLACEMENTS.get(wrapper_type)
            if new_wrapper:
                replacement2 = f"{new_wrapper}<{inner_type}>"
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "kernel_prototype_window_size_mismatch",
                    "category": "graph_kernel_binding",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement2,
                    "description": (
                        f"Changed kernel parameter wrapper from {full_match} to "
                        f"{replacement2} to create type mismatch with graph connections."
                    ),
                })

    return candidates


def apply_mutation(
    project_files: dict[str, str], candidate: dict[str, object]
) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    content = new_files[file_path]

    # Verify the original text is at the expected position
    actual = content[start:end]
    if actual == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
