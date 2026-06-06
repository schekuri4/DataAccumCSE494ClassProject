import re
import copy

BUG_FAMILY = {
    "family_id": "BF119",
    "bug_type": "writeincr_v_cascade_stream_confusion",
    "category": "stream_vector_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "writeincr_v<",
        "put_mcd(",
        "writeincr(",
        "output_stream<",
        "output_cascade_stream"
    ],
    "mutation_strategy": "Use writeincr_v on a cascade stream pointer or use cascade-specific APIs (put_mcd/get_mcd) on a regular output_stream pointer, confusing the cascade and AXI stream interfaces.",
    "repair_expectation": "Use writeincr_v/readincr_v only on regular AXI stream pointers and cascade-specific APIs only on cascade stream ports.",
    "validation_signal": "WSL Vitis/AIE compile failure with no matching function or incompatible pointer type for the stream API call.",
    "tags": [
        "api_confusion",
        "cascade_stream",
        "port_type",
        "stream_vector_interfaces",
        "writeincr_v"
    ]
}


def _is_kernel_source(path):
    """Heuristic: C/C++ source or header files likely containing AIE kernel code."""
    return path.endswith(('.cc', '.cpp', '.c', '.h', '.hpp'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find writeincr_v calls on regular output_stream pointers
        # and suggest replacing with put_mcd (cascade API on regular stream)
        # Pattern: writeincr_v<TYPE>(stream_ptr, value)
        pattern_writeincr_v = re.compile(
            r'(writeincr_v\s*<\s*[^>]+>\s*\(\s*\w+\s*,\s*[^)]+\))'
        )
        for m in pattern_writeincr_v.finditer(content):
            original = m.group(0)
            # Check context: if the variable used looks like it's an output_stream (not cascade)
            # We'll create a mutation that replaces writeincr_v with put_mcd
            # Extract args: writeincr_v<TYPE>(ptr, val) -> put_mcd(val)
            args_match = re.match(
                r'writeincr_v\s*<\s*([^>]+)>\s*\(\s*(\w+)\s*,\s*([^)]+)\)', original
            )
            if args_match:
                type_param = args_match.group(1).strip()
                ptr_name = args_match.group(2).strip()
                value = args_match.group(3).strip()
                replacement = f'put_mcd({value})'
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "writeincr_v_cascade_stream_confusion",
                    "category": "stream_vector_interfaces",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Replaced writeincr_v<{type_param}>({ptr_name}, ...) with "
                        f"put_mcd(...), using cascade-specific API on a regular stream pointer."
                    )
                })

        # Strategy 2: Find put_mcd calls and replace with writeincr_v on cascade stream
        pattern_put_mcd = re.compile(
            r'(put_mcd\s*\(\s*([^)]+)\))'
        )
        for m in pattern_put_mcd.finditer(content):
            original = m.group(0)
            value = m.group(2).strip()
            # Try to find a cascade stream variable in context to use as ptr
            # Look for output_cascade_stream declarations nearby
            cascade_var_match = re.search(
                r'output_cascade_stream\s*\*?\s*(\w+)', content
            )
            ptr_name = cascade_var_match.group(1) if cascade_var_match else "cascadeout"
            replacement = f'writeincr_v<acc48>({ptr_name}, {value})'
            candidates.append({
                "file_path": file_path,
                "bug_type": "writeincr_v_cascade_stream_confusion",
                "category": "stream_vector_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Replaced put_mcd({value}) with writeincr_v<acc48>({ptr_name}, {value}), "
                    f"using regular stream API on a cascade stream."
                )
            })

        # Strategy 3: Find writeincr (non-vector) on streams and replace with put_mcd
        pattern_writeincr = re.compile(
            r'(writeincr\s*\(\s*(\w+)\s*,\s*([^)]+)\))'
        )
        for m in pattern_writeincr.finditer(content):
            original = m.group(0)
            ptr_name = m.group(2).strip()
            value = m.group(3).strip()
            replacement = f'put_mcd({value})'
            candidates.append({
                "file_path": file_path,
                "bug_type": "writeincr_v_cascade_stream_confusion",
                "category": "stream_vector_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Replaced writeincr({ptr_name}, {value}) with put_mcd({value}), "
                    f"using cascade-specific API on a regular stream pointer."
                )
            })

        # Strategy 4: If there's an output_cascade_stream declaration and writeincr_v usage,
        # mutate to use writeincr_v on the cascade pointer directly
        cascade_decls = re.finditer(
            r'(output_cascade_stream\s*\*?\s*(\w+))', content
        )
        for cdecl in cascade_decls:
            cascade_var = cdecl.group(2)
            # Find any writeincr_v that does NOT already use this cascade var
            for wm in pattern_writeincr_v.finditer(content):
                original = wm.group(0)
                args_match = re.match(
                    r'writeincr_v\s*<\s*([^>]+)>\s*\(\s*(\w+)\s*,\s*([^)]+)\)', original
                )
                if args_match:
                    ptr_name = args_match.group(2).strip()
                    if ptr_name != cascade_var:
                        type_param = args_match.group(1).strip()
                        value = args_match.group(3).strip()
                        replacement = f'writeincr_v<{type_param}>({cascade_var}, {value})'
                        # Avoid duplicate if same start/end
                        already = any(
                            c["start"] == wm.start() and c["end"] == wm.end()
                            and c["replacement"] == replacement
                            for c in candidates
                        )
                        if not already:
                            candidates.append({
                                "file_path": file_path,
                                "bug_type": "writeincr_v_cascade_stream_confusion",
                                "category": "stream_vector_interfaces",
                                "start": wm.start(),
                                "end": wm.end(),
                                "original": original,
                                "replacement": replacement,
                                "description": (
                                    f"Replaced stream pointer '{ptr_name}' with cascade stream "
                                    f"pointer '{cascade_var}' in writeincr_v call, confusing "
                                    f"cascade and AXI stream interfaces."
                                )
                            })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
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
