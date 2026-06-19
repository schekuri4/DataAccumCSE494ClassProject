import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF265",
    "bug_type": "sliding_mul_coefficient_offset_out_of_range",
    "category": "sliding_mul_and_mac",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::sliding_mul_ops",
        ".mul",
        "CoeffStart",
        "DataStart"
    ],
    "mutation_strategy": "Set the CoeffStart or DataStart runtime/template offset parameter to a value that exceeds the valid range for the vector size being used. For example, if the coefficient vector has 8 elements and Points=8, set CoeffStart to a value >= vector_size - Points + 1, causing an out-of-bounds compile-time check failure.",
    "repair_expectation": "Correct the CoeffStart or DataStart offset to be within the valid range that ensures all taps can be accessed within the vector boundaries.",
    "validation_signal": "WSL Vitis/AIE compile failure with static_assert about offset exceeding valid range or template parameter bounds check.",
    "tags": [
        "bounds_check",
        "coefficient",
        "offset",
        "sliding_mul",
        "sliding_mul_and_mac"
    ]
}


def _is_kernel_source(path: str) -> bool:
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def _find_sliding_mul_ops_files(project_files: dict[str, str]) -> list[str]:
    """Find files that contain sliding multiply/MAC usage."""
    results = []
    for path, content in project_files.items():
        if _is_kernel_source(path) and any(token in content for token in (
            'sliding_mul', 'sliding_mac', 'mul4', 'mac4', 'lmul4'
        )):
            results.append(path)
    return results


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    target_files = _find_sliding_mul_ops_files(project_files)

    for file_path in target_files:
        content = project_files[file_path]

        # Strategy 1: Find .mul( calls with CoeffStart or DataStart as named/positional arguments
        # Look for patterns like .mul(coeff, coeff_start, data, data_start) or template params
        
        # Pattern for template instantiation of sliding_mul_ops with template parameters
        # e.g., aie::sliding_mul_ops<Lanes, Points, CoeffStep, DataStepX, DataStepY, CoeffType, DataType>
        template_pattern = re.compile(
            r'((?:::)?aie::sliding_mul(?:_sym)?(?:_ops)?\s*<[^>]*>)'
        )

        # Pattern for .mul( or .mac( calls with offset arguments
        # Typically: obj.mul(coeff, coeff_start, data, data_start)
        # or: aie::sliding_mul_ops<...>::mul(coeff, coeff_start, data, data_start)
        mul_call_pattern = re.compile(
            r'\.mul\s*\(([^)]*)\)|\.mac\s*\(([^)]*)\)'
        )

        # Find CoeffStart or DataStart in template parameters or variable assignments
        # Pattern: CoeffStart = N or coeff_start = N or similar
        offset_assign_pattern = re.compile(
            r'\b((?:[Cc]oeff_?[Ss]tart|[Dd]ata_?[Ss]tart|coeff_offset|data_offset))\s*=\s*(\d+)'
        )

        for m in offset_assign_pattern.finditer(content):
            var_name = m.group(1)
            original_value = m.group(2)
            original_int = int(original_value)
            # Set to an out-of-range value
            bad_value = str(max(original_int + 16, 64))
            
            start_pos = m.start()
            end_pos = m.end()
            original_text = m.group(0)
            replacement_text = f"{var_name} = {bad_value}"

            is_coeff = 'coeff' in var_name.lower()
            param_type = "CoeffStart" if is_coeff else "DataStart"

            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mul_coefficient_offset_out_of_range",
                "category": "sliding_mul_and_mac",
                "start": start_pos,
                "end": end_pos,
                "original": original_text,
                "replacement": replacement_text,
                "description": f"Set {param_type} ({var_name}) from {original_value} to {bad_value}, exceeding valid range for sliding_mul operation."
            })

        # Modern direct API forms:
        #   ::aie::sliding_mul<...>(coeff, 0, data, 0)
        #   ::aie::sliding_mac<...>(acc, coeff, 4, data, 0)
        # This intentionally targets the first simple numeric offset after the
        # template call prefix; it leaves expression offsets such as kmap[c_s]
        # alone because those require semantic rewriting.
        modern_call_offset = re.compile(
            r'((?:::)?aie::sliding_m(?:ul|ac)\s*<[^>]+>\s*\((?:[^,()]+,\s*){1,2})'
            r'(\d+)'
        )
        for m in modern_call_offset.finditer(content):
            val = int(m.group(2))
            bad_val = str(max(val + 16, 64))
            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mul_coefficient_offset_out_of_range",
                "category": "sliding_mul_and_mac",
                "start": m.start(2),
                "end": m.end(2),
                "original": m.group(2),
                "replacement": bad_val,
                "description": (
                    f"Set sliding_mul/sliding_mac runtime offset from {val} to {bad_val}, "
                    f"exceeding the valid coefficient/data vector range."
                )
            })

        # Legacy intrinsic forms:
        #   mul4_sym(lbuff, 6, ..., rbuff, 8, coeff, 0, ...)
        # Mutating the first vector offset gives a compact, deterministic
        # compile-time bounds failure without changing unrelated arguments.
        legacy_call_offset = re.compile(
            r'\b(?:l)?(?:mul|mac)4(?:_(?:sym|antisym|ct|sym_ct))?\s*'
            r'\(\s*[^,]+,\s*(\d+)'
        )
        for m in legacy_call_offset.finditer(content):
            val = int(m.group(1))
            bad_val = str(max(val + 16, 64))
            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mul_coefficient_offset_out_of_range",
                "category": "sliding_mul_and_mac",
                "start": m.start(1),
                "end": m.end(1),
                "original": m.group(1),
                "replacement": bad_val,
                "description": (
                    f"Set legacy sliding intrinsic offset from {val} to {bad_val}, "
                    f"exceeding the valid vector range."
                )
            })

        # Pattern for .mul(coeff, <number>, data, <number>) style calls
        # Match the numeric offset arguments in .mul/.mac calls
        mul_with_offsets = re.compile(
            r'(\.\s*(?:mul|mac)\s*\(\s*'
            r'[^,]+,\s*)'       # first arg (coeff vector) + comma
            r'(\d+)'            # coeff_start (capture group 2)
            r'(\s*,\s*'
            r'[^,]+,\s*)'      # data vector + comma
            r'(\d+)'           # data_start (capture group 4)
            r'(\s*\))'
        )

        for m in mul_with_offsets.finditer(content):
            # Mutate CoeffStart (group 2)
            coeff_start_val = int(m.group(2))
            bad_coeff = str(max(coeff_start_val + 16, 64))
            
            original_full = m.group(0)
            replacement_coeff = m.group(1) + bad_coeff + m.group(3) + m.group(4) + m.group(5)

            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mul_coefficient_offset_out_of_range",
                "category": "sliding_mul_and_mac",
                "start": m.start(),
                "end": m.end(),
                "original": original_full,
                "replacement": replacement_coeff,
                "description": f"Set CoeffStart from {coeff_start_val} to {bad_coeff} in .mul/.mac call, exceeding valid coefficient vector range."
            })

            # Mutate DataStart (group 4)
            data_start_val = int(m.group(4))
            bad_data = str(max(data_start_val + 16, 64))
            
            replacement_data = m.group(1) + m.group(2) + m.group(3) + bad_data + m.group(5)

            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mul_coefficient_offset_out_of_range",
                "category": "sliding_mul_and_mac",
                "start": m.start(),
                "end": m.end(),
                "original": original_full,
                "replacement": replacement_data,
                "description": f"Set DataStart from {data_start_val} to {bad_data} in .mul/.mac call, exceeding valid data vector range."
            })

        # Pattern for template parameters in sliding_mul_ops instantiation
        # e.g., aie::sliding_mul_ops<8, 8, 1, 1, 1, int16, int16>
        # Some variants include CoeffStart/DataStart as template params
        sliding_mul_template = re.compile(
            r'((?:::)?aie::sliding_mul(?:_sym)?(?:_ops)?\s*<\s*'
            r'\d+\s*,\s*'   # Lanes
            r'\d+\s*,\s*'   # Points
            r'\d+\s*,\s*'   # CoeffStep
            r'\d+\s*,\s*'   # DataStepX
            r'\d+\s*,\s*'   # DataStepY
            r'[^,>]+,\s*'   # CoeffType
            r'[^,>]+)'      # DataType
            r'(\s*>)'
        )

        # Also look for sliding_mul_ops with explicit CoeffStart template param
        # Some APIs: aie::sliding_mul_ops<Lanes, Points, CoeffStep, DataStepX, DataStepY, CoeffType, DataType, CoeffStart>
        sliding_mul_with_start = re.compile(
            r'((?:::)?aie::sliding_mul(?:_sym)?(?:_ops)?\s*<[^>]*?,\s*)'
            r'(\d+)'  # Last numeric template param that could be CoeffStart
            r'(\s*>)'
        )

        for m in sliding_mul_with_start.finditer(content):
            val = int(m.group(2))
            # Only mutate if it looks like a start offset (small number)
            if val < 8:
                bad_val = str(val + 32)
                original_text = m.group(0)
                replacement_text = m.group(1) + bad_val + m.group(3)

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "sliding_mul_coefficient_offset_out_of_range",
                    "category": "sliding_mul_and_mac",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": f"Set CoeffStart template parameter from {val} to {bad_val} in sliding_mul_ops, exceeding valid range."
                })

        # Pattern for standalone .mul/.mac with single offset argument
        # e.g., sliding_mul_obj.mul(coeff, offset, data)
        single_offset_pattern = re.compile(
            r'(\.\s*(?:mul|mac)\s*\(\s*[^,]+,\s*)(\d+)(\s*,\s*[^)]+\))'
        )

        for m in single_offset_pattern.finditer(content):
            # Avoid duplicates with the two-offset pattern
            full_match = m.group(0)
            # Count commas to distinguish from the 4-arg pattern
            comma_count = full_match.count(',')
            if comma_count == 2:  # 3 args: coeff, offset, data
                val = int(m.group(2))
                bad_val = str(max(val + 16, 64))
                original_text = full_match
                replacement_text = m.group(1) + bad_val + m.group(3)

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "sliding_mul_coefficient_offset_out_of_range",
                    "category": "sliding_mul_and_mac",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": f"Set offset from {val} to {bad_val} in .mul/.mac call, exceeding valid range for sliding_mul operation."
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate to the project files."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    actual = content[start:end]
    if actual == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: use string replacement (first occurrence)
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
