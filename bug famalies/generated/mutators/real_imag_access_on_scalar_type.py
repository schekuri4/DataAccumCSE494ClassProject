import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF273",
    "bug_type": "real_imag_access_on_scalar_type",
    "category": "complex_datatypes",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [".real", ".imag", "int32", "int16", "float"],
    "mutation_strategy": "Change a variable declaration from a complex type (cint16, cint32, cfloat) to its scalar counterpart (int16, int32, float) while leaving subsequent .real or .imag member accesses in place, causing a member-not-found compile error.",
    "repair_expectation": "Restore the variable type to the appropriate complex type (cint16, cint32, or cfloat) so that .real and .imag members are valid.",
    "validation_signal": "WSL Vitis/AIE compile failure with error indicating that scalar type has no member named 'real' or 'imag'.",
    "tags": ["cint16", "cint32", "complex_datatypes", "imag", "member_access", "real", "scalar_vs_complex"]
}

# Mapping from complex types to their scalar counterparts
COMPLEX_TO_SCALAR = {
    "cint16": "int16",
    "cint32": "int32",
    "cfloat": "float",
}

def _is_kernel_source(file_path):
    """Heuristic: consider .cpp, .cc, .c, .h, .hpp files as potential kernel sources."""
    lower = file_path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp'))


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to find variable declarations with complex types
    # Matches things like: cint16 varname, cint32 x, cfloat val
    # Also handles pointer/reference and multiple scenarios
    decl_pattern = re.compile(
        r'\b(cint16|cint32|cfloat)\b'
    )
    
    # Pattern to find .real or .imag access on a variable
    real_imag_pattern = re.compile(r'\b(\w+)\s*\.\s*(real|imag)\b')
    
    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue
        
        lines = content.split('\n')
        
        # First, find all variables that have .real or .imag accessed on them
        vars_with_member_access = set()
        for line in lines:
            for m in real_imag_pattern.finditer(line):
                vars_with_member_access.add(m.group(1))
        
        if not vars_with_member_access:
            continue
        
        # Now find declarations of complex types where the declared variable
        # is subsequently accessed with .real or .imag
        # Pattern: complex_type followed by variable name (with optional whitespace, *, &)
        var_decl_pattern = re.compile(
            r'\b(cint16|cint32|cfloat)\b(\s+[*&]?\s*)(\w+)'
        )
        
        for line_idx, line in enumerate(lines):
            for m in var_decl_pattern.finditer(line):
                complex_type = m.group(1)
                spacing = m.group(2)
                var_name = m.group(3)
                
                if var_name not in vars_with_member_access:
                    continue
                
                scalar_type = COMPLEX_TO_SCALAR[complex_type]
                
                # Calculate character positions in the full content
                line_start = sum(len(lines[i]) + 1 for i in range(line_idx))
                match_start = line_start + m.start(1)
                match_end = line_start + m.end(1)
                
                original_text = complex_type
                replacement_text = scalar_type
                
                candidate = {
                    "file_path": file_path,
                    "bug_type": "real_imag_access_on_scalar_type",
                    "category": "complex_datatypes",
                    "start": match_start,
                    "end": match_end,
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Change type '{complex_type}' to '{scalar_type}' for variable "
                        f"'{var_name}' at line {line_idx + 1}, leaving .real/.imag accesses "
                        f"intact to cause a compile error."
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
    actual_text = content[start:end]
    if actual_text != original:
        # Fallback: try to find and replace the first occurrence
        new_content = content.replace(original, replacement, 1)
    else:
        new_content = content[:start] + replacement + content[end:]
    
    new_files[file_path] = new_content
    return new_files
