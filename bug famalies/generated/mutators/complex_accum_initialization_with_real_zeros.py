import re
import copy

BUG_FAMILY = {
    "family_id": "BF284",
    "bug_type": "complex_accum_initialization_with_real_zeros",
    "category": "complex_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::zeros",
        "aie::accum",
        "cacc48",
        "cacc80",
        "cint16",
        "cint32"
    ],
    "mutation_strategy": "Initialize a complex accumulator (aie::accum<cacc48, N>) using aie::zeros<int32, N>() or aie::zeros<int16, N>() instead of aie::zeros<cint32, N>() or the correct complex zero initialization, causing a type conversion failure.",
    "repair_expectation": "Use the correct complex element type in aie::zeros (e.g., aie::zeros<cint16, N>()) or use the accumulator's default constructor.",
    "validation_signal": "WSL Vitis/AIE compile failure with cannot convert from real vector to complex accumulator.",
    "tags": [
        "accumulator",
        "complex",
        "complex_intrinsics",
        "initialization",
        "type_mismatch",
        "zeros"
    ]
}


def _is_kernel_source(filepath):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = filepath.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern 1: aie::zeros<cint16, N>() or aie::zeros<cint32, N>() - replace complex type with real type
    # Matches aie::zeros<cint16, ...>() or aie::zeros<cint32, ...>()
    zeros_pattern = re.compile(
        r'(?:::)?aie::zeros\s*<\s*(cint16|cint32)\s*,\s*([^>]+)\s*>\s*\(\s*\)'
    )
    
    # Pattern 2: Look for complex accumulator declarations that use aie::zeros with complex types
    # Also match patterns where accum is initialized with zeros
    accum_zeros_pattern = re.compile(
        r'((?:::)?aie::zeros\s*<\s*)(cint16|cint32)(\s*,\s*[^>]+\s*>\s*\(\s*\))'
    )

    legacy_cacc_init_pattern = re.compile(
        r'(\bv([48]|16)cacc48\s+\w+\s*=\s*)'
        r'(undef_v\2cacc48|null_v\2cacc48)\s*\(\s*\)'
    )
    
    for filepath, content in project_files.items():
        if not _is_kernel_source(filepath):
            continue
        
        # Check if file has any relevance (contains accumulator or complex types)
        has_relevance = any(target in content for target in ['cacc48', 'cacc80', 'aie::accum', 'cint16', 'cint32'])
        if not has_relevance and 'aie::zeros' not in content:
            continue
        
        for match in accum_zeros_pattern.finditer(content):
            original_text = match.group(0)
            complex_type = match.group(2)
            
            # Map complex type to its real counterpart
            if complex_type == 'cint16':
                real_type = 'int16'
            elif complex_type == 'cint32':
                real_type = 'int32'
            else:
                continue
            
            replacement_text = match.group(1) + real_type + match.group(3)
            
            start = match.start()
            end = match.end()
            
            candidates.append({
                "file_path": filepath,
                "bug_type": "complex_accum_initialization_with_real_zeros",
                "category": "complex_intrinsics",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Replace aie::zeros<{complex_type}, N>() with aie::zeros<{real_type}, N>() "
                    f"causing a type mismatch when initializing a complex accumulator with a real vector."
                )
            })

        for match in legacy_cacc_init_pattern.finditer(content):
            lane = match.group(2)
            original_text = match.group(0)
            replacement_text = match.group(1) + f"null_v{lane}acc48()"
            candidates.append({
                "file_path": filepath,
                "bug_type": "complex_accum_initialization_with_real_zeros",
                "category": "complex_intrinsics",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    "Initialize a legacy complex accumulator with the real "
                    f"accumulator zero factory null_v{lane}acc48()."
                )
            })
    
    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    
    filepath = candidate["file_path"]
    content = new_files[filepath]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    
    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)
    
    new_files[filepath] = new_content
    return new_files
