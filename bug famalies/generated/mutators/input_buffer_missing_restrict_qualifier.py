import re
import copy

BUG_FAMILY = {
    "family_id": "BF131",
    "bug_type": "input_buffer_missing_restrict_qualifier",
    "category": "buffer_interfaces",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_buffer<int32>",
        "input_buffer<cint16>",
        "input_buffer<float>",
        "__restrict"
    ],
    "mutation_strategy": "Remove the __restrict qualifier from input_buffer pointer access via begin_vector or begin_restrict_vector, or change begin_restrict_vector to begin_vector while the kernel signature still expects restrict-qualified iteration. Alternatively, add __restrict to a non-pointer parameter causing a type mismatch with the kernel registration.",
    "repair_expectation": "Restore the correct use of __restrict on the buffer iterator or switch back to the appropriate begin_restrict_vector call matching the kernel's declared interface.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch or restrict qualifier error in kernel signature binding.",
    "tags": [
        "begin_restrict_vector",
        "buffer_interfaces",
        "input_buffer",
        "kernel_signature",
        "restrict"
    ]
}


def _is_kernel_file(path):
    """Heuristic: kernel source or header files (C/C++ with kernel-like names)."""
    lower = path.lower()
    exts = ('.cpp', '.cc', '.c', '.h', '.hpp', '.hxx')
    return any(lower.endswith(ext) for ext in exts)


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path):
            continue

        # Strategy 1: Replace begin_restrict_vector with begin_vector
        pattern_brv = re.compile(r'\bbegin_restrict_vector\b')
        for m in pattern_brv.finditer(content):
            candidates.append({
                "file_path": file_path,
                "bug_type": "input_buffer_missing_restrict_qualifier",
                "category": "buffer_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": "begin_vector",
                "description": "Replace begin_restrict_vector with begin_vector, removing __restrict qualification from buffer iterator access."
            })

        # Strategy 2: Remove __restrict qualifier from input_buffer parameter declarations
        # Match patterns like: input_buffer<type> & __restrict  or  input_buffer<type>& __restrict
        pattern_restrict_param = re.compile(
            r'(input_buffer\s*<[^>]+>\s*(?:&\s*)?)\s*__restrict\b'
        )
        for m in pattern_restrict_param.finditer(content):
            candidates.append({
                "file_path": file_path,
                "bug_type": "input_buffer_missing_restrict_qualifier",
                "category": "buffer_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": m.group(1).rstrip(),
                "description": "Remove __restrict qualifier from input_buffer parameter declaration."
            })

        # Strategy 3: Remove standalone __restrict from pointer declarations associated with input buffers
        # Look for patterns like: auto * __restrict pIn = ... or type * __restrict ptr
        pattern_ptr_restrict = re.compile(
            r'(\*\s*)__restrict\b(\s*)'
        )
        for m in pattern_ptr_restrict.finditer(content):
            # Only consider if there's an input_buffer reference nearby (within 200 chars before)
            context_start = max(0, m.start() - 200)
            context = content[context_start:m.start()]
            if re.search(r'input_buffer|begin_restrict_vector|begin_vector', context):
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "input_buffer_missing_restrict_qualifier",
                    "category": "buffer_interfaces",
                    "start": m.start(),
                    "end": m.end(),
                    "original": m.group(0),
                    "replacement": m.group(1) + m.group(2),
                    "description": "Remove __restrict qualifier from pointer declaration associated with input_buffer access."
                })

        # Strategy 4: Remove __restrict that appears after a variable name in declarations
        # e.g., "sometype varname __restrict" near input_buffer usage
        pattern_trailing_restrict = re.compile(
            r'(\b\w+\s+\w+)\s+(__restrict)\b'
        )
        for m in pattern_trailing_restrict.finditer(content):
            context_start = max(0, m.start() - 300)
            context = content[context_start:m.end() + 100]
            if re.search(r'input_buffer', context):
                # Avoid duplicates with previous patterns
                already_covered = False
                for c in candidates:
                    if c["file_path"] == file_path and c["start"] <= m.start(2) < c["end"]:
                        already_covered = True
                        break
                if not already_covered:
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "input_buffer_missing_restrict_qualifier",
                        "category": "buffer_interfaces",
                        "start": m.start(2),
                        "end": m.end(2),
                        "original": "__restrict",
                        "replacement": "",
                        "description": "Remove __restrict qualifier from declaration near input_buffer usage."
                    })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] != original:
        # Fallback: try to find it from the start position vicinity
        # This shouldn't happen with deterministic candidates, but be safe
        return new_files

    replacement = candidate["replacement"]
    new_content = content[:start] + replacement + content[end:]

    # Clean up potential double spaces left by removal
    # Only in the immediate vicinity of the mutation
    patch_start = max(0, start - 1)
    patch_end = min(len(new_content), start + len(replacement) + 1)
    patch_region = new_content[patch_start:patch_end]
    cleaned_region = re.sub(r'  +', ' ', patch_region)
    new_content = new_content[:patch_start] + cleaned_region + new_content[patch_end:]

    new_files[file_path] = new_content
    return new_files
