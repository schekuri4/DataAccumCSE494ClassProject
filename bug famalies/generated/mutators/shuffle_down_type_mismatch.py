import re
import copy

BUG_FAMILY = {
    "family_id": "BF212",
    "bug_type": "shuffle_down_type_mismatch",
    "category": "vector_shuffles_and_permutations",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::shuffle_down",
        "aie::vector<cint16,",
        "aie::vector<int16,"
    ],
    "mutation_strategy": "Call aie::shuffle_down with two vector arguments of different element types (e.g., one cint16 vector and one int16 vector), causing a type deduction failure in the template.",
    "repair_expectation": "Ensure both vector arguments to shuffle_down have the same element type and lane count.",
    "validation_signal": "WSL Vitis/AIE compile failure with template argument deduction or type mismatch error.",
    "tags": [
        "cint16",
        "int16",
        "shuffle_down",
        "type_mismatch",
        "vector_shuffles_and_permutations"
    ]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []
    
    # Pattern to match aie::shuffle_down calls with two vector arguments of the same type
    # We look for shuffle_down calls where both arguments appear to be the same vector type
    # and we can change one argument's declaration or the call itself
    
    # Strategy 1: Find shuffle_down calls and try to introduce type mismatch
    # by changing one of the vector arguments in the call
    shuffle_pattern = re.compile(
        r'(aie::shuffle_down\s*\(\s*)([a-zA-Z_]\w*)(\s*,\s*)([a-zA-Z_]\w*)(\s*,\s*[^)]+\))'
    )
    
    # Also look for vector declarations to understand types
    vec_decl_pattern = re.compile(
        r'(aie::vector<\s*(cint16|int16)\s*,\s*(\d+)\s*>\s+)([a-zA-Z_]\w*)'
    )
    
    for file_path, content in project_files.items():
        # Only consider kernel source files (typically .cc, .cpp, .h files)
        if not any(file_path.endswith(ext) for ext in ['.cc', '.cpp', '.h', '.hpp', '.c']):
            continue
        
        # Check if file contains relevant match targets
        has_shuffle_down = 'aie::shuffle_down' in content
        has_cint16_vec = 'aie::vector<cint16,' in content
        has_int16_vec = 'aie::vector<int16,' in content
        
        if not has_shuffle_down:
            continue
        
        # Build a map of variable names to their vector element types
        var_types = {}
        for m in vec_decl_pattern.finditer(content):
            elem_type = m.group(2)
            var_name = m.group(4)
            lanes = m.group(3)
            var_types[var_name] = (elem_type, lanes)
        
        # Find shuffle_down calls
        for m in shuffle_pattern.finditer(content):
            prefix = m.group(1)
            arg1 = m.group(2)
            separator = m.group(3)
            arg2 = m.group(4)
            suffix = m.group(5)
            
            full_match = m.group(0)
            start = m.start()
            end = m.end()
            
            # Case 1: Both args are known and same type - introduce mismatch
            if arg1 in var_types and arg2 in var_types:
                type1, lanes1 = var_types[arg1]
                type2, lanes2 = var_types[arg2]
                
                if type1 == type2:
                    # We need to find a variable of a different type, or create a cast
                    # Find a variable of the opposite type
                    opposite_type = 'int16' if type1 == 'cint16' else 'cint16'
                    opposite_vars = [v for v, (t, l) in var_types.items() if t == opposite_type]
                    
                    if opposite_vars:
                        # Replace second argument with a variable of different type
                        replacement_var = opposite_vars[0]
                        replacement = prefix + arg1 + separator + replacement_var + suffix
                        candidates.append({
                            "file_path": file_path,
                            "bug_type": "shuffle_down_type_mismatch",
                            "category": "vector_shuffles_and_permutations",
                            "start": start,
                            "end": end,
                            "original": full_match,
                            "replacement": replacement,
                            "description": f"Replace second argument '{arg2}' (type {type2}) with '{replacement_var}' (type {opposite_type}) in aie::shuffle_down call to introduce type mismatch."
                        })
                    else:
                        # No opposite-type variable exists; cast inline
                        # Replace the second arg with a reinterpret or explicit different-type vector
                        opposite_type = 'int16' if type1 == 'cint16' else 'cint16'
                        # Adjust lanes for type size difference
                        if type1 == 'cint16' and opposite_type == 'int16':
                            new_lanes = str(int(lanes1) * 2)
                        elif type1 == 'int16' and opposite_type == 'cint16':
                            new_lanes = str(int(lanes1) // 2) if int(lanes1) >= 2 else lanes1
                        else:
                            new_lanes = lanes1
                        
                        # Use same lanes to make it a clear type mismatch
                        cast_expr = f"aie::vector<{opposite_type},{lanes1}>()"
                        replacement = prefix + arg1 + separator + cast_expr + suffix
                        candidates.append({
                            "file_path": file_path,
                            "bug_type": "shuffle_down_type_mismatch",
                            "category": "vector_shuffles_and_permutations",
                            "start": start,
                            "end": end,
                            "original": full_match,
                            "replacement": replacement,
                            "description": f"Replace second argument '{arg2}' with a default-constructed aie::vector<{opposite_type},{lanes1}> in aie::shuffle_down to introduce type mismatch."
                        })
            
            elif arg1 in var_types:
                # Only first arg type is known, replace second with mismatched type
                type1, lanes1 = var_types[arg1]
                opposite_type = 'int16' if type1 == 'cint16' else 'cint16'
                cast_expr = f"aie::vector<{opposite_type},{lanes1}>()"
                replacement = prefix + arg1 + separator + cast_expr + suffix
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shuffle_down_type_mismatch",
                    "category": "vector_shuffles_and_permutations",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": f"Replace second argument '{arg2}' with aie::vector<{opposite_type},{lanes1}>() in aie::shuffle_down to introduce type mismatch."
                })
            
            elif arg2 in var_types:
                # Only second arg type is known, replace first with mismatched type
                type2, lanes2 = var_types[arg2]
                opposite_type = 'int16' if type2 == 'cint16' else 'cint16'
                cast_expr = f"aie::vector<{opposite_type},{lanes2}>()"
                replacement = prefix + cast_expr + separator + arg2 + suffix
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shuffle_down_type_mismatch",
                    "category": "vector_shuffles_and_permutations",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": f"Replace first argument '{arg1}' with aie::vector<{opposite_type},{lanes2}>() in aie::shuffle_down to introduce type mismatch."
                })
            else:
                # Neither arg type is known from declarations; use a default mutation
                # Assume cint16 with 16 lanes as a reasonable default
                if has_cint16_vec:
                    # File uses cint16, so introduce int16
                    cast_expr = "aie::vector<int16,16>()"
                elif has_int16_vec:
                    cast_expr = "aie::vector<cint16,8>()"
                else:
                    cast_expr = "aie::vector<int16,16>()"
                
                replacement = prefix + arg1 + separator + cast_expr + suffix
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shuffle_down_type_mismatch",
                    "category": "vector_shuffles_and_permutations",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": f"Replace second argument '{arg2}' with {cast_expr} in aie::shuffle_down to introduce type mismatch."
                })
    
    # Strategy 2: If no shuffle_down call pattern matched but file has shuffle_down,
    # try a more general regex for template-style calls
    if not candidates:
        # Try a broader pattern that handles template arguments in the call
        broad_pattern = re.compile(
            r'(aie::shuffle_down\s*(?:<[^>]*>\s*)?\(\s*)'
            r'([^,]+?)'
            r'(\s*,\s*)'
            r'([^,]+?)'
            r'(\s*,\s*[^)]+\))'
        )
        
        for file_path, content in project_files.items():
            if not any(file_path.endswith(ext) for ext in ['.cc', '.cpp', '.h', '.hpp', '.c']):
                continue
            if 'aie::shuffle_down' not in content:
                continue
            
            for m in broad_pattern.finditer(content):
                full_match = m.group(0)
                prefix = m.group(1)
                arg1 = m.group(2).strip()
                separator = m.group(3)
                arg2 = m.group(4).strip()
                suffix = m.group(5)
                start = m.start()
                end = m.end()
                
                # Determine what type to inject based on file content
                if 'cint16' in content:
                    mismatch_expr = "aie::vector<int16,16>()"
                    desc_type = "int16"
                else:
                    mismatch_expr = "aie::vector<cint16,8>()"
                    desc_type = "cint16"
                
                replacement = prefix + arg1 + separator + mismatch_expr + suffix
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shuffle_down_type_mismatch",
                    "category": "vector_shuffles_and_permutations",
                    "start": start,
                    "end": end,
                    "original": full_match,
                    "replacement": replacement,
                    "description": f"Replace second argument in aie::shuffle_down with {mismatch_expr} to introduce type mismatch between vector element types."
                })
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fall back to string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
