import re


BUG_FAMILY = {
    "family_id": "BF_MANUAL_MMUL_001",
    "bug_type": "mmul_template_k_dimension_off_by_one",
    "category": "arithmetic_intrinsics",
    "target_files": ["kernel source", "kernel header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["aie::mmul<", "::aie::mmul<", "M", "K", "N"],
    "mutation_strategy": (
        "Change the K template dimension in an aie::mmul<M,K,N,...> alias or "
        "instantiation by one, producing an unsupported or inconsistent matrix "
        "multiply shape."
    ),
    "repair_expectation": "Restore the original K dimension so the MMUL shape matches the tile/kernel contract.",
    "validation_signal": "WSL Vitis/AIE compile failure with an mmul template constraint/static_assert or operand shape mismatch.",
    "tags": ["aie_mmul", "matrix", "shape", "template_parameter", "single_span"],
}


_MMUL_PATTERN = re.compile(
    r'((?:::)?aie::mmul\s*<\s*([^,>]+)\s*,\s*)([^,>]+)(\s*,\s*[^>]+>)'
)


def _is_kernel_file(path):
    return path.lower().endswith((".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh"))


def _mutate_k_dimension(value):
    stripped = value.strip()
    if stripped.isdigit():
        return str(int(stripped) + 1)
    if re.fullmatch(r'[A-Za-z_]\w*', stripped):
        return f"{stripped} + 1"
    return None


def find_mutation_candidates(project_files):
    candidates = []
    for file_path, content in project_files.items():
        if not _is_kernel_file(file_path):
            continue
        if "mmul" not in content:
            continue
        for match in _MMUL_PATTERN.finditer(content):
            original_k = match.group(3)
            replacement_k = _mutate_k_dimension(original_k)
            if replacement_k is None or replacement_k == original_k:
                continue
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(3),
                "end": match.end(3),
                "original": original_k,
                "replacement": replacement_k,
                "description": (
                    f"Changed aie::mmul K dimension from '{original_k.strip()}' "
                    f"to '{replacement_k}', creating an MMUL shape mismatch."
                ),
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
    if content[start:end] == original:
        new_files[file_path] = content[:start] + replacement + content[end:]
    else:
        new_files[file_path] = content.replace(original, replacement, 1)
    return new_files
