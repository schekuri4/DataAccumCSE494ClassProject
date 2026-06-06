import re
import copy

BUG_FAMILY = {
    "family_id": "BF123",
    "bug_type": "margin_exceeds_window_size",
    "category": "window_interfaces",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["margin", "connect<window<", "adf::connect<adf::window<"],
    "mutation_strategy": "Set the margin parameter in a window connection (e.g., connect<window<256, 512>>) so that the margin value exceeds or equals the window size, which is architecturally invalid.",
    "repair_expectation": "Reduce the margin to be strictly less than the window size and a valid multiple of the element size.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating margin size exceeds or equals window size during aiecompiler elaboration.",
    "tags": [
        "compile_time", "graph", "margin", "overflow",
        "window_interfaces", "window_size"
    ]
}

# Pattern to match window connections with a margin parameter
# Matches both `window<SIZE, MARGIN>` and `adf::window<SIZE, MARGIN>`
_WINDOW_PATTERN = re.compile(
    r'((?:adf::)?window\s*<\s*)(\d+)\s*,\s*(\d+)(\s*>)'
)


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files, or contain 'graph' in name."""
    lower = file_path.lower()
    if lower.endswith(('.h', '.hpp', '.hh', '.hxx')):
        return True
    if 'graph' in lower:
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Look for window<SIZE, MARGIN> patterns where margin < window_size
        for match in _WINDOW_PATTERN.finditer(content):
            window_size_str = match.group(2)
            margin_str = match.group(3)

            try:
                window_size = int(window_size_str)
                margin = int(margin_str)
            except ValueError:
                continue

            # Only mutate if currently valid (margin < window_size)
            if margin >= window_size:
                continue  # Already buggy, skip

            # Create a margin that exceeds the window size
            # Use window_size * 2 to clearly exceed
            new_margin = window_size * 2

            original_text = match.group(0)
            replacement_text = f"{match.group(1)}{window_size_str}, {new_margin}{match.group(4)}"

            candidates.append({
                "file_path": file_path,
                "bug_type": "margin_exceeds_window_size",
                "category": "window_interfaces",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Changed margin from {margin} to {new_margin} in window<{window_size}, {margin}>, "
                    f"making margin exceed window size ({new_margin} >= {window_size})."
                )
            })

    # Also handle cases where there's a window with only size (no margin) inside a connect
    # and we could add a margin, but the primary strategy is to modify existing margins.
    # Additionally, look for single-parameter windows where we can add an invalid margin.
    _WINDOW_NO_MARGIN = re.compile(
        r'((?:adf::)?connect\s*<\s*(?:adf::)?window\s*<\s*)(\d+)(\s*>\s*>)'
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        for match in _WINDOW_NO_MARGIN.finditer(content):
            window_size_str = match.group(2)
            try:
                window_size = int(window_size_str)
            except ValueError:
                continue

            if window_size <= 0:
                continue

            # Add a margin that equals the window size (invalid)
            new_margin = window_size

            original_text = match.group(0)
            replacement_text = f"{match.group(1)}{window_size_str}, {new_margin}{match.group(3)}"

            candidates.append({
                "file_path": file_path,
                "bug_type": "margin_exceeds_window_size",
                "category": "window_interfaces",
                "start": match.start(),
                "end": match.end(),
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Added margin {new_margin} to window<{window_size}>, "
                    f"making margin equal to window size ({new_margin} >= {window_size})."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    file_path = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    new_files = dict(project_files)
    content = new_files[file_path]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
