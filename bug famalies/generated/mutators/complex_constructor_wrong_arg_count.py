import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF272",
    "bug_type": "complex_constructor_wrong_arg_count",
    "category": "complex_datatypes",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["cint16(", "cint32(", "cfloat(", "aie::vector", "push_back"],
    "mutation_strategy": "Replace a correct two-argument complex constructor (e.g., cint16{real, imag}) with a single scalar argument (e.g., cint16{val}) or three arguments (e.g., cint16{r, i, extra}), causing a compile-time constructor resolution failure.",
    "repair_expectation": "Provide exactly two arguments (real and imaginary parts) to the complex type constructor, matching the expected aggregate or struct initialization.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about no matching constructor or too many/few initializers for cint16/cint32/cfloat.",
    "tags": ["cfloat", "cint16", "cint32", "complex_datatypes", "constructor", "initializer"]
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx'))


def _find_matching_paren(text, open_pos):
    """Find the matching closing paren/brace for the opener at open_pos."""
    opener = text[open_pos]
    closer = ')' if opener == '(' else '}'
    depth = 1
    i = open_pos + 1
    while i < len(text) and depth > 0:
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
        i += 1
    if depth == 0:
        return i - 1  # position of closing paren/brace
    return -1


def _split_args(args_str):
    """Split arguments by comma, respecting nested parens/braces/brackets."""
    args = []
    depth = 0
    current = []
    for ch in args_str:
        if ch in '({[':
            depth += 1
            current.append(ch)
        elif ch in ')}]':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    final = ''.join(current).strip()
    if final:
        args.append(final)
    return args


def find_mutation_candidates(project_files):
    candidates = []
    # Match complex type constructors with parens or braces containing exactly 2 args
    # Patterns: cint16(arg1, arg2), cint16{arg1, arg2}, cint32(...), cfloat(...)
    complex_types = ['cint16', 'cint32', 'cfloat']

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Search for patterns like cint16( or cint16{ with two arguments
        for ctype in complex_types:
            # Match both paren and brace initialization
            pattern = re.compile(r'(\(?\b' + re.escape(ctype) + r'\)?\s*)([({])')
            for m in pattern.finditer(content):
                type_name = m.group(1)
                open_char = m.group(2)
                open_pos = m.start(2)

                close_pos = _find_matching_paren(content, open_pos)
                if close_pos == -1:
                    continue

                # Extract the arguments string
                args_str = content[open_pos + 1:close_pos]

                # Split and check for exactly 2 arguments
                args = _split_args(args_str)
                if len(args) != 2:
                    continue

                # Full match span: from start of type name to closing paren/brace inclusive
                full_start = m.start(0)
                full_end = close_pos + 1
                original = content[full_start:full_end]

                close_char = ')' if open_char == '(' else '}'

                # Mutation option 1: single argument (remove imaginary part)
                replacement_one_arg = f"{type_name}{open_char}{args[0]}{close_char}"

                # Mutation option 2: three arguments (add extra arg)
                replacement_three_args = f"{type_name}{open_char}{args[0]}, {args[1]}, 0{close_char}"

                # We'll prefer removing an argument (single arg) as primary mutation
                # Alternate between strategies based on position parity
                if full_start % 2 == 0:
                    replacement = replacement_one_arg
                    desc = f"Removed imaginary argument from {type_name} constructor, leaving only one argument."
                else:
                    replacement = replacement_three_args
                    desc = f"Added extra third argument to {type_name} constructor, causing argument count mismatch."

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "complex_constructor_wrong_arg_count",
                    "category": "complex_datatypes",
                    "start": full_start,
                    "end": full_end,
                    "original": original,
                    "replacement": replacement,
                    "description": desc
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    start = candidate["start"]
    end = candidate["end"]
    replacement = candidate["replacement"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
