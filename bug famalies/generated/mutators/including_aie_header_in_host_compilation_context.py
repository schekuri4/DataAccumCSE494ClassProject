import re
import copy

BUG_FAMILY = {
    "family_id": "BF007",
    "bug_type": "including_aie_header_in_host_compilation_context",
    "category": "include_headers",
    "target_files": [
        "graph source",
        "shared utility header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include <aie_api/aie.hpp>",
        "#include <aie_api/aie_adf.hpp>",
        "aie::vector",
        "aie::accum"
    ],
    "mutation_strategy": "Include <aie_api/aie.hpp> in a file that is compiled by the host (x86) compiler rather than the AIE compiler, such as the top-level graph source or a shared utility header that is also included in host code, causing architecture-specific intrinsic failures.",
    "repair_expectation": "Guard the AIE-specific include with #ifdef __AIE_ARCH__ or move it to a file only compiled by the AIE toolchain.",
    "validation_signal": "WSL Vitis/AIE compile failure during host compilation with errors about unknown intrinsics or architecture-specific types.",
    "tags": [
        "architecture_guard",
        "conditional_include",
        "host_vs_aie",
        "include_headers"
    ]
}


def _is_graph_or_shared_header(file_path):
    """Heuristic to identify graph source files or shared utility headers."""
    lower = file_path.lower()
    # Graph source files
    if "graph" in lower and (lower.endswith(".cpp") or lower.endswith(".h") or lower.endswith(".hpp")):
        return True
    # Shared utility headers
    if ("util" in lower or "common" in lower or "shared" in lower) and (lower.endswith(".h") or lower.endswith(".hpp")):
        return True
    # Any header file that might be shared
    if lower.endswith(".h") or lower.endswith(".hpp"):
        return True
    # Graph cpp files
    if lower.endswith(".cpp") and "graph" in lower:
        return True
    return False


def _file_is_likely_host_compiled(file_path, content):
    """Check if a file is likely compiled by the host compiler."""
    lower = file_path.lower()
    # Files with 'graph' in name that include adf.h are typically host-compiled graph definitions
    if "graph" in lower:
        return True
    # Shared headers
    if "util" in lower or "common" in lower or "shared" in lower:
        return True
    # If it includes host-side headers like iostream or adf.h
    if "#include" in content and ("adf.h" in content or "iostream" in content or "stdlib" in content):
        return True
    return False


def _already_has_aie_include(content):
    """Check if file already has an unguarded aie_api include."""
    # Check for unguarded #include <aie_api/aie.hpp>
    lines = content.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == '#include <aie_api/aie.hpp>' or stripped == '#include <aie_api/aie_adf.hpp>':
            # Check if it's already guarded
            if _is_guarded(lines, i):
                continue
            return True
    return False


def _is_guarded(lines, line_idx):
    """Check if a line is inside an #ifdef __AIE_ARCH__ guard."""
    depth = 0
    for i in range(line_idx - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("#endif"):
            depth += 1
        elif stripped.startswith("#ifdef") or stripped.startswith("#if "):
            if depth > 0:
                depth -= 1
            else:
                if "__AIE_ARCH__" in stripped or "__AIE__" in stripped:
                    return True
                return False
        elif stripped.startswith("#ifndef"):
            if depth > 0:
                depth -= 1
            else:
                return False
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_or_shared_header(file_path):
            continue
        if not _file_is_likely_host_compiled(file_path, content):
            continue

        # Strategy 1: If file already has a guarded AIE include, remove the guard
        lines = content.split('\n')
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped in ('#include <aie_api/aie.hpp>', '#include <aie_api/aie_adf.hpp>'):
                if _is_guarded(lines, i):
                    # Find the guard and remove it
                    guard_start = None
                    guard_end = None
                    depth = 0
                    for j in range(i - 1, -1, -1):
                        s = lines[j].strip()
                        if s.startswith("#endif"):
                            depth += 1
                        elif s.startswith("#ifdef") or s.startswith("#if ") or s.startswith("#ifndef"):
                            if depth > 0:
                                depth -= 1
                            else:
                                if "__AIE_ARCH__" in s or "__AIE__" in s:
                                    guard_start = j
                                break
                    if guard_start is not None:
                        # Find matching #endif
                        depth = 0
                        for j in range(guard_start + 1, len(lines)):
                            s = lines[j].strip()
                            if s.startswith("#ifdef") or s.startswith("#if ") or s.startswith("#ifndef"):
                                depth += 1
                            elif s.startswith("#endif"):
                                if depth > 0:
                                    depth -= 1
                                else:
                                    guard_end = j
                                    break

                        if guard_end is not None:
                            # Build original block and replacement (without guards)
                            original_block = '\n'.join(lines[guard_start:guard_end + 1])
                            inner_lines = lines[guard_start + 1:guard_end]
                            replacement_block = '\n'.join(inner_lines)

                            # Calculate character positions
                            start_pos = sum(len(lines[k]) + 1 for k in range(guard_start))
                            end_pos = start_pos + len(original_block)

                            candidates.append({
                                "file_path": file_path,
                                "bug_type": "including_aie_header_in_host_compilation_context",
                                "category": "include_headers",
                                "start": start_pos,
                                "end": end_pos,
                                "original": original_block,
                                "replacement": replacement_block,
                                "description": f"Remove #ifdef __AIE_ARCH__ guard around AIE include in {file_path}, exposing it to host compilation."
                            })

        # Strategy 2: If file does NOT have an AIE include, add one
        if not _already_has_aie_include(content):
            # Find a good insertion point - after existing includes
            include_pattern = re.compile(r'^#include\s+[<"].*[>"]', re.MULTILINE)
            matches = list(include_pattern.finditer(content))
            if matches:
                last_include = matches[-1]
                insert_start = last_include.start()
                insert_end = last_include.end()
                original = content[insert_start:insert_end]
                replacement = original + "\n#include <aie_api/aie.hpp>"

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "including_aie_header_in_host_compilation_context",
                    "category": "include_headers",
                    "start": insert_start,
                    "end": insert_end,
                    "original": original,
                    "replacement": replacement,
                    "description": f"Add unguarded #include <aie_api/aie.hpp> to {file_path} which is compiled by the host compiler."
                })
            elif content:
                newline = content.find('\n')
                anchor_end = newline + 1 if newline >= 0 else min(len(content), 1)
                original = content[:anchor_end]
                # Insert at beginning of file
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "including_aie_header_in_host_compilation_context",
                    "category": "include_headers",
                    "start": 0,
                    "end": anchor_end,
                    "original": original,
                    "replacement": "#include <aie_api/aie.hpp>\n" + original,
                    "description": f"Add unguarded #include <aie_api/aie.hpp> at the top of {file_path} which is compiled by the host compiler."
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

    # Verify the original text matches
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: try to find and replace the original string
        if original and original in content:
            new_content = content.replace(original, replacement, 1)
        else:
            # For insertion (empty original), just insert at position
            new_content = content[:start] + replacement + content[start:]

    new_files[file_path] = new_content
    return new_files
