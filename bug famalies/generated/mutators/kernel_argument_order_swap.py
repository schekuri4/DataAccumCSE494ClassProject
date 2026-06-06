import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF031",
    "bug_type": "kernel_argument_order_swap",
    "category": "kernel_prototypes_and_signatures",
    "target_files": [
        "kernel header",
        "kernel source",
        "graph header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_window<",
        "output_window<",
        "input_stream<",
        "output_stream<",
        "void kernel_func("
    ],
    "mutation_strategy": "Swap the order of two kernel function parameters in the declaration (header) or definition (source) so that the prototype disagrees with the graph's connect<> bindings.",
    "repair_expectation": "Restore the original parameter order in the kernel declaration/definition so it matches the graph's port binding order.",
    "validation_signal": "WSL Vitis/AIE compile failure with errors about port type mismatch or incompatible kernel signature during ADF graph compilation.",
    "tags": [
        "argument_order",
        "kernel_prototypes_and_signatures",
        "signature_mismatch",
        "stream",
        "window"
    ]
}

# Pattern to match kernel-like function declarations/definitions with AIE port parameters
_KERNEL_FUNC_PATTERN = re.compile(
    r'(void\s+\w+\s*\()'   # return type and function name with opening paren
    r'([^)]+)'              # parameters
    r'(\)\s*[;{])',         # closing paren followed by ; or {
    re.DOTALL
)

# Pattern to identify AIE port parameters
_AIE_PARAM_PATTERN = re.compile(
    r'((?:input_window|output_window|input_stream|output_stream)\s*<[^>]+>\s*\*?\s*\w+)'
)

# Match targets as a set for quick checking
_MATCH_TARGETS = [
    "input_window<",
    "output_window<",
    "input_stream<",
    "output_stream<",
]


def _has_aie_content(content: str) -> bool:
    """Check if file content contains any AIE match targets."""
    for target in _MATCH_TARGETS:
        if target in content:
            return True
    return False


def _is_target_file(filepath: str) -> bool:
    """Heuristic to determine if a file is a kernel header, kernel source, or graph header."""
    lower = filepath.lower()
    # Accept .h, .hpp, .cpp, .cc files that might be kernel or graph files
    extensions = ('.h', '.hpp', '.cpp', '.cc', '.c')
    return any(lower.endswith(ext) for ext in extensions)


def _split_params(params_str: str) -> list[str]:
    """Split parameter string by commas, respecting angle brackets and parentheses."""
    params = []
    depth = 0
    current = []
    for ch in params_str:
        if ch in ('<', '('):
            depth += 1
            current.append(ch)
        elif ch in ('>', ')'):
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            params.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        params.append(''.join(current).strip())
    return params


def _is_aie_port_param(param: str) -> bool:
    """Check if a parameter is an AIE port type."""
    for target in _MATCH_TARGETS:
        if target in param:
            return True
    return False


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find all places where we can swap two kernel function parameters."""
    candidates: list[dict[str, object]] = []

    for filepath, content in project_files.items():
        if not _is_target_file(filepath):
            continue
        if not _has_aie_content(content):
            continue

        for match in _KERNEL_FUNC_PATTERN.finditer(content):
            prefix = match.group(1)
            params_str = match.group(2)
            suffix = match.group(3)

            params = _split_params(params_str)
            if len(params) < 2:
                continue

            # Find indices of AIE port parameters
            aie_indices = [i for i, p in enumerate(params) if _is_aie_port_param(p)]

            if len(aie_indices) < 2:
                # If we don't have 2 AIE port params, try swapping any two params
                # as long as at least one is an AIE port param
                if len(aie_indices) >= 1 and len(params) >= 2:
                    # Swap the first AIE param with an adjacent non-AIE param
                    idx_a = aie_indices[0]
                    idx_b = (idx_a + 1) % len(params)
                    if idx_b == idx_a:
                        idx_b = (idx_a - 1) % len(params)
                else:
                    continue
            else:
                # Swap the first two AIE port parameters
                idx_a = aie_indices[0]
                idx_b = aie_indices[1]

            # Ensure we're swapping different types to create a real mismatch
            if params[idx_a].strip() == params[idx_b].strip():
                # Same type and name pattern - still swap for position mismatch
                # but only if names differ
                pass

            # Build the swapped version
            swapped_params = list(params)
            swapped_params[idx_a], swapped_params[idx_b] = swapped_params[idx_b], swapped_params[idx_a]

            original_full = prefix + params_str + suffix
            # Reconstruct with same spacing style
            new_params_str = ', '.join(swapped_params)
            replacement_full = prefix + new_params_str + suffix

            if original_full == replacement_full:
                continue

            start = match.start()
            end = match.end()

            description = (
                f"Swap kernel parameters at positions {idx_a} and {idx_b}: "
                f"'{params[idx_a].strip()}' <-> '{params[idx_b].strip()}' "
                f"in {filepath}"
            )

            candidates.append({
                "file_path": filepath,
                "bug_type": "kernel_argument_order_swap",
                "category": "kernel_prototypes_and_signatures",
                "start": start,
                "end": end,
                "original": original_full,
                "replacement": replacement_full,
                "description": description,
            })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    content = new_files[filepath]

    # Replace the first occurrence of the original text
    start = candidate["start"]
    end = candidate["end"]

    # Verify the content at the expected position matches
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replace for first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[filepath] = new_content
    return new_files
