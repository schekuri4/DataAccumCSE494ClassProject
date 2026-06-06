import re
import copy

BUG_FAMILY = {
    "family_id": "BF121",
    "bug_type": "window_element_type_mismatch_kernel_vs_graph",
    "category": "window_interfaces",
    "target_files": ["kernel source", "graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_window<int32>",
        "input_window<cint16>",
        "output_window<float>",
        "connect<window<"
    ],
    "mutation_strategy": "Change the element type in the kernel function signature's input_window or output_window template parameter (e.g., input_window<int32> to input_window<cint16>) without updating the corresponding connect<window<N>> in the graph, or vice versa, creating a type mismatch between the kernel port declaration and the graph connection.",
    "repair_expectation": "Restore consistent element types between the kernel's window parameter template type and the graph's port/connect declarations so that byte sizes and types align.",
    "validation_signal": "WSL Vitis/AIE compile failure with type mismatch or port connection error during aiecompiler graph elaboration.",
    "tags": [
        "compile_time",
        "graph_connect",
        "kernel_signature",
        "type_mismatch",
        "window",
        "window_interfaces"
    ]
}

# AIE element types commonly used in window declarations
AIE_TYPES = ["int8", "int16", "int32", "int64", "uint8", "uint16", "uint32",
             "cint16", "cint32", "float", "cfloat", "bfloat16"]

# Pattern to match input_window<type> or output_window<type>
WINDOW_PARAM_PATTERN = re.compile(
    r'((?:input_window|output_window)\s*<\s*)(' + '|'.join(re.escape(t) for t in AIE_TYPES) + r')(\s*>)'
)


def _pick_different_type(original_type):
    """Pick a different AIE type to create a mismatch."""
    # Preference mapping for realistic mismatches
    preference = {
        "int32": "cint16",
        "cint16": "int32",
        "float": "int32",
        "int16": "int32",
        "int8": "int16",
        "cint32": "cint16",
        "cfloat": "float",
        "int64": "int32",
        "uint8": "int8",
        "uint16": "int16",
        "uint32": "int32",
        "bfloat16": "float",
    }
    if original_type in preference:
        return preference[original_type]
    # Fallback: pick int32 if not already, else cint16
    if original_type != "int32":
        return "int32"
    return "cint16"


def _is_kernel_source(filepath, content):
    """Heuristic: kernel source files contain window parameter declarations in function signatures."""
    if any(ext in filepath for ext in ['.cc', '.cpp', '.c', '.h', '.hpp']):
        if WINDOW_PARAM_PATTERN.search(content):
            return True
    return False


def _is_graph_header(filepath, content):
    """Heuristic: graph headers contain connect<window< declarations."""
    if any(ext in filepath for ext in ['.h', '.hpp', '.cpp', '.cc']):
        if 'connect<window<' in content or 'graph' in content.lower():
            return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        # Look for kernel source files with window parameters
        if not _is_kernel_source(filepath, content):
            continue

        # Find all window parameter type declarations
        for match in WINDOW_PARAM_PATTERN.finditer(content):
            original_type = match.group(2)
            replacement_type = _pick_different_type(original_type)

            original_text = match.group(0)
            replacement_text = match.group(1) + replacement_type + match.group(3)

            start = match.start()
            end = match.end()

            direction = "input_window" if "input_window" in match.group(1) else "output_window"

            candidate = {
                "file_path": filepath,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed kernel {direction} element type from '{original_type}' to "
                    f"'{replacement_type}' in '{filepath}' without updating the graph connection, "
                    f"creating a type mismatch between kernel port and graph connect declaration."
                )
            }
            candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_project_files = dict(project_files)  # shallow copy of the dict

    filepath = candidate["file_path"]
    content = new_project_files[filepath]

    # Verify the original text is at the expected position
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_project_files[filepath] = new_content
    return new_project_files
