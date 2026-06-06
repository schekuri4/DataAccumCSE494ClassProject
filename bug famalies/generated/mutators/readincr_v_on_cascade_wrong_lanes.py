import re
import copy

BUG_FAMILY = {
    "family_id": "BF145",
    "bug_type": "readincr_v_on_cascade_wrong_lanes",
    "category": "cascade_streams",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v",
        "readincr_v<8>",
        "readincr_v<4>",
        "readincr_v<16>",
        "input_cascade"
    ],
    "mutation_strategy": "Change the lane count template parameter of readincr_v (e.g., from readincr_v<8>(cascin) to readincr_v<4>(cascin) or readincr_v<16>(cascin)) on a cascade input, producing a lane count that is incompatible with the cascade data width for the given element type.",
    "repair_expectation": "Restore the correct lane count in readincr_v that matches the cascade stream width for the data type (e.g., 8 lanes for int32 on a 384-bit cascade).",
    "validation_signal": "WSL Vitis/AIE compile failure with invalid vector size or readincr_v template instantiation failure from aiecompiler.",
    "tags": [
        "cascade",
        "cascade_streams",
        "intrinsic",
        "lanes",
        "readincr_v",
        "vector_size"
    ]
}

# Possible lane counts for readincr_v on cascade streams
VALID_LANE_COUNTS = [4, 8, 16, 32]


def _is_kernel_source(file_path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = file_path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx'))


def _pick_replacement_lanes(original_lanes):
    """Pick a different lane count that would be incompatible."""
    original_int = int(original_lanes)
    # Prefer specific wrong values based on original
    candidates = [lc for lc in VALID_LANE_COUNTS if lc != original_int]
    if not candidates:
        # Fallback: just double or halve
        if original_int * 2 <= 64:
            return str(original_int * 2)
        else:
            return str(original_int // 2)
    # Pick the first candidate that differs meaningfully
    # Prefer halving for 8->4, doubling for 4->8, etc.
    if original_int == 8:
        return "4"
    elif original_int == 4:
        return "16"
    elif original_int == 16:
        return "8"
    elif original_int == 32:
        return "16"
    return str(candidates[0])


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern: readincr_v<N>(...) where N is a number
    # We look for readincr_v with explicit template parameter
    pattern_explicit = re.compile(
        r'(readincr_v\s*<\s*)(\d+)(\s*>\s*\()'
    )
    
    # Pattern: readincr_v(...) without explicit template (we can add a wrong one)
    # But mutation_strategy says "change" so we focus on explicit ones
    
    # Also match readincr_v<N, type>(...) patterns
    pattern_with_type = re.compile(
        r'(readincr_v\s*<\s*)(\d+)(\s*,\s*[^>]*>\s*\()'
    )
    
    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue
        
        # Check if file has any cascade-related content (relaxed: any readincr_v is likely on cascade)
        has_cascade_hint = ('cascade' in content.lower() or 
                           'casc' in content.lower() or
                           'input_cascade' in content or
                           'acc48' in content or
                           'acc80' in content)
        
        # Search for explicit template parameter patterns
        for pattern in [pattern_explicit, pattern_with_type]:
            for match in pattern.finditer(content):
                original_lanes = match.group(2)
                
                # Only mutate if it looks like it could be cascade-related
                # Check surrounding context for cascade indicators
                line_start = content.rfind('\n', 0, match.start()) + 1
                line_end = content.find('\n', match.end())
                if line_end == -1:
                    line_end = len(content)
                line_content = content[line_start:line_end]
                
                # Relaxed check: if file has cascade hints or line has cascade-like args
                is_cascade_context = (has_cascade_hint or 
                                      'casc' in line_content.lower() or
                                      'cascade' in line_content.lower())
                
                if not is_cascade_context:
                    # Still include if readincr_v is present (it's primarily used with cascades)
                    # readincr_v is specifically a cascade intrinsic in AIE
                    pass
                
                replacement_lanes = _pick_replacement_lanes(original_lanes)
                
                full_original = match.group(0)
                full_replacement = match.group(1) + replacement_lanes + match.group(3)
                
                candidate = {
                    "file_path": file_path,
                    "bug_type": "readincr_v_on_cascade_wrong_lanes",
                    "category": "cascade_streams",
                    "start": match.start(),
                    "end": match.end(),
                    "original": full_original,
                    "replacement": full_replacement,
                    "description": (
                        f"Changed readincr_v lane count from <{original_lanes}> to "
                        f"<{replacement_lanes}>, producing incompatible vector size "
                        f"for cascade data width."
                    )
                }
                candidates.append(candidate)
    
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
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
