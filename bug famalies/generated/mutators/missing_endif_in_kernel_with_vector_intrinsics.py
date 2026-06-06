BUG_FAMILY = {
    "family_id": "BF014",
    "bug_type": "missing_endif_in_kernel_with_vector_intrinsics",
    "category": "header_guards_and_preprocessor",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#ifdef __AIESIM__",
        "#endif",
        "readincr_v",
        "writeincr_v",
        "shuffle_up",
        "shuffle_down"
    ],
    "mutation_strategy": "Remove a #endif that closes an #ifdef __AIESIM__ block surrounding simulation-specific readincr_v/writeincr_v or shuffle intrinsic code, causing all subsequent code to be conditionally compiled and leading to missing function definitions or unbalanced preprocessor errors.",
    "repair_expectation": "Add the missing #endif at the correct location to close the conditional block.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'unterminated #ifdef' or 'unexpected end of file' preprocessor error.",
    "tags": [
        "header_guards_and_preprocessor",
        "missing_endif",
        "preprocessor",
        "readincr_v",
        "shuffle",
        "writeincr_v"
    ]
}

import re


def _is_kernel_file(path):
    """Check if file looks like a kernel source or header."""
    exts = ('.cpp', '.cc', '.c', '.h', '.hpp', '.hh')
    return any(path.endswith(ext) for ext in exts)


def _has_vector_intrinsics(text):
    """Check if text contains any of the target vector intrinsics."""
    intrinsics = ['readincr_v', 'writeincr_v', 'shuffle_up', 'shuffle_down']
    return any(intr in text for intr in intrinsics)


def find_mutation_candidates(project_files):
    candidates = []
    intrinsic_pattern = re.compile(r'(readincr_v|writeincr_v|shuffle_up|shuffle_down)')

    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path):
            continue
        if not _has_vector_intrinsics(content):
            continue

        lines = content.split('\n')

        # Find #ifdef __AIESIM__ blocks and their matching #endif
        # Track nesting to find the correct #endif
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped == '#ifdef __AIESIM__' or stripped.startswith('#ifdef __AIESIM__'):
                ifdef_line = i
                # Find the block content and matching #endif
                nesting = 1
                j = i + 1
                block_has_intrinsic = False
                while j < len(lines) and nesting > 0:
                    line_s = lines[j].strip()
                    if line_s.startswith('#ifdef') or line_s.startswith('#ifndef') or line_s.startswith('#if '):
                        nesting += 1
                    elif line_s.startswith('#endif'):
                        nesting -= 1
                        if nesting == 0:
                            # Check if block between ifdef_line and j has vector intrinsics
                            block_text = '\n'.join(lines[ifdef_line+1:j])
                            if intrinsic_pattern.search(block_text):
                                block_has_intrinsic = True
                                # This #endif at line j is our target
                                # Calculate start/end character offsets
                                start_offset = sum(len(lines[k]) + 1 for k in range(j))
                                end_offset = start_offset + len(lines[j])
                                original_line = lines[j]

                                # Include trailing newline in removal if present
                                # We remove the entire line (including newline)
                                candidates.append({
                                    "file_path": file_path,
                                    "bug_type": "missing_endif_in_kernel_with_vector_intrinsics",
                                    "category": "header_guards_and_preprocessor",
                                    "start": start_offset,
                                    "end": end_offset,
                                    "original": original_line,
                                    "replacement": "",
                                    "description": (
                                        f"Remove #endif at line {j+1} that closes "
                                        f"'#ifdef __AIESIM__' block (line {ifdef_line+1}) "
                                        f"containing vector intrinsics, causing unterminated "
                                        f"preprocessor conditional."
                                    )
                                })
                            break
                    j += 1
                i = j + 1 if j < len(lines) else i + 1
            else:
                i += 1

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]
    lines = content.split('\n')

    # Find the line matching the original #endif at the correct offset
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Reconstruct by removing the line
    # Calculate which line index corresponds to start offset
    offset = 0
    target_line_idx = None
    for idx, line in enumerate(lines):
        if offset == start and line == original:
            target_line_idx = idx
            break
        offset += len(line) + 1  # +1 for newline

    if target_line_idx is not None:
        new_lines = lines[:target_line_idx] + lines[target_line_idx + 1:]
        new_files[file_path] = '\n'.join(new_lines)
    else:
        # Fallback: use string replacement at offset
        new_content = content[:start] + candidate["replacement"] + content[end:]
        # Clean up potential double newline
        new_content = new_content.replace('\n\n\n', '\n\n')
        new_files[file_path] = new_content

    return new_files
