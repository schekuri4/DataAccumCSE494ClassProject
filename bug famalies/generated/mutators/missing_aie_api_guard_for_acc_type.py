BUG_FAMILY = {
    "family_id": "BF011",
    "bug_type": "missing_aie_api_guard_for_acc_type",
    "category": "header_guards_and_preprocessor",
    "target_files": [
        "kernel header",
        "kernel source"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "#include <aie_api/aie.hpp>",
        "aie::accum<acc48",
        "aie::accum<acc80",
        "#ifdef __AIE_ARCH__"
    ],
    "mutation_strategy": "Wrap the #include <aie_api/aie.hpp> inside an #ifdef __AIE_ARCH__ guard but invert the condition to #ifndef __AIE_ARCH__, causing accumulator types like acc48/acc80 to be undefined when compiling for AIE targets.",
    "repair_expectation": "Change #ifndef __AIE_ARCH__ to #ifdef __AIE_ARCH__ or remove the guard entirely so aie_api headers are included during AIE compilation.",
    "validation_signal": "WSL Vitis/AIE compile failure with errors about undeclared acc48/acc80 types or aie namespace not found.",
    "tags": [
        "acc48",
        "acc80",
        "aie_api",
        "header_guards_and_preprocessor",
        "ifdef",
        "inverted_guard"
    ]
}

import re
import copy


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []
    
    # Pattern to match #include <aie_api/aie.hpp> possibly with surrounding whitespace
    include_pattern = re.compile(
        r'^([ \t]*#\s*include\s*<aie_api/aie\.hpp>[ \t]*\n?)',
        re.MULTILINE
    )
    
    # Pattern to detect if the include is already wrapped in an #ifdef __AIE_ARCH__ guard
    already_guarded_pattern = re.compile(
        r'#\s*ifdef\s+__AIE_ARCH__\s*\n\s*#\s*include\s*<aie_api/aie\.hpp>',
        re.MULTILINE
    )
    
    # Pattern to detect if already wrapped with #ifndef __AIE_ARCH__ (already mutated)
    already_inverted_pattern = re.compile(
        r'#\s*ifndef\s+__AIE_ARCH__\s*\n\s*#\s*include\s*<aie_api/aie\.hpp>',
        re.MULTILINE
    )
    
    # Check for accumulator type usage pattern to confirm this is a relevant file
    acc_pattern = re.compile(r'aie::accum<acc(48|80)')
    
    for file_path, content in project_files.items():
        # Only target kernel headers and sources (common extensions)
        if not any(file_path.endswith(ext) for ext in ['.h', '.hpp', '.cpp', '.cc', '.c']):
            continue
        
        # Must contain the aie_api include
        match = include_pattern.search(content)
        if not match:
            continue
        
        # Skip if already guarded (either correctly or already inverted)
        if already_guarded_pattern.search(content) or already_inverted_pattern.search(content):
            continue
        
        # Check if this file or any other file in the project uses acc48/acc80
        # (the file with the include is the target regardless, but we prefer files
        # that are clearly kernel-related)
        has_acc_usage = acc_pattern.search(content)
        # Also check other files for acc usage referencing this header
        if not has_acc_usage:
            for other_path, other_content in project_files.items():
                if acc_pattern.search(other_content):
                    has_acc_usage = True
                    break
        
        # We still produce a candidate even without acc usage, but it's more relevant with it
        original = match.group(1)
        start = match.start()
        end = match.end()
        
        # Build the mutated replacement: wrap with inverted guard
        indent = ""
        indent_match = re.match(r'^([ \t]*)', original)
        if indent_match:
            indent = indent_match.group(1)
        
        replacement = (
            f"{indent}#ifndef __AIE_ARCH__\n"
            f"{original}"
            f"{indent}#endif\n"
        )
        
        candidates.append({
            "file_path": file_path,
            "bug_type": BUG_FAMILY["bug_type"],
            "category": BUG_FAMILY["category"],
            "start": start,
            "end": end,
            "original": original,
            "replacement": replacement,
            "description": (
                f"Wrap '#include <aie_api/aie.hpp>' in an inverted '#ifndef __AIE_ARCH__' guard "
                f"in {file_path}, causing acc48/acc80 types to be undefined on AIE targets."
            )
        })
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    
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
