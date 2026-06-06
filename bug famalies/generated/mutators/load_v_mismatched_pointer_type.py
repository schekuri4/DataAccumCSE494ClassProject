import re
import copy

BUG_FAMILY = {
    "family_id": "BF191",
    "bug_type": "load_v_mismatched_pointer_type",
    "category": "vector_load_store",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::load_v<",
        "int32*",
        "int16*",
        "cint16*",
        "float*"
    ],
    "mutation_strategy": "Change the pointer argument passed to aie::load_v<N> so that the pointer's element type does not match the template element type. For example, pass an int16* to aie::load_v<8> where the context expects int32 elements, or pass a float* where cint16* is expected.",
    "repair_expectation": "Cast or change the pointer declaration to match the element type implied by the load_v template instantiation.",
    "validation_signal": "WSL Vitis/AIE compile failure with template deduction or type mismatch error on aie::load_v.",
    "tags": [
        "compile_error",
        "load_v",
        "pointer_type",
        "template_mismatch",
        "vector_load_store"
    ]
}

# Mapping of pointer types to mismatched alternatives
_TYPE_MISMATCHES = {
    "int32": "int16",
    "int16": "int32",
    "cint16": "float",
    "float": "cint16",
    "int8": "int16",
    "uint8": "int16",
    "int64": "int32",
    "uint16": "int32",
    "uint32": "int16",
    "cint32": "float",
    "cfloat": "int32",
}


def _is_kernel_source(path):
    """Heuristic: consider .cc, .cpp, .h, .hpp files as kernel sources."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cc', '.cpp', '.c', '.h', '.hpp', '.hxx', '.cxx'))


def _find_pointer_declarations(content):
    """Find pointer variable declarations with known AIE types."""
    # Match patterns like: int32* varname, const int32 * varname, etc.
    type_pattern = r'(?:const\s+)?(int8|uint8|int16|uint16|int32|uint32|int64|uint64|cint16|cint32|float|cfloat)\s*\*'
    return list(re.finditer(type_pattern, content))


def _find_load_v_calls(content):
    """Find aie::load_v<N>(...) calls and extract the pointer argument."""
    # Pattern: aie::load_v<NUMBER>(EXPR)
    pattern = r'aie::load_v\s*<\s*(\d+)\s*>\s*\(([^)]+)\)'
    return list(re.finditer(pattern, content))


def _resolve_pointer_type(content, var_name):
    """Try to find the declared type of a pointer variable in the content."""
    # Look for declarations like: type* var_name or type * var_name
    pattern = r'(?:const\s+)?(int8|uint8|int16|uint16|int32|uint32|int64|uint64|cint16|cint32|float|cfloat)\s*\*\s*' + re.escape(var_name.strip()) + r'\b'
    m = re.search(pattern, content)
    if m:
        return m.group(1)
    return None


def _find_cast_in_arg(arg):
    """Check if the argument contains a cast like (int32*)expr."""
    pattern = r'\(\s*(int8|uint8|int16|uint16|int32|uint32|int64|uint64|cint16|cint32|float|cfloat)\s*\*\s*\)'
    m = re.search(pattern, arg)
    if m:
        return m
    return None


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        load_v_calls = _find_load_v_calls(content)
        if not load_v_calls:
            continue

        for call_match in load_v_calls:
            vector_size = call_match.group(1)
            arg = call_match.group(2).strip()
            full_match_str = call_match.group(0)
            start_pos = call_match.start()
            end_pos = call_match.end()

            # Strategy 1: The argument contains an explicit cast like (int32*)ptr
            cast_match = _find_cast_in_arg(arg)
            if cast_match:
                original_type = cast_match.group(1)
                if original_type in _TYPE_MISMATCHES:
                    new_type = _TYPE_MISMATCHES[original_type]
                    # Build replacement for the full load_v call by replacing the cast type
                    original_fragment = full_match_str
                    # Replace within the argument portion
                    new_arg = arg[:cast_match.start()] + '(' + new_type + '*)' + arg[cast_match.end():]
                    replacement_fragment = f"aie::load_v<{vector_size}>({new_arg})"
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "load_v_mismatched_pointer_type",
                        "category": "vector_load_store",
                        "start": start_pos,
                        "end": end_pos,
                        "original": original_fragment,
                        "replacement": replacement_fragment,
                        "description": f"Changed cast from ({original_type}*) to ({new_type}*) in aie::load_v<{vector_size}> call, causing pointer type mismatch."
                    })
                continue

            # Strategy 2: The argument is a simple variable name; find its declared type
            # Strip any address-of or simple expressions
            simple_var = arg.strip()
            # Handle cases like &arr[0] or ptr + offset - just use the first identifier
            var_match = re.match(r'&?\s*([a-zA-Z_]\w*)', simple_var)
            if var_match:
                var_name = var_match.group(1)
                ptr_type = _resolve_pointer_type(content, var_name)
                if ptr_type and ptr_type in _TYPE_MISMATCHES:
                    new_type = _TYPE_MISMATCHES[ptr_type]
                    # Mutate by wrapping the argument with a mismatched cast
                    original_fragment = full_match_str
                    new_arg = f"({new_type}*){arg}"
                    replacement_fragment = f"aie::load_v<{vector_size}>({new_arg})"
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "load_v_mismatched_pointer_type",
                        "category": "vector_load_store",
                        "start": start_pos,
                        "end": end_pos,
                        "original": original_fragment,
                        "replacement": replacement_fragment,
                        "description": f"Added mismatched cast ({new_type}*) to pointer argument '{arg}' (declared as {ptr_type}*) in aie::load_v<{vector_size}> call."
                    })
                elif ptr_type is None:
                    # Can't resolve type, but we can still try a generic mutation
                    # Use int16 as a likely mismatch for unknown types
                    original_fragment = full_match_str
                    new_arg = f"(int16*){arg}"
                    replacement_fragment = f"aie::load_v<{vector_size}>({new_arg})"
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "load_v_mismatched_pointer_type",
                        "category": "vector_load_store",
                        "start": start_pos,
                        "end": end_pos,
                        "original": original_fragment,
                        "replacement": replacement_fragment,
                        "description": f"Added mismatched cast (int16*) to pointer argument '{arg}' in aie::load_v<{vector_size}> call to introduce type mismatch."
                    })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
