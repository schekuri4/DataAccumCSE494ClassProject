import re
import copy

BUG_FAMILY = {
    "family_id": "BF133",
    "bug_type": "begin_vector_lane_count_mismatch",
    "category": "buffer_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "begin_vector<8>",
        "begin_vector<16>",
        "begin_vector<4>",
        "begin_restrict_vector<"
    ],
    "mutation_strategy": "Change the lane count template argument of begin_vector or begin_restrict_vector to a value incompatible with the data type's vector register width (e.g., begin_vector<16> on int32 buffer when architecture only supports 8-lane vectors for that type, or use begin_vector<3> which is not a power of two).",
    "repair_expectation": "Correct the lane count to a valid power-of-two value supported by the AIE architecture for the given data type (e.g., 4, 8, or 16 as appropriate).",
    "validation_signal": "WSL Vitis/AIE compile failure with invalid vector size or unsupported lane count error.",
    "tags": [
        "aie_arch",
        "begin_vector",
        "buffer_interfaces",
        "lane_count",
        "vector_register"
    ]
}

# Pattern matches begin_vector<N> or begin_restrict_vector<N>
_PATTERN = re.compile(r'(begin_(?:restrict_)?vector)\s*<\s*(\d+)\s*>')

# Mapping from original lane count to a mismatched replacement
# We pick values that are either not power-of-two or incompatible sizes
_MISMATCH_MAP = {
    4: 3,    # not a power of two
    8: 3,    # not a power of two
    16: 3,   # not a power of two
    32: 3,   # not a power of two
}


def _get_replacement_lane_count(original_count):
    """Return an invalid/mismatched lane count for the given original."""
    if original_count in _MISMATCH_MAP:
        return _MISMATCH_MAP[original_count]
    # For any other value, use 3 (not power of two) if original != 3,
    # otherwise use 5
    if original_count == 3:
        return 5
    return 3


def _is_kernel_source(file_path):
    """Heuristic to identify kernel source files for AIE."""
    lower = file_path.lower()
    # Common AIE kernel source extensions and patterns
    if any(lower.endswith(ext) for ext in ['.cc', '.cpp', '.c', '.h', '.hpp']):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []
    
    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue
        
        for match in _PATTERN.finditer(content):
            func_name = match.group(1)
            lane_count = int(match.group(2))
            
            replacement_count = _get_replacement_lane_count(lane_count)
            
            original_text = match.group(0)
            # Reconstruct replacement with new lane count
            replacement_text = f"{func_name}<{replacement_count}>"
            
            start = match.start()
            end = match.end()
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "begin_vector_lane_count_mismatch",
                "category": "buffer_interfaces",
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed lane count from {lane_count} to {replacement_count} "
                    f"in {func_name}<> call, introducing an incompatible vector "
                    f"lane count for the AIE architecture."
                )
            })
    
    return candidates


def apply_mutation(project_files, candidate):
    mutated_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = mutated_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: try to find and replace first occurrence
        mutated_content = content.replace(original, replacement, 1)
    else:
        mutated_content = content[:start] + replacement + content[end:]
    
    mutated_files[file_path] = mutated_content
    return mutated_files
