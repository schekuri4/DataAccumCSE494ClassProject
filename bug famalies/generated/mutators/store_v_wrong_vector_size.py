import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF192",
    "bug_type": "store_v_wrong_vector_size",
    "category": "vector_load_store",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["aie::store_v(", "aie::vector<", "::size()"],
    "mutation_strategy": "Pass a vector of incorrect lane count to aie::store_v. For example, store an aie::vector<int32,16> through a call that was originally storing an aie::vector<int32,8>, or vice versa, creating a mismatch between the vector size and the expected store width.",
    "repair_expectation": "Change the vector variable or the store call so that the vector lane count matches the intended store width and pointer stride.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating vector size mismatch or no matching overload for store_v.",
    "tags": ["compile_error", "lane_count", "store_v", "vector_load_store", "vector_size"],
}


def _is_kernel_source(path: str) -> bool:
    """Heuristic: consider .cpp, .cc, .h, .hpp files as potential kernel sources."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.h', '.hpp', '.c'))


def _pick_different_size(size_str: str) -> str:
    """Given a lane count string, return a different valid AIE lane count."""
    try:
        size = int(size_str)
    except ValueError:
        return size_str

    # Common AIE vector lane counts
    common_sizes = [4, 8, 16, 32, 64, 128]
    # Pick a different size - prefer doubling or halving
    if size * 2 in common_sizes:
        return str(size * 2)
    elif size // 2 in common_sizes and size // 2 >= 4:
        return str(size // 2)
    else:
        # Just pick something different
        for s in common_sizes:
            if s != size:
                return str(s)
    return str(size * 2)  # fallback


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find aie::vector<type, N> declarations where the variable
        # is later used in aie::store_v, and mutate the vector declaration's lane count.
        
        # First, find all aie::store_v calls and extract the vector variable names used
        store_v_pattern = re.compile(r'aie::store_v\s*\(\s*[^,]+,\s*(\w+)')
        store_v_vars = set()
        for m in store_v_pattern.finditer(content):
            store_v_vars.add(m.group(1))

        # Find vector declarations for those variables and mutate their lane count
        # Pattern: aie::vector<type, N> var_name
        vec_decl_pattern = re.compile(
            r'(aie::vector\s*<\s*[^,>]+\s*,\s*)(\d+)(\s*>\s*)(\w+)'
        )
        for m in vec_decl_pattern.finditer(content):
            var_name = m.group(4)
            original_size = m.group(2)
            if var_name in store_v_vars:
                new_size = _pick_different_size(original_size)
                if new_size == original_size:
                    continue
                original_text = m.group(0)
                replacement_text = m.group(1) + new_size + m.group(3) + m.group(4)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "store_v_wrong_vector_size",
                    "category": "vector_load_store",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Changed vector lane count from {original_size} to {new_size} "
                        f"for variable '{var_name}' which is passed to aie::store_v, "
                        f"creating a vector size mismatch."
                    ),
                })

        # Strategy 2: Find aie::store_v calls where the vector argument is constructed
        # inline with aie::vector<type, N> and mutate the size there
        store_v_inline_pattern = re.compile(
            r'(aie::store_v\s*\([^,]+,\s*)'
            r'(aie::vector\s*<\s*[^,>]+\s*,\s*)(\d+)(\s*>)'
        )
        for m in store_v_inline_pattern.finditer(content):
            original_size = m.group(3)
            new_size = _pick_different_size(original_size)
            if new_size == original_size:
                continue
            original_text = m.group(0)
            replacement_text = m.group(1) + m.group(2) + new_size + m.group(4)
            candidates.append({
                "file_path": file_path,
                "bug_type": "store_v_wrong_vector_size",
                "category": "vector_load_store",
                "start": m.start(),
                "end": m.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed inline vector lane count from {original_size} to {new_size} "
                    f"in aie::store_v call, creating a vector size mismatch."
                ),
            })

        # Strategy 3: If we have store_v but couldn't match the above patterns,
        # try to find any aie::vector<type, N> declaration in a file that has store_v
        if not any(c["file_path"] == file_path for c in candidates):
            if 'aie::store_v' in content:
                for m in vec_decl_pattern.finditer(content):
                    original_size = m.group(2)
                    new_size = _pick_different_size(original_size)
                    if new_size == original_size:
                        continue
                    original_text = m.group(0)
                    replacement_text = m.group(1) + new_size + m.group(3) + m.group(4)
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "store_v_wrong_vector_size",
                        "category": "vector_load_store",
                        "start": m.start(),
                        "end": m.end(),
                        "original": original_text,
                        "replacement": replacement_text,
                        "description": (
                            f"Changed vector lane count from {original_size} to {new_size} "
                            f"for variable '{m.group(4)}' in file containing aie::store_v, "
                            f"likely creating a vector size mismatch at the store."
                        ),
                    })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
