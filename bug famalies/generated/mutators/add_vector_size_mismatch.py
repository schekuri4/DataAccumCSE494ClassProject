import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF253",
    "bug_type": "add_vector_size_mismatch",
    "category": "arithmetic_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::add",
        "aie::vector<int32, 8>",
        "aie::vector<int32, 16>",
        "aie::vector<int16, 32>"
    ],
    "mutation_strategy": "Change one operand of aie::add to have a different lane count than the other operand (e.g., add a vector<int32,8> to a vector<int32,16>), creating a size mismatch that prevents template instantiation.",
    "repair_expectation": "Make both operands the same vector size (lane count) so the aie::add template can be instantiated.",
    "validation_signal": "WSL Vitis/AIE aiecompiler emits a compile-time error about mismatched vector sizes or failed static_assert on vector dimensions.",
    "tags": ["add", "arithmetic_intrinsics", "lane_mismatch", "template_error", "vector_size"]
}

# Map of lane counts to alternative lane counts for creating mismatches
_LANE_ALTERNATIVES = {
    "8": "16",
    "16": "8",
    "32": "16",
    "64": "32",
    "4": "8",
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, or .h files likely containing AIE code."""
    return any(path.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find aie::vector declarations that are used as operands in aie::add calls
        # and mutate the vector type declaration to have a different lane count.
        
        # First, find all aie::add calls and try to identify operand variables
        # Strategy 2: Find aie::vector type declarations/definitions and change lane count
        # to create mismatch with aie::add usage.
        
        # Look for aie::add calls
        add_pattern = re.compile(r'aie::add\s*\(([^)]+)\)')
        for add_match in add_pattern.finditer(content):
            add_start = add_match.start()
            add_end = add_match.end()
            args_str = add_match.group(1)
            
            # Try to find the operand variable declarations with aie::vector types
            # Split args (simple split by comma, may not handle nested templates perfectly)
            args = [a.strip() for a in args_str.split(',')]
            if len(args) < 2:
                continue
            
            # For each operand, search for its declaration with aie::vector type
            for arg_idx, arg_name in enumerate(args[:2]):
                # arg_name might be a simple variable name or an expression
                # Try simple variable name
                var_name = arg_name.strip()
                # Skip if it's a complex expression
                if not re.match(r'^[a-zA-Z_]\w*$', var_name):
                    continue
                
                # Search for declaration of this variable with aie::vector type
                decl_pattern = re.compile(
                    r'(aie::vector\s*<\s*(\w+)\s*,\s*(\d+)\s*>)\s+' + re.escape(var_name) + r'\b'
                )
                for decl_match in decl_pattern.finditer(content):
                    full_type = decl_match.group(1)
                    elem_type = decl_match.group(2)
                    lane_count = decl_match.group(3)
                    
                    if lane_count not in _LANE_ALTERNATIVES:
                        continue
                    
                    new_lane_count = _LANE_ALTERNATIVES[lane_count]
                    original = full_type
                    replacement = re.sub(
                        r'(aie::vector\s*<\s*' + re.escape(elem_type) + r'\s*,\s*)' + re.escape(lane_count) + r'(\s*>)',
                        r'\g<1>' + new_lane_count + r'\2',
                        full_type
                    )
                    
                    if original == replacement:
                        continue
                    
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "add_vector_size_mismatch",
                        "category": "arithmetic_intrinsics",
                        "start": decl_match.start(1),
                        "end": decl_match.end(1),
                        "original": original,
                        "replacement": replacement,
                        "description": (
                            f"Changed lane count of '{var_name}' from {lane_count} to {new_lane_count} "
                            f"to create a vector size mismatch in aie::add call."
                        )
                    })

        # Strategy 3: Find aie::vector type annotations in any context near aie::add
        # Look for patterns like aie::vector<type, N> used in variable declarations
        # where the variable is likely an operand of aie::add
        if 'aie::add' in content:
            vec_decl_pattern = re.compile(
                r'(aie::vector\s*<\s*(\w+)\s*,\s*(\d+)\s*>)\s+(\w+)'
            )
            for vec_match in vec_decl_pattern.finditer(content):
                full_type = vec_match.group(1)
                elem_type = vec_match.group(2)
                lane_count = vec_match.group(3)
                var_name = vec_match.group(4)
                
                if lane_count not in _LANE_ALTERNATIVES:
                    continue
                
                # Check if this variable is used in an aie::add call
                usage_pattern = re.compile(
                    r'aie::add\s*\([^)]*\b' + re.escape(var_name) + r'\b[^)]*\)'
                )
                if not usage_pattern.search(content):
                    continue
                
                new_lane_count = _LANE_ALTERNATIVES[lane_count]
                original = full_type
                replacement = re.sub(
                    r'(aie::vector\s*<\s*' + re.escape(elem_type) + r'\s*,\s*)' + re.escape(lane_count) + r'(\s*>)',
                    r'\g<1>' + new_lane_count + r'\2',
                    full_type
                )
                
                if original == replacement:
                    continue
                
                # Avoid duplicates
                cand = {
                    "file_path": file_path,
                    "bug_type": "add_vector_size_mismatch",
                    "category": "arithmetic_intrinsics",
                    "start": vec_match.start(1),
                    "end": vec_match.end(1),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Changed lane count of '{var_name}' from {lane_count} to {new_lane_count} "
                        f"to create a vector size mismatch in aie::add call."
                    )
                }
                # Check for duplicates based on start position
                if not any(c["file_path"] == cand["file_path"] and c["start"] == cand["start"] for c in candidates):
                    candidates.append(cand)

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
