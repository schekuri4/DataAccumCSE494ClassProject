import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF108",
    "bug_type": "kernel_create_wrong_function_for_stream_signature",
    "category": "stream_scalar_interfaces",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "kernel::create(",
        "adf::kernel::create(",
        "input_stream",
        "output_stream"
    ],
    "mutation_strategy": "In kernel::create<>(), reference a different function name or a function with a mismatched signature (e.g., one that takes window parameters instead of stream pointers), so the graph cannot bind stream ports to the kernel function's actual parameters.",
    "repair_expectation": "Correct the kernel::create<>() template argument to reference the function with the proper stream pointer signature.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about kernel function signature mismatch or unresolved kernel function template instantiation.",
    "tags": [
        "graph",
        "kernel_create",
        "signature_mismatch",
        "stream_ports",
        "stream_scalar_interfaces"
    ]
}


def _is_graph_header(file_path: str) -> bool:
    """Heuristic: graph headers are .h or .hpp files likely containing graph definitions."""
    lower = file_path.lower()
    return lower.endswith('.h') or lower.endswith('.hpp')


def _file_has_stream_context(content: str) -> bool:
    """Check if file references stream types, indicating stream-based kernels."""
    return (
        'input_stream' in content or 'output_stream' in content or
        'connect<stream' in content or 'input_plio' in content or
        'output_plio' in content
    )


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match kernel::create<function_name>(...) with optional adf:: prefix
    # Captures the full match and the function name inside angle brackets
    kernel_create_pattern = re.compile(
        r'((?:adf::)?kernel::create\s*<\s*)([A-Za-z_][A-Za-z0-9_:]*)\s*(>\s*\()'
    )
    plain_kernel_create_pattern = re.compile(
        r'((?:adf::)?kernel::create\s*\(\s*)'
        r'([A-Za-z_][A-Za-z0-9_:]*(?:\s*<[^;\n()]*>)?)'
        r'(\s*\))'
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        if not _file_has_stream_context(content):
            continue

        for match in kernel_create_pattern.finditer(content):
            original_func_name = match.group(2)
            full_original = match.group(0)
            start_pos = match.start()
            end_pos = match.end()

            # Generate a mutated function name that suggests a window-based signature
            # Strategy: append "_window" or replace with a clearly wrong name
            mutated_func_name = original_func_name + "_window_variant"

            # Build the replacement string
            replacement = match.group(1) + mutated_func_name + match.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start_pos,
                "end": end_pos,
                "original": full_original,
                "replacement": replacement,
                "description": (
                    f"Changed kernel::create<{original_func_name}> to "
                    f"kernel::create<{mutated_func_name}> — referencing a function "
                    f"with a mismatched signature (window instead of stream pointers)."
                )
            })

        for match in plain_kernel_create_pattern.finditer(content):
            original_func_name = match.group(2).strip()
            mutated_func_name = original_func_name + "_window_variant"

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": match.group(0),
                "replacement": match.group(1) + mutated_func_name + match.group(3),
                "description": (
                    f"Change kernel::create({original_func_name}) to "
                    f"kernel::create({mutated_func_name}) for a stream graph."
                )
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_project_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_project_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)

    new_project_files[file_path] = new_content
    return new_project_files
