import re
import copy

BUG_FAMILY = {
    "family_id": "BF017",
    "bug_type": "macro_redefine_lane_width_mismatch",
    "category": "header_guards_and_preprocessor",
    "target_files": [
        "kernel header",
        "shared utility header",
        "kernel source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#define VECTOR_LANES",
        "#define NUM_LANES",
        "readincr_v<",
        "writeincr_v<",
        "aie::vector<"
    ],
    "mutation_strategy": "Define a VECTOR_LANES or NUM_LANES macro in a shared header with value 8, then redefine it to 16 (or vice versa) in the kernel header without #undef, causing readincr_v<VECTOR_LANES> or writeincr_v<VECTOR_LANES> to use the wrong lane count that doesn't match the stream or window data width, producing a compile-time template error.",
    "repair_expectation": "Remove the conflicting macro redefinition or add #undef before redefining, ensuring consistent lane width across all files.",
    "validation_signal": "WSL Vitis/AIE compile failure with macro redefinition warning promoted to error or template instantiation failure for vector lane mismatch.",
    "tags": [
        "header_guards_and_preprocessor",
        "lane_mismatch",
        "macro_redefine",
        "readincr_v",
        "vector_lanes",
        "writeincr_v"
    ]
}

# Pattern to match #define VECTOR_LANES or #define NUM_LANES with a numeric value
_MACRO_DEFINE_PATTERN = re.compile(
    r'^(\s*#\s*define\s+(VECTOR_LANES|NUM_LANES)\s+)(\d+)(.*?)$',
    re.MULTILINE
)

# Pattern to match usage of readincr_v<N>, writeincr_v<N>, aie::vector<type, N>
_USAGE_PATTERN = re.compile(
    r'(readincr_v<|writeincr_v<|aie::vector<)'
)


def _is_header(path):
    return path.endswith('.h') or path.endswith('.hpp')


def _is_source(path):
    return path.endswith('.cc') or path.endswith('.cpp') or path.endswith('.c')


def _flip_lane_value(val):
    """Flip between common lane widths: 8<->16, 4<->8, 16<->32, etc."""
    v = int(val)
    if v == 8:
        return "16"
    elif v == 16:
        return "8"
    elif v == 4:
        return "8"
    elif v == 32:
        return "16"
    else:
        # Default: double it
        return str(v * 2)


