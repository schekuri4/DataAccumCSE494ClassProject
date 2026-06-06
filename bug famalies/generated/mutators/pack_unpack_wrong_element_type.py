import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF215",
    "bug_type": "pack_unpack_wrong_element_type",
    "category": "vector_shuffles_and_permutations",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::pack",
        "aie::unpack",
        "aie::vector<int32,",
        "aie::vector<int8,"
    ],
    "mutation_strategy": "Call aie::pack on a vector whose element type does not support packing (e.g., pack an int32 vector expecting int8 output without proper template parameters), or call aie::unpack with an incompatible target type that is not a valid widening of the source type.",
    "repair_expectation": "Use the correct source/destination type pair for pack/unpack (e.g., int16->int8 for pack, int8->int16 for unpack) with matching lane counts.",
    "validation_signal": "WSL Vitis/AIE compile failure with no matching function or invalid template specialization for pack/unpack.",
    "tags": [
        "element_type",
        "narrowing",
        "pack",
        "unpack",
        "vector_shuffles_and_permutations"
    ]
}


def _is_kernel_source(path: str) -> bool:
    """Heuristic: consider .cpp, .cc, .h, .hpp files as potential kernel sources."""
    return any(path.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Mutate aie::pack calls - change the source vector type
        # Look for patterns like aie::pack(some_vector) where the vector is declared
        # with a packable type (int16) and change it to int32 (not directly packable to int8)
        
        # Find aie::pack calls and try to corrupt the input vector's type declaration
        pack_call_pattern = re.compile(r'aie::pack\s*(<[^>]*>)?\s*\(([^)]+)\)')
        for m in pack_call_pattern.finditer(content):
            start = m.start()
            end = m.end()
            original = m.group(0)
            
            # Mutate: if there's a template parameter, corrupt it
            # If pack<int8>(...), change to pack<int32>(...)
            if m.group(1):
                template_part = m.group(1)
                # Replace int8 with int32 or int16 with int32 in template
                new_template = template_part
                if 'int8' in template_part:
                    new_template = template_part.replace('int8', 'int32')
                elif 'int16' in template_part:
                    new_template = template_part.replace('int16', 'int32')
                else:
                    new_template = template_part.replace(template_part.strip('<>').strip(), 'int32')
                
                if new_template != template_part:
                    replacement = original.replace(template_part, new_template)
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "pack_unpack_wrong_element_type",
                        "category": "vector_shuffles_and_permutations",
                        "start": start,
                        "end": end,
                        "original": original,
                        "replacement": replacement,
                        "description": f"Changed pack template type to incompatible int32: '{original}' -> '{replacement}'"
                    })
            else:
                # No template param on pack - wrap with wrong template
                replacement = original.replace('aie::pack(', 'aie::pack<int32>(')
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "pack_unpack_wrong_element_type",
                    "category": "vector_shuffles_and_permutations",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": f"Added incompatible int32 template to pack call: '{original}' -> '{replacement}'"
                })

        # Strategy 2: Mutate aie::unpack calls
        unpack_call_pattern = re.compile(r'aie::unpack\s*(<[^>]*>)?\s*\(([^)]+)\)')
        for m in unpack_call_pattern.finditer(content):
            start = m.start()
            end = m.end()
            original = m.group(0)
            
            if m.group(1):
                template_part = m.group(1)
                new_template = template_part
                if 'int16' in template_part:
                    new_template = template_part.replace('int16', 'int8')
                elif 'int32' in template_part:
                    new_template = template_part.replace('int32', 'int8')
                else:
                    new_template = template_part.replace(template_part.strip('<>').strip(), 'int8')
                
                if new_template != template_part:
                    replacement = original.replace(template_part, new_template)
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "pack_unpack_wrong_element_type",
                        "category": "vector_shuffles_and_permutations",
                        "start": start,
                        "end": end,
                        "original": original,
                        "replacement": replacement,
                        "description": f"Changed unpack template to incompatible narrower type: '{original}' -> '{replacement}'"
                    })
            else:
                replacement = original.replace('aie::unpack(', 'aie::unpack<int8>(')
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "pack_unpack_wrong_element_type",
                    "category": "vector_shuffles_and_permutations",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": f"Added incompatible int8 template to unpack call: '{original}' -> '{replacement}'"
                })

        # Strategy 3: Find vector declarations used near pack/unpack and change their element type
        # Look for aie::vector<int16, N> or aie::vector<int8, N> near pack/unpack usage
        # and change int16 to int32 to make pack invalid
        
        # Check if file contains pack or unpack
        has_pack = 'aie::pack' in content
        has_unpack = 'aie::unpack' in content
        
        if has_pack:
            # Find int16 vectors that might be pack sources and change to int32
            vec_pattern = re.compile(r'aie::vector<\s*int16\s*,\s*(\d+)\s*>')
            for m in vec_pattern.finditer(content):
                start = m.start()
                end = m.end()
                original = m.group(0)
                lanes = m.group(1)
                replacement = f'aie::vector<int32, {lanes}>'
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "pack_unpack_wrong_element_type",
                    "category": "vector_shuffles_and_permutations",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": f"Changed vector element type from int16 to int32 making pack incompatible: '{original}' -> '{replacement}'"
                })

        if has_unpack:
            # Find int8 vectors used with unpack and change to int32 (invalid unpack source)
            vec_pattern = re.compile(r'aie::vector<\s*int8\s*,\s*(\d+)\s*>')
            for m in vec_pattern.finditer(content):
                start = m.start()
                end = m.end()
                original = m.group(0)
                lanes = m.group(1)
                replacement = f'aie::vector<int32, {lanes}>'
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "pack_unpack_wrong_element_type",
                    "category": "vector_shuffles_and_permutations",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": f"Changed vector element type from int8 to int32 making unpack incompatible: '{original}' -> '{replacement}'"
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy
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
