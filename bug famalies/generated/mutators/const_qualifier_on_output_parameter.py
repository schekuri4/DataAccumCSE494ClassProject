import re
import copy

BUG_FAMILY = {
    "family_id": "BF034",
    "bug_type": "const_qualifier_on_output_parameter",
    "category": "kernel_prototypes_and_signatures",
    "target_files": ["kernel header", "kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["output_window<", "output_stream<", "output_buffer<", "const"],
    "mutation_strategy": "Add a const qualifier to an output window/stream/buffer pointer parameter in the kernel declaration, e.g., change 'output_window<int32>* out' to 'const output_window<int32>* out', making writes to the output illegal.",
    "repair_expectation": "Remove the const qualifier from the output parameter so the kernel can write to it.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about writing to a const-qualified pointer or type mismatch in port binding.",
    "tags": ["constness", "kernel_prototypes_and_signatures", "output_parameter", "qualifier_error"]
}

# Pattern to match output_window/output_stream/output_buffer parameters that are NOT already const-qualified
# Matches things like: output_window<int32>* out  or  output_stream<cint16> * data_out
_OUTPUT_PARAM_PATTERN = re.compile(
    r'(?<!const\s)(?<!const)'  # negative lookbehind (limited, we'll filter in code)
    r'(output_window|output_stream|output_buffer)'
    r'\s*<\s*[^>]+\s*>'        # template argument
    r'\s*\*'                   # pointer star
)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []
    
    # Target file extensions typical for kernel headers and sources
    target_extensions = ('.h', '.hpp', '.hh', '.cc', '.cpp', '.c')
    
    for file_path, content in project_files.items():
        if not any(file_path.endswith(ext) for ext in target_extensions):
            continue
        
        # Look for output parameter patterns that are not already const-qualified
        for match in re.finditer(
            r'(output_window|output_stream|output_buffer)\s*<\s*[^>]+\s*>\s*\*',
            content
        ):
            start = match.start()
            end = match.end()
            original = match.group(0)
            
            # Check if already preceded by 'const' (with optional whitespace)
            # Look at the text before the match
            prefix_region = content[max(0, start - 20):start]
            if re.search(r'const\s*$', prefix_region):
                continue  # Already const-qualified, skip
            
            replacement = "const " + original
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "const_qualifier_on_output_parameter",
                "category": "kernel_prototypes_and_signatures",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Add 'const' qualifier to output parameter '{original}' "
                    f"in {file_path}, making writes to the output illegal."
                )
            })
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
