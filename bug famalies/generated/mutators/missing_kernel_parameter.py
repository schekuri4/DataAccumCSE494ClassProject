import re
import copy

BUG_FAMILY = {
    "family_id": "BF032",
    "bug_type": "missing_kernel_parameter",
    "category": "kernel_prototypes_and_signatures",
    "target_files": ["kernel header", "kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_window<",
        "output_window<",
        "input_stream<",
        "output_stream<",
        "input_buffer<",
        "output_buffer<"
    ],
    "mutation_strategy": "Remove one parameter from the kernel function prototype (e.g., remove the output_window<cint16>* parameter) while keeping the graph connect<> that binds to that port, causing an arity mismatch between the declared kernel and the graph port count.",
    "repair_expectation": "Re-add the missing parameter to the kernel function declaration and definition with the correct type and position.",
    "validation_signal": "WSL Vitis/AIE compile failure reporting mismatched number of kernel ports or unresolved port binding.",
    "tags": [
        "arity_mismatch",
        "kernel_declaration",
        "kernel_prototypes_and_signatures",
        "missing_parameter"
    ]
}

# Pattern to match AIE kernel port parameter types
_PORT_TYPES = r'(?:input_window|output_window|input_stream|output_stream|input_buffer|output_buffer)'

# Pattern to match a single parameter that is an AIE port type
# e.g., "input_window<cint16>* in" or "output_stream<int32> * out"
_PARAM_PATTERN = re.compile(
    r'(' + _PORT_TYPES + r'<[^>]*>\s*\*?\s*\w+)'
)

# Pattern to find function declarations/definitions with AIE port parameters
# Matches: "void funcname(" ... params with port types ... ")"
_FUNC_PATTERN = re.compile(
    r'((?:void|int|float|double|auto)\s+\w+\s*\()'  # return type + name + open paren
    r'([^)]*' + _PORT_TYPES + r'[^)]*)'              # parameters containing port types
    r'(\)\s*[;{])',                                    # close paren + semicolon or brace
    re.DOTALL
)


def _is_kernel_file(filepath):
    """Heuristic: header or source file likely containing kernel code."""
    lower = filepath.lower()
    # Target .h, .hpp, .cc, .cpp files
    return any(lower.endswith(ext) for ext in ('.h', '.hpp', '.hh', '.cc', '.cpp', '.c'))


def _split_params(params_str):
    """Split parameter string by commas, respecting angle brackets."""
    params = []
    depth = 0
    current = []
    for ch in params_str:
        if ch == '<':
            depth += 1
            current.append(ch)
        elif ch == '>':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            params.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        params.append(''.join(current))
    return params


def _has_port_type(param):
    """Check if a parameter contains one of the AIE port types."""
    for mt in BUG_FAMILY["match_targets"]:
        if mt in param:
            return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_kernel_file(filepath):
            continue

        # Check if file contains any of the match targets
        has_target = any(mt in content for mt in BUG_FAMILY["match_targets"])
        if not has_target:
            continue

        # Find all function signatures with port-type parameters
        for match in _FUNC_PATTERN.finditer(content):
            prefix = match.group(1)   # "void funcname("
            params_str = match.group(2)  # parameters
            suffix = match.group(3)   # ");" or ") {"

            full_match_start = match.start()
            full_match_end = match.end()
            original_text = match.group(0)

            # Split parameters
            params = _split_params(params_str)

            # Find port-type parameters (candidates for removal)
            port_param_indices = [i for i, p in enumerate(params) if _has_port_type(p)]

            if len(port_param_indices) < 1:
                continue

            # We need at least 2 parameters total or at least 2 port params
            # to make a meaningful removal (removing one still leaves a valid function)
            # Actually, we can remove even if it leaves 0 params - that's the bug.
            # Prefer removing the last port-type parameter for maximum impact.
            for remove_idx in port_param_indices:
                removed_param = params[remove_idx].strip()
                new_params = [p for i, p in enumerate(params) if i != remove_idx]
                new_params_str = ','.join(new_params)

                # Clean up: if removal leaves leading/trailing commas or spaces
                new_params_str = new_params_str.strip()

                replacement_text = prefix + new_params_str + suffix

                candidates.append({
                    "file_path": filepath,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": full_match_start,
                    "end": full_match_end,
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Remove kernel parameter '{removed_param.strip()}' from function signature "
                        f"in {filepath}, causing arity mismatch with graph port bindings."
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = new_files[filepath]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[filepath] = new_content
    return new_files
