import re
import copy

BUG_FAMILY = {
    "family_id": "BF112",
    "bug_type": "writeincr_v_type_mismatch",
    "category": "stream_vector_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "writeincr_v<",
        "output_stream<",
        "aie::vector<int32",
        "aie::vector<cint16"
    ],
    "mutation_strategy": "Pass a vector of the wrong element type to writeincr_v. For example, declare an aie::vector<int32,8> but write it to an output_stream<cint16>*, or pass aie::vector<float,4> to an output_stream<int32>*.",
    "repair_expectation": "Change the vector element type to match the output_stream's declared element type, or change the stream declaration to match the vector type.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch or no matching function for writeincr_v.",
    "tags": [
        "stream_element_type",
        "stream_vector_interfaces",
        "type_mismatch",
        "writeincr_v"
    ]
}

# Type substitution map: original type -> mismatched replacement type
_TYPE_SUBSTITUTIONS = {
    "int32": "cint16",
    "cint16": "int32",
    "int16": "cint16",
    "cint32": "int32",
    "float": "int32",
    "int8": "int32",
    "uint8": "int32",
    "int32_t": "cint16",
    "int16_t": "cint16",
}


def _is_kernel_source(path):
    """Heuristic: kernel source files are .cpp, .cc, or .h files."""
    lower = path.lower()
    return lower.endswith(('.cpp', '.cc', '.h', '.hpp', '.c'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find writeincr_v calls and change the vector type at declaration
        # Look for patterns like: writeincr_v<TYPE>(stream_ptr, vec_var)
        # Then find the declaration of vec_var as aie::vector<TYPE, N>

        # First, find all writeincr_v calls
        writeincr_pattern = re.compile(
            r'writeincr_v\s*<\s*(\w+)\s*>\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)'
        )

        for m in writeincr_pattern.finditer(content):
            stream_type = m.group(1)
            stream_var = m.group(2)
            vec_var = m.group(3)

            # Find the vector declaration for vec_var
            # Pattern: aie::vector<TYPE, N> vec_var
            vec_decl_pattern = re.compile(
                r'(aie::vector\s*<\s*)(\w+)(\s*,\s*\d+\s*>\s*' + re.escape(vec_var) + r')'
            )
            for vm in vec_decl_pattern.finditer(content):
                vec_elem_type = vm.group(2)
                # Only mutate if the vector type currently matches the stream type
                if vec_elem_type == stream_type:
                    # Pick a mismatched type
                    replacement_type = _TYPE_SUBSTITUTIONS.get(vec_elem_type)
                    if replacement_type is None:
                        replacement_type = "float" if vec_elem_type != "float" else "int32"

                    original = vm.group(1) + vm.group(2) + vm.group(3)
                    replacement = vm.group(1) + replacement_type + vm.group(3)

                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "writeincr_v_type_mismatch",
                        "category": "stream_vector_interfaces",
                        "start": vm.start(),
                        "end": vm.end(),
                        "original": original,
                        "replacement": replacement,
                        "description": (
                            f"Changed vector element type from '{vec_elem_type}' to "
                            f"'{replacement_type}' causing type mismatch with "
                            f"writeincr_v<{stream_type}> call."
                        )
                    })

        # Strategy 2: Find aie::vector declarations used in writeincr_v where
        # we can directly mutate the vector type in the writeincr_v template argument
        # Pattern: writeincr_v<TYPE>(...) and we change TYPE to a mismatched type
        # But we need to verify there's a vector of the original type being passed

        # Strategy 3: Direct approach - find writeincr_v<TYPE> and change the template type
        # to mismatch with the vector being passed
        writeincr_template_pattern = re.compile(
            r'(writeincr_v\s*<\s*)(\w+)(\s*>\s*\(\s*\w+\s*,\s*(\w+)\s*\))'
        )

        for m in writeincr_template_pattern.finditer(content):
            template_type = m.group(2)
            vec_var = m.group(4)

            # Find the vector declaration
            vec_decl_pattern2 = re.compile(
                r'aie::vector\s*<\s*(\w+)\s*,\s*\d+\s*>\s*' + re.escape(vec_var)
            )
            for vm in vec_decl_pattern2.finditer(content):
                vec_elem_type = vm.group(1)
                # If template type matches vector type, mutate the vector decl type
                if vec_elem_type == template_type:
                    replacement_type = _TYPE_SUBSTITUTIONS.get(vec_elem_type)
                    if replacement_type is None:
                        replacement_type = "float" if vec_elem_type != "float" else "int32"

                    original_text = content[vm.start():vm.end()]
                    new_text = original_text.replace(
                        f"aie::vector<{vec_elem_type}",
                        f"aie::vector<{replacement_type}",
                        1
                    )
                    # Avoid duplicates
                    already_exists = any(
                        c["file_path"] == file_path and c["start"] == vm.start()
                        for c in candidates
                    )
                    if not already_exists and original_text != new_text:
                        candidates.append({
                            "file_path": file_path,
                            "bug_type": "writeincr_v_type_mismatch",
                            "category": "stream_vector_interfaces",
                            "start": vm.start(),
                            "end": vm.end(),
                            "original": original_text,
                            "replacement": new_text,
                            "description": (
                                f"Changed vector element type from '{vec_elem_type}' to "
                                f"'{replacement_type}' causing type mismatch with "
                                f"writeincr_v<{template_type}> call."
                            )
                        })

        # Strategy 4: Broader pattern - find any aie::vector<TYPE,N> near writeincr_v usage
        # Check if file contains writeincr_v at all
        if 'writeincr_v' in content:
            # Find all aie::vector declarations
            vec_pattern = re.compile(
                r'(aie::vector\s*<\s*)(\w+)(\s*,\s*(\d+)\s*>)'
            )
            for vm in vec_pattern.finditer(content):
                vec_elem_type = vm.group(2)
                vec_size = vm.group(4)

                # Check if this isn't already captured
                already_exists = any(
                    c["file_path"] == file_path and c["start"] == vm.start()
                    for c in candidates
                )
                if already_exists:
                    continue

                # Check if there's an output_stream with a different or same type
                # and a writeincr_v that would use this vector
                output_stream_pattern = re.compile(
                    r'output_stream\s*<\s*(\w+)\s*>'
                )
                stream_matches = output_stream_pattern.findall(content)
                if stream_matches:
                    # If the vector type matches any stream type, mutate it
                    for st in stream_matches:
                        if st == vec_elem_type:
                            replacement_type = _TYPE_SUBSTITUTIONS.get(vec_elem_type)
                            if replacement_type is None:
                                replacement_type = "float" if vec_elem_type != "float" else "int32"

                            original_text = vm.group(1) + vm.group(2) + vm.group(3)
                            new_text = vm.group(1) + replacement_type + vm.group(3)

                            if original_text != new_text:
                                candidates.append({
                                    "file_path": file_path,
                                    "bug_type": "writeincr_v_type_mismatch",
                                    "category": "stream_vector_interfaces",
                                    "start": vm.start(),
                                    "end": vm.start() + len(original_text),
                                    "original": original_text,
                                    "replacement": new_text,
                                    "description": (
                                        f"Changed vector element type from '{vec_elem_type}' to "
                                        f"'{replacement_type}' causing type mismatch with "
                                        f"output_stream<{st}>."
                                    )
                                })
                            break  # Only one mutation per site

    # Deduplicate candidates by (file_path, start, end)
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c["file_path"], c["start"], c["end"], c["replacement"])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    return unique_candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    actual_text = content[start:end]
    if actual_text == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
