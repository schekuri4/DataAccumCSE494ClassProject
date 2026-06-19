import re
import copy

BUG_FAMILY = {
    "family_id": "BF213",
    "bug_type": "interleave_mismatched_vector_sizes",
    "category": "vector_shuffles_and_permutations",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::interleave_zip",
        "aie::interleave_unzip",
        "::aie::interleave_zip",
        "::aie::interleave_unzip",
        "aie::vector<int32,16>",
        "aie::vector<int32,8>"
    ],
    "mutation_strategy": "Pass two vectors of different lane counts to aie::interleave_zip or aie::interleave_unzip (e.g., vector<int32,16> and vector<int32,8>), which requires matching sizes.",
    "repair_expectation": "Make both input vectors the same lane count and element type before calling interleave.",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert or template substitution failure about mismatched vector sizes.",
    "tags": ["interleave", "mismatch", "vector_shuffles_and_permutations", "vector_size", "zip"]
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, or .h files."""
    return path.endswith(('.cpp', '.cc', '.h', '.hpp'))


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to find calls to aie::interleave_zip or aie::interleave_unzip
    # with two arguments that are both vectors of the same size
    interleave_call_pattern = re.compile(
        r'((?:::)?aie::interleave_(?:zip|unzip))\s*\(\s*([A-Za-z_]\w*)\s*,\s*([A-Za-z_]\w*)\s*(?:,\s*[^)]*)?\)'
    )
    
    # Pattern to find vector declarations with lane counts
    vector_decl_pattern = re.compile(
        r'aie::vector\s*<\s*(\w+)\s*,\s*(\d+)\s*>\s+([A-Za-z_]\w*)'
    )
    
    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue
        
        # First, find all vector variable declarations and their types
        var_types = {}  # var_name -> (element_type, lane_count)
        for m in vector_decl_pattern.finditer(content):
            elem_type = m.group(1)
            lane_count = int(m.group(2))
            var_name = m.group(3)
            var_types[var_name] = (elem_type, lane_count)
        
        # Find interleave calls where both arguments have the same vector size
        for m in interleave_call_pattern.finditer(content):
            func_name = m.group(1)
            arg1 = m.group(2)
            arg2 = m.group(3)
            
            # Check if both args are known vectors with matching sizes
            if arg1 in var_types and arg2 in var_types:
                elem1, lanes1 = var_types[arg1]
                elem2, lanes2 = var_types[arg2]
                
                if elem1 == elem2 and lanes1 == lanes2:
                    # We'll mutate the declaration of arg2 to have a different lane count
                    # Find the declaration of arg2 and change its lane count
                    new_lanes = lanes2 // 2 if lanes2 > 4 else lanes2 * 2
                    
                    # Find the declaration match for arg2
                    decl_pattern_for_var = re.compile(
                        r'(aie::vector\s*<\s*' + re.escape(elem2) + r'\s*,\s*)' +
                        str(lanes2) +
                        r'(\s*>\s+' + re.escape(arg2) + r')'
                    )
                    
                    for decl_m in decl_pattern_for_var.finditer(content):
                        original = decl_m.group(0)
                        replacement = decl_m.group(1) + str(new_lanes) + decl_m.group(2)
                        
                        candidates.append({
                            "file_path": file_path,
                            "bug_type": "interleave_mismatched_vector_sizes",
                            "category": "vector_shuffles_and_permutations",
                            "start": decl_m.start(),
                            "end": decl_m.end(),
                            "original": original,
                            "replacement": replacement,
                            "description": (
                                f"Changed vector lane count of '{arg2}' from {lanes2} to {new_lanes} "
                                f"creating a size mismatch in {func_name}({arg1}, {arg2}) call. "
                                f"'{arg1}' remains aie::vector<{elem1},{lanes1}> while '{arg2}' "
                                f"becomes aie::vector<{elem2},{new_lanes}>."
                            )
                        })
                        break  # Only first declaration of this variable
        
        # Alternative strategy: if we find interleave calls but couldn't match
        # variable declarations, try to mutate the call itself by looking for
        # inline template arguments or find declarations with different patterns
        
        # Common fallback: interleave_zip(a, b, step). Make the second operand
        # a concatenated vector so its lane count no longer matches the first.
        generic_interleave_pattern = re.compile(
            r'((?:::)?aie::interleave_(?:zip|unzip)\s*\(\s*)'
            r'([A-Za-z_]\w*)'
            r'(\s*,\s*)'
            r'([A-Za-z_]\w*)'
            r'(\s*,\s*[^)]*\))'
        )
        for m in generic_interleave_pattern.finditer(content):
            original = m.group(0)
            replacement = (
                m.group(1) + m.group(2) + m.group(3) +
                "::aie::concat(" + m.group(4) + ", " + m.group(4) + ")" +
                m.group(5)
            )
            candidates.append({
                "file_path": file_path,
                "bug_type": "interleave_mismatched_vector_sizes",
                "category": "vector_shuffles_and_permutations",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    "Replace the second interleave operand with concat(arg, arg), "
                    "doubling its lanes and creating a vector size mismatch."
                )
            })

        # Also look for cases where vectors are declared and used in interleave
        # but with template syntax in the call itself
        template_interleave_pattern = re.compile(
            r'((?:::)?aie::interleave_(?:zip|unzip))\s*\(\s*'
            r'(aie::vector\s*<\s*(\w+)\s*,\s*(\d+)\s*>\s*\([^)]*\))\s*,\s*'
            r'(aie::vector\s*<\s*(\w+)\s*,\s*(\d+)\s*>\s*\([^)]*\))\s*\)'
        )
        
        for m in template_interleave_pattern.finditer(content):
            elem1 = m.group(3)
            lanes1 = int(m.group(4))
            elem2 = m.group(6)
            lanes2 = int(m.group(7))
            
            if elem1 == elem2 and lanes1 == lanes2:
                # Mutate the second vector's lane count
                new_lanes = lanes2 // 2 if lanes2 > 4 else lanes2 * 2
                second_vec_start = m.start(5)
                second_vec_end = m.end(5)
                original_second = m.group(5)
                replacement_second = re.sub(
                    r'(aie::vector\s*<\s*' + re.escape(elem2) + r'\s*,\s*)' + str(lanes2),
                    r'\g<1>' + str(new_lanes),
                    original_second,
                    count=1
                )
                
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "interleave_mismatched_vector_sizes",
                    "category": "vector_shuffles_and_permutations",
                    "start": second_vec_start,
                    "end": second_vec_end,
                    "original": original_second,
                    "replacement": replacement_second,
                    "description": (
                        f"Changed inline vector lane count from {lanes2} to {new_lanes} "
                        f"in second argument of {m.group(1)} call, creating size mismatch."
                    )
                })
    
    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]
    
    new_files = dict(project_files)
    content = new_files[file_path]
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
