import re
import copy

BUG_FAMILY = {
    "family_id": "BF453",
    "bug_type": "misspelled_aie_intrinsic",
    "category": "function_and_member_naming",
    "target_files": [
        "kernel source",
        "kernel header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::zeros",
        "aie::broadcast",
        "aie::shuffle_up",
        "aie::shuffle_down",
        "readincr_v",
        "writeincr_v"
    ],
    "mutation_strategy": "Introduce a typo in an AIE intrinsic name used by the kernel, such as changing aie::zeros to aie::zeroes, aie::shuffle_down to aie::shuffle_dwon, or readincr_v to readinc_v. The goal is to surface errors where the code relies on exact intrinsic spelling.",
    "repair_expectation": "Restore the exact AIE intrinsic spelling so the compiler can resolve the symbol.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared identifier or no matching function errors for the misspelled intrinsic.",
    "tags": [
        "aie_api",
        "compile_error",
        "intrinsic",
        "spelling",
        "typo",
        "vector"
    ]
}


def _is_target_file(file_path):
    lower = file_path.lower()
    return lower.endswith(('.cpp', '.cc', '.c', '.cxx', '.h', '.hpp', '.hxx'))


def find_mutation_candidates(project_files):
    candidates = []
    replacements = {
        'aie::zeros': 'aie::zeroes',
        'aie::broadcast': 'aie::brodcast',
        'aie::shuffle_up': 'aie::shuffle_upp',
        'aie::shuffle_down': 'aie::shuffle_dwon',
        'readincr_v': 'readinc_v',
        'writeincr_v': 'writeinc_v'
    }

    for file_path, content in project_files.items():
        if not _is_target_file(file_path):
            continue
        for original, replacement in replacements.items():
            start = content.find(original)
            if start == -1:
                continue
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": start + len(original),
                "original": original,
                "replacement": replacement,
                "description": f"Misspelled intrinsic {original} as {replacement} in {file_path}."
            })
            break

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
        new_content = content[:start] + replacement + content[end:]
    else:
        new_content = content.replace(original, replacement, 1)
    new_files[file_path] = new_content
    return new_files
