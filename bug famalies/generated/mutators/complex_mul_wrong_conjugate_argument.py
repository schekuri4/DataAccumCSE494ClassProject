import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF281",
    "bug_type": "complex_mul_wrong_conjugate_argument",
    "category": "complex_intrinsics",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::mul",
        "aie::conj",
        "cint16",
        "cint32",
        "aie::op_mul_conj"
    ],
    "mutation_strategy": "Replace aie::mul(a, aie::op_conj(b)) with aie::mul(aie::op_conj(a), b) where the types don't support conjugation on the first operand for that specific overload, or pass a non-complex vector to aie::op_conj causing a template substitution failure.",
    "repair_expectation": "Move the aie::op_conj wrapper to the correct operand position or ensure the operand is a complex type that supports conjugation.",
    "validation_signal": "WSL Vitis/AIE compile failure with template instantiation error or no matching overload for aie::mul with conjugate modifier.",
    "tags": [
        "complex",
        "complex_intrinsics",
        "conjugation",
        "intrinsics",
        "mul",
        "template_error"
    ]
}


def _is_kernel_source(path: str) -> bool:
    """Heuristic: kernel source files are .cc, .cpp, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cc', '.cpp', '.c', '.h', '.hpp', '.hxx'))


def _find_matching_paren(text: str, start: int) -> int:
    """Find the matching closing paren for the opening paren at position start."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _split_top_level_args(text: str) -> list[str]:
    """Split text by commas at the top level (not inside parens, angle brackets, etc.)."""
    args = []
    depth_paren = 0
    depth_angle = 0
    current = []
    for ch in text:
        if ch == '(' :
            depth_paren += 1
            current.append(ch)
        elif ch == ')':
            depth_paren -= 1
            current.append(ch)
        elif ch == '<':
            depth_angle += 1
            current.append(ch)
        elif ch == '>':
            depth_angle -= 1
            current.append(ch)
        elif ch == ',' and depth_paren == 0 and depth_angle == 0:
            args.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        args.append(''.join(current))
    return args


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Pattern 1: aie::mul(a, aie::op_conj(b)) -> aie::mul(aie::op_conj(a), b)
        # Match aie::mul( ... )
        pattern_mul = re.compile(r'aie::mul\s*\(')
        for match in pattern_mul.finditer(content):
            # Find the full extent of aie::mul(...)
            open_paren_pos = match.end() - 1  # position of '('
            close_paren_pos = _find_matching_paren(content, open_paren_pos)
            if close_paren_pos == -1:
                continue

            full_call = content[match.start():close_paren_pos + 1]
            inner = content[open_paren_pos + 1:close_paren_pos]

            # Split into top-level arguments
            args = _split_top_level_args(inner)
            if len(args) < 2:
                continue

            # Check if second argument contains aie::op_conj or aie::conj
            second_arg = args[1].strip() if len(args) >= 2 else ""
            first_arg = args[0].strip()

            conj_pattern = re.compile(r'aie::(?:op_conj|conj)\s*\(')
            conj_in_second = conj_pattern.search(second_arg)

            if conj_in_second:
                # Mutation: move conj from second arg to first arg
                # Extract the inner content of conj in second arg
                conj_match = conj_pattern.search(second_arg)
                conj_start_in_arg = conj_match.start()
                conj_open = conj_match.end() - 1
                conj_close = _find_matching_paren(second_arg, conj_open)
                if conj_close == -1:
                    continue

                conj_wrapper = second_arg[conj_match.start():conj_match.end() - 1]  # e.g. "aie::op_conj"
                conj_inner = second_arg[conj_open + 1:conj_close]  # inner arg of conj

                # New second arg: replace conj(x) with just x, keep rest
                new_second = second_arg[:conj_start_in_arg] + conj_inner + second_arg[conj_close + 1:]
                # New first arg: wrap with same conj
                new_first = conj_wrapper + "(" + first_arg + ")"

                # Reconstruct
                new_args = [new_first] + [new_second] + [a for a in args[2:]]
                new_inner = ", ".join(new_args)
                replacement = "aie::mul(" + new_inner + ")"

                start_pos = match.start()
                end_pos = close_paren_pos + 1
                original = content[start_pos:end_pos]

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "complex_mul_wrong_conjugate_argument",
                    "category": "complex_intrinsics",
                    "start": start_pos,
                    "end": end_pos,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Moved conjugate modifier from second operand to first operand in aie::mul call, "
                        f"which may cause a template substitution failure if the first operand type "
                        f"doesn't support conjugation."
                    )
                })
            elif not conj_pattern.search(first_arg):
                # Pattern 2: aie::mul(a, b) where neither has conj but file uses complex types
                # Check if file references cint16/cint32 and aie::mul is present
                # We can introduce aie::op_conj on the first (non-complex or wrong) operand
                has_complex = re.search(r'cint(?:16|32)', content)
                if has_complex and len(args) >= 2:
                    # Wrap first arg with aie::op_conj
                    new_first = "aie::op_conj(" + first_arg + ")"
                    new_args = [new_first] + [a for a in args[1:]]
                    new_inner = ", ".join(new_args)
                    replacement = "aie::mul(" + new_inner + ")"

                    start_pos = match.start()
                    end_pos = close_paren_pos + 1
                    original = content[start_pos:end_pos]

                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "complex_mul_wrong_conjugate_argument",
                        "category": "complex_intrinsics",
                        "start": start_pos,
                        "end": end_pos,
                        "original": original,
                        "replacement": replacement,
                        "description": (
                            f"Added aie::op_conj wrapper to first operand of aie::mul, "
                            f"which may not support conjugation on that operand position."
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
