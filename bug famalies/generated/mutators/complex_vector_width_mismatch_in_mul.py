import re
import copy

BUG_FAMILY = {
    "family_id": "BF283",
    "bug_type": "complex_vector_width_mismatch_in_mul",
    "category": "complex_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::vector<cint16,",
        "aie::vector<cint32,",
        "aie::mul",
        "aie::mac"
    ],
    "mutation_strategy": "Change the vector width of one operand in a complex multiply from the correct lane count (e.g., aie::vector<cint16, 8>) to a mismatched width (e.g., aie::vector<cint16, 4> or aie::vector<cint16, 16>) that has no valid intrinsic mapping for the given operation.",
    "repair_expectation": "Restore the vector width to the architecturally supported lane count for the complex multiply intrinsic (e.g., 8 lanes for cint16 x cint16).",
    "validation_signal": "WSL Vitis/AIE compile failure with no matching function or static_assert on vector size.",
    "tags": ["complex", "complex_intrinsics", "intrinsics", "lanes", "mul", "vector_width"]
}

# Typical supported widths for complex types in AIE mul/mac
_SUPPORTED_WIDTHS = {
    "cint16": [8, 16],
    "cint32": [4, 8],
}

# Alternative (mismatched) widths to inject
_MISMATCH_OPTIONS = {
    4: [2, 8],
    8: [4, 16],
    16: [8, 32],
    32: [16, 64],
    2: [1, 4],
}


def _pick_mismatch(width):
    """Pick a mismatched width that differs from the original."""
    options = _MISMATCH_OPTIONS.get(width, [])
    if options:
        # Prefer halving
        return options[0]
    # Fallback: double or halve
    if width > 1:
        return width // 2
    return width * 2


def _file_is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hh'))


def _has_mul_or_mac_context(content):
    """Check if file contains aie::mul or aie::mac usage."""
    return 'aie::mul' in content or 'aie::mac' in content


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match aie::vector<cint16, N> or aie::vector<cint32, N>
    vec_pattern = re.compile(
        r'(aie::vector\s*<\s*(cint16|cint32)\s*,\s*)(\d+)(\s*>)'
    )
    
    for file_path, content in project_files.items():
        if not _file_is_kernel_source(file_path):
            continue
        
        # File must contain mul/mac operations to be relevant
        if not _has_mul_or_mac_context(content):
            continue
        
        lines = content.split('\n')
        offset = 0
        
        for line_idx, line in enumerate(lines):
            # Check if this line or nearby lines involve mul/mac
            # Look within a window of lines for context
            context_start = max(0, line_idx - 5)
            context_end = min(len(lines), line_idx + 6)
            context_block = '\n'.join(lines[context_start:context_end])
            
            has_mul_mac_nearby = ('aie::mul' in context_block or 'aie::mac' in context_block)
            
            for match in vec_pattern.finditer(line):
                if not has_mul_mac_nearby:
                    continue
                
                prefix = match.group(1)
                ctype = match.group(2)
                width_str = match.group(3)
                suffix = match.group(4)
                width = int(width_str)
                
                mismatch_width = _pick_mismatch(width)
                
                original_text = match.group(0)
                replacement_text = prefix + str(mismatch_width) + suffix
                
                abs_start = offset + match.start()
                abs_end = offset + match.end()
                
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "complex_vector_width_mismatch_in_mul",
                    "category": "complex_intrinsics",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Changed complex vector width from {width} to {mismatch_width} "
                        f"for {ctype} in mul/mac context, creating an unsupported lane count."
                    )
                })
            
            offset += len(line) + 1  # +1 for newline
    
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
    actual = content[start:end]
    if actual != original:
        # Fallback: try to find and replace first occurrence
        new_content = content.replace(original, replacement, 1)
    else:
        new_content = content[:start] + replacement + content[end:]
    
    new_files[file_path] = new_content
    return new_files
