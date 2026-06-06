import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF456",
    "bug_type": "unknown_kernel_create_symbol",
    "category": "compiler_diagnostics_driven_patterns",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::kernel::create(",
        "kernel::create(",
        "adf::source("
    ],
    "mutation_strategy": "Change the symbol passed to kernel::create so it points at a function name that does not exist anywhere in scope, or a nearby name with a spelling/namespace mistake that leaves the graph elaboration without a resolvable kernel entry point.",
    "repair_expectation": "Restore the exact declared kernel function symbol and signature used by kernel::create.",
    "validation_signal": "WSL Vitis/AIE compile failure with an unresolved kernel symbol, undefined reference, or no matching kernel::create overload.",
    "tags": [
        "compile_error",
        "compiler_diagnostics_driven_patterns",
        "graph_config",
        "kernel_create",
        "symbol_mismatch",
        "undefined_reference"
    ]
}


def _is_graph_file(path: str) -> bool:
    """Heuristic: graph headers and sources typically have .h/.hpp/.cpp extensions
    and often contain 'graph' in name or contain kernel::create calls."""
    lower = path.lower()
    extensions = ('.h', '.hpp', '.cpp', '.cc', '.cxx')
    return any(lower.endswith(ext) for ext in extensions)


def _mangle_symbol(symbol: str) -> str:
    """Create a plausible but non-existent symbol name."""
    # Strategy: introduce a typo by duplicating a character or changing case
    if len(symbol) > 2:
        # Insert a typo near the middle
        mid = len(symbol) // 2
        return symbol[:mid] + symbol[mid] + symbol[mid:]
    elif len(symbol) > 0:
        return symbol + "_undefined"
    return "nonexistent_kernel_func"


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    # Pattern to match kernel::create( or adf::kernel::create( with the symbol argument
    # Captures the full expression including the function name argument
    kernel_create_pattern = re.compile(
        r'((?:adf::)?kernel::create\s*\(\s*)'  # prefix: kernel::create(
        r'([A-Za-z_][A-Za-z0-9_:]*)'           # the kernel function symbol
    )

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Check if file contains any of our match targets
        has_target = any(target in content for target in [
            "adf::kernel::create(",
            "kernel::create(",
        ])
        if not has_target:
            continue

        for match in kernel_create_pattern.finditer(content):
            prefix = match.group(1)
            symbol = match.group(2)

            # Skip if symbol looks like a template parameter or is too short
            if len(symbol) < 2:
                continue

            mangled = _mangle_symbol(symbol)

            full_original = prefix + symbol
            full_replacement = prefix + mangled

            start = match.start()
            end = match.start() + len(full_original)

            candidate = {
                "file_path": file_path,
                "bug_type": "unknown_kernel_create_symbol",
                "category": "compiler_diagnostics_driven_patterns",
                "start": start,
                "end": end,
                "original": full_original,
                "replacement": full_replacement,
                "description": (
                    f"Changed kernel::create symbol '{symbol}' to non-existent "
                    f"'{mangled}' to produce an unresolved kernel symbol error."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
