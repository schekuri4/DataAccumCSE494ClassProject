import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF098",
    "bug_type": "rtp_scalar_passed_as_array_or_vice_versa",
    "category": "rtp_parameters",
    "target_files": [
        "kernel source",
        "kernel header",
        "graph header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "int32 rtp_val",
        "int32 (&rtp)[",
        "dimensions(",
        "connect<parameter>"
    ],
    "mutation_strategy": "Change a scalar RTP parameter in the kernel signature to an array reference (or vice versa) without updating the graph constraints (adding/removing dimensions()), causing a compile-time mismatch between scalar and array RTP handling.",
    "repair_expectation": "Ensure consistency between scalar/array RTP declaration in the kernel signature and the corresponding graph constraints (dimensions() for arrays, none for scalars).",
    "validation_signal": "WSL Vitis/AIE compile failure with error about RTP parameter type incompatibility or missing/unexpected dimensions constraint.",
    "tags": [
        "array",
        "dimensions",
        "rtp",
        "rtp_parameters",
        "scalar",
        "signature_mismatch"
    ]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find scalar RTP parameters that can be mutated to array references, or vice versa."""
    candidates: list[dict[str, object]] = []

    # Pattern for scalar RTP: type identifier (not already an array reference)
    # Matches things like: int32 rtp_val, int32 coeff, float scale
    # Common AIE scalar types
    scalar_types = r'(?:int8|int16|int32|int64|uint8|uint16|uint32|uint64|float|int|unsigned\s+int)'

    # Pattern 1: scalar parameter -> array reference
    # Matches: type param_name (as a function parameter, not already (&name)[...])
    scalar_rtp_pattern = re.compile(
        r'(\b(' + scalar_types + r')\s+)((\w+))'
        r'(?=\s*[,\)])'  # followed by comma or closing paren (function param context)
    )

    # Pattern 2: array reference parameter -> scalar
    # Matches: type (&param_name)[size]
    array_rtp_pattern = re.compile(
        r'(\b(' + scalar_types + r')\s*\(\s*&\s*(\w+)\s*\)\s*\[\s*(\w+)\s*\])'
    )

    for file_path, content in project_files.items():
        # Target kernel source, kernel header, graph header files
        # Heuristic: .h, .hpp, .cc, .cpp files that look like kernel or graph files
        if not re.search(r'\.(h|hpp|cc|cpp|c)$', file_path, re.IGNORECASE):
            continue

        # Check if file looks like it contains kernel signatures or graph definitions
        is_kernel_file = bool(re.search(r'(void\s+\w+\s*\(|class\s+\w+.*kernel|#include.*aie)', content, re.IGNORECASE))
        is_graph_file = bool(re.search(r'(graph|connect\s*<\s*parameter|dimensions\s*\()', content, re.IGNORECASE))

        if not (is_kernel_file or is_graph_file):
            continue

        # Look for function declarations/definitions with RTP-like parameters
        # Find function signatures
        func_sig_pattern = re.compile(
            r'(void\s+\w+\s*\([^)]*\))',
            re.MULTILINE | re.DOTALL
        )

        for func_match in func_sig_pattern.finditer(content):
            func_sig = func_match.group(0)
            func_start = func_match.start()

            # Within the function signature, look for scalar params to mutate to array
            for scalar_match in scalar_rtp_pattern.finditer(func_sig):
                type_name = scalar_match.group(2).strip()
                param_name = scalar_match.group(4)

                # Skip common non-RTP params (like input/output stream params)
                if re.search(r'(input|output|stream|window|buffer)', param_name, re.IGNORECASE):
                    continue

                # Heuristic: likely an RTP if name contains rtp, coeff, val, param, scale, threshold, etc.
                # But we'll be permissive - any scalar in a kernel signature could be RTP
                original = scalar_match.group(0)
                # The full match is: type param_name
                # Replace with: type (&param_name)[16]
                replacement = f"{type_name} (&{param_name})[16]"

                abs_start = func_start + scalar_match.start()
                abs_end = func_start + scalar_match.end()

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "rtp_scalar_passed_as_array_or_vice_versa",
                    "category": "rtp_parameters",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Changed scalar RTP parameter '{type_name} {param_name}' to array reference "
                        f"'{replacement}' without updating graph dimensions() constraint, "
                        f"causing scalar/array RTP mismatch."
                    )
                })

            # Within the function signature, look for array ref params to mutate to scalar
            for array_match in array_rtp_pattern.finditer(func_sig):
                full_match = array_match.group(1)
                type_name = array_match.group(2).strip()
                param_name = array_match.group(3)

                original = full_match
                replacement = f"{type_name} {param_name}"

                abs_start = func_start + array_match.start()
                abs_end = func_start + array_match.end()

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "rtp_scalar_passed_as_array_or_vice_versa",
                    "category": "rtp_parameters",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Changed array reference RTP parameter '{original}' to scalar "
                        f"'{replacement}' without removing graph dimensions() constraint, "
                        f"causing array/scalar RTP mismatch."
                    )
                })

    # Also look for standalone (non-function-sig-wrapped) patterns in case the
    # function signature regex didn't capture everything
    for file_path, content in project_files.items():
        if not re.search(r'\.(h|hpp|cc|cpp|c)$', file_path, re.IGNORECASE):
            continue

        is_relevant = bool(re.search(r'(kernel|rtp|aie|graph|connect\s*<\s*parameter|dimensions)', content, re.IGNORECASE))
        if not is_relevant:
            continue

        # Direct pattern: look for "int32 rtp_val" style (from match_targets)
        direct_scalar = re.compile(
            r'\b(' + scalar_types + r')\s+(rtp\w*|coeff\w*|param\w*|threshold\w*|scale\w*)'
            r'(?=\s*[,\);])'
        )

        for m in direct_scalar.finditer(content):
            type_name = m.group(1).strip()
            param_name = m.group(2)
            original = m.group(0)
            replacement = f"{type_name} (&{param_name})[16]"

            # Check we haven't already found this candidate
            abs_start = m.start()
            abs_end = m.end()

            already_found = any(
                c["file_path"] == file_path and c["start"] == abs_start and c["end"] == abs_end
                for c in candidates
            )
            if already_found:
                continue

            candidates.append({
                "file_path": file_path,
                "bug_type": "rtp_scalar_passed_as_array_or_vice_versa",
                "category": "rtp_parameters",
                "start": abs_start,
                "end": abs_end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed scalar RTP parameter '{original}' to array reference "
                    f"'{replacement}' without updating graph dimensions() constraint."
                )
            })

        # Direct pattern for array ref (from match_targets): "int32 (&rtp)[..."
        direct_array = re.compile(
            r'\b(' + scalar_types + r')\s*\(\s*&\s*(\w+)\s*\)\s*\[\s*(\w+)\s*\]'
        )

        for m in direct_array.finditer(content):
            original = m.group(0)
            type_name = m.group(1).strip()
            param_name = m.group(2)
            replacement = f"{type_name} {param_name}"

            abs_start = m.start()
            abs_end = m.end()

            already_found = any(
                c["file_path"] == file_path and c["start"] == abs_start and c["end"] == abs_end
                for c in candidates
            )
            if already_found:
                continue

            candidates.append({
                "file_path": file_path,
                "bug_type": "rtp_scalar_passed_as_array_or_vice_versa",
                "category": "rtp_parameters",
                "start": abs_start,
                "end": abs_end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed array reference RTP parameter '{original}' to scalar "
                    f"'{replacement}' without removing graph dimensions() constraint."
                )
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate to the project files."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