def find_mutation_candidates(project_files):
    candidates = []

    # Strategy 1: Find files that define VECTOR_LANES or NUM_LANES and
    # mutate by inserting a conflicting redefinition at the usage site,
    # or by changing the value in one of the definitions.

    # Collect all macro definitions across files
    macro_defs = []  # (file_path, match_obj, macro_name, value)
    for fpath, content in project_files.items():
        if not (_is_header(fpath) or _is_source(fpath)):
            continue
        for m in _MACRO_DEFINE_PATTERN.finditer(content):
            macro_defs.append((fpath, m, m.group(2), m.group(3)))

    # Collect files that use the macros in template contexts
    usage_files = []
    for fpath, content in project_files.items():
        if not (_is_header(fpath) or _is_source(fpath)):
            continue
        if _USAGE_PATTERN.search(content):
            usage_files.append(fpath)

    # Strategy A: If there are macro definitions, we can insert a conflicting
    # redefinition in a different file (preferably a kernel header or source
    # that uses the macro in readincr_v/writeincr_v/aie::vector).
    for def_path, def_match, macro_name, value in macro_defs:
        flipped = _flip_lane_value(value)

        # Look for another file that uses this macro or the vector operations
        for use_path in usage_files:
            if use_path == def_path:
                continue
            use_content = project_files[use_path]

            # Check if this file uses the macro name
            if macro_name not in use_content:
                continue

            # Find a good insertion point: after includes or at the top
            # Insert a redefinition without #undef
            insert_line = f"#define {macro_name} {flipped}  // redefined for this kernel\n"

            # Find the last #include line to insert after it
            include_pattern = re.compile(r'^#\s*include\s+.*$', re.MULTILINE)
            includes = list(include_pattern.finditer(use_content))

            if includes:
                insert_pos = includes[-1].end()
                # Insert after the last include
                original_segment = use_content[insert_pos:insert_pos]  # empty
                replacement_segment = "\n" + insert_line
                candidates.append({
                    "file_path": use_path,
                    "bug_type": "macro_redefine_lane_width_mismatch",
                    "category": "header_guards_and_preprocessor",
                    "start": insert_pos,
                    "end": insert_pos,
                    "original": "",
                    "replacement": replacement_segment,
                    "description": (
                        f"Insert conflicting redefinition of {macro_name} as {flipped} "
                        f"(originally {value} in {def_path}) in {use_path} without #undef, "
                        f"causing lane width mismatch in vector operations."
                    )
                })
            else:
                # Insert at the very beginning
                candidates.append({
                    "file_path": use_path,
                    "bug_type": "macro_redefine_lane_width_mismatch",
                    "category": "header_guards_and_preprocessor",
                    "start": 0,
                    "end": 0,
                    "original": "",
                    "replacement": insert_line,
                    "description": (
                        f"Insert conflicting redefinition of {macro_name} as {flipped} "
                        f"(originally {value} in {def_path}) at top of {use_path} without #undef, "
                        f"causing lane width mismatch in vector operations."
                    )
                })

    # Strategy B: If there's only one file with the macro definition and it also
    # has usage, change the macro value directly to cause mismatch with any
    # hardcoded template parameters in the same or other files.
    if not candidates:
        for def_path, def_match, macro_name, value in macro_defs:
            flipped = _flip_lane_value(value)
            start = def_match.start(3)
            end = def_match.end(3)
            candidates.append({
                "file_path": def_path,
                "bug_type": "macro_redefine_lane_width_mismatch",
                "category": "header_guards_and_preprocessor",
                "start": start,
                "end": end,
                "original": value,
                "replacement": flipped,
                "description": (
                    f"Change {macro_name} from {value} to {flipped} in {def_path}, "
                    f"causing lane width mismatch with stream/window data width."
                )
            })

    # Strategy C: If no macro definitions exist but there are template usages
    # with literal numbers, look for readincr_v<N> or writeincr_v<N> patterns
    # and change the lane count.
    if not candidates:
        literal_pattern = re.compile(
            r'(readincr_v<|writeincr_v<)(\d+)(>)'
        )
        for fpath, content in project_files.items():
            if not (_is_header(fpath) or _is_source(fpath)):
                continue
            for m in literal_pattern.finditer(content):
                val = m.group(2)
                flipped = _flip_lane_value(val)
                # Insert a macro redefinition approach: add a #define before usage
                # Actually, to match the bug family, we should add a conflicting macro.
                # But since there's no existing macro, let's change the literal to
                # introduce a macro-based mismatch by adding a define and using it.
                # Simpler: just change the literal value.
                start = m.start(2)
                end = m.end(2)
                candidates.append({
                    "file_path": fpath,
                    "bug_type": "macro_redefine_lane_width_mismatch",
                    "category": "header_guards_and_preprocessor",
                    "start": start,
                    "end": end,
                    "original": val,
                    "replacement": flipped,
                    "description": (
                        f"Change lane width in {m.group(1)}{val}> to {flipped} in {fpath}, "
                        f"simulating a macro redefinition lane width mismatch."
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate to the project files."""
    mutated = dict(project_files)  # shallow copy of the dict

    fpath = candidate["file_path"]
    if fpath not in mutated:
        return mutated

    content = mutated[fpath]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text matches (for non-insertion mutations)
    if original != "" and content[start:end] != original:
        # Try to find it nearby as a fallback
        idx = content.find(original)
        if idx >= 0:
            start = idx
            end = idx + len(original)
        else:
            return mutated  # Cannot apply mutation safely

    # Apply the mutation
    mutated[fpath] = content[:start] + replacement + content[end:]

    return mutated
