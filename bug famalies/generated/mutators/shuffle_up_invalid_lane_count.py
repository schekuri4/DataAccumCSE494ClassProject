import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF211",
    "bug_type": "shuffle_up_invalid_lane_count",
    "category": "vector_shuffles_and_permutations",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["aie::shuffle_up", "aie::vector<int32,"],
    "mutation_strategy": "Replace the shift amount in aie::shuffle_up with a value exceeding the vector lane count (e.g., shuffle_up(vec, 64) on a 32-lane vector), or use a non-compile-time-constant expression as the shift parameter.",
    "repair_expectation": "Change the shift amount to a valid compile-time constant within [1, vector_size-1].",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or static_assert about invalid shift amount.",
    "tags": [
        "compile_time",
        "lane_index",
        "out_of_range",
        "shuffle_up",
        "vector_shuffles_and_permutations"
    ]
}


def _find_vector_lane_count(file_content: str) -> int | None:
    """Try to find the lane count from aie::vector<int32, N> declarations."""
    match = re.search(r'aie::vector\s*<\s*int32\s*,\s*(\d+)\s*>', file_content)
    if match:
        return int(match.group(1))
    return None


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        # Look for kernel source files (typically .cc, .cpp, .h, .hpp)
        if not any(file_path.endswith(ext) for ext in ('.cc', '.cpp', '.c', '.h', '.hpp', '.hh')):
            continue

        # Check if file contains relevant match targets
        if 'aie::shuffle_up' not in content:
            continue

        # Try to determine vector lane count from the file
        lane_count = _find_vector_lane_count(content)

        # Find all aie::shuffle_up calls with their shift arguments
        # Pattern: aie::shuffle_up(expr, shift_amount)
        # We need to handle nested parentheses in the first argument
        pattern = re.compile(
            r'(aie::shuffle_up\s*\()'  # function call start
        )

        for m in pattern.finditer(content):
            call_start = m.start()
            # Find the matching closing parenthesis
            paren_depth = 0
            idx = m.end() - 1  # position of the opening '('
            # Actually let's find from the '(' character
            open_paren_pos = content.index('(', m.start())
            paren_depth = 1
            i = open_paren_pos + 1
            comma_positions = []
            while i < len(content) and paren_depth > 0:
                ch = content[i]
                if ch == '(':
                    paren_depth += 1
                elif ch == ')':
                    paren_depth -= 1
                elif ch == ',' and paren_depth == 1:
                    comma_positions.append(i)
                i += 1

            if paren_depth != 0:
                continue

            close_paren_pos = i - 1  # position of closing ')'

            # shuffle_up takes 2 arguments: (vector, shift)
            if len(comma_positions) < 1:
                continue

            # The shift argument is after the last comma
            last_comma = comma_positions[-1]
            shift_arg = content[last_comma + 1:close_paren_pos].strip()

            # Check if shift_arg is a numeric literal
            shift_match = re.match(r'^(\d+)$', shift_arg)

            # Full call text
            full_call_start = call_start
            full_call_end = close_paren_pos + 1
            original_text = content[full_call_start:full_call_end]

            # Determine invalid replacement value
            if lane_count:
                invalid_shift = lane_count * 2  # Exceeds lane count
            else:
                invalid_shift = 64  # Default large value

            if shift_match:
                # Replace the numeric shift with an invalid value
                original_shift = shift_arg
                replacement_text = original_text[:last_comma - call_start + 1] + ' ' + str(invalid_shift) + content[close_paren_pos - len(content):]
                # Simpler: reconstruct
                new_shift = str(invalid_shift)
                # Replace just the shift portion in the original text
                shift_start_in_file = last_comma + 1
                shift_end_in_file = close_paren_pos
                # Trim whitespace awareness
                original_segment = content[shift_start_in_file:shift_end_in_file]
                # Preserve leading whitespace
                leading_ws = ''
                for ch in original_segment:
                    if ch in ' \t':
                        leading_ws += ch
                    else:
                        break
                replacement_segment = leading_ws + new_shift

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shuffle_up_invalid_lane_count",
                    "category": "vector_shuffles_and_permutations",
                    "start": shift_start_in_file,
                    "end": shift_end_in_file,
                    "original": original_segment,
                    "replacement": replacement_segment,
                    "description": (
                        f"Replace shuffle_up shift amount '{shift_arg}' with '{new_shift}' "
                        f"which exceeds the vector lane count"
                        f"{' of ' + str(lane_count) if lane_count else ''}, "
                        f"causing a compile-time error."
                    )
                })
            else:
                # Shift is an expression or variable - replace with invalid constant
                shift_start_in_file = last_comma + 1
                shift_end_in_file = close_paren_pos
                original_segment = content[shift_start_in_file:shift_end_in_file]
                leading_ws = ''
                for ch in original_segment:
                    if ch in ' \t':
                        leading_ws += ch
                    else:
                        break
                replacement_segment = leading_ws + str(invalid_shift)

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "shuffle_up_invalid_lane_count",
                    "category": "vector_shuffles_and_permutations",
                    "start": shift_start_in_file,
                    "end": shift_end_in_file,
                    "original": original_segment,
                    "replacement": replacement_segment,
                    "description": (
                        f"Replace shuffle_up shift expression '{shift_arg.strip()}' with '{invalid_shift}' "
                        f"which exceeds the vector lane count"
                        f"{' of ' + str(lane_count) if lane_count else ''}, "
                        f"causing a compile-time error."
                    )
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
        # Fallback: try to find and replace the first occurrence in context
        # This handles cases where file may have shifted slightly
        idx = content.find(original)
        if idx != -1:
            new_content = content[:idx] + replacement + content[idx + len(original):]
        else:
            # Cannot apply mutation safely, return unchanged
            new_content = content

    new_files[file_path] = new_content
    return new_files
