import re
import copy

BUG_FAMILY = {
    "family_id": "BF093",
    "bug_type": "rtp_array_size_mismatch_kernel_signature",
    "category": "rtp_parameters",
    "target_files": [
        "kernel source",
        "kernel header",
        "graph header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "int32 (&rtp)[",
        "int32* rtp",
        "connect<parameter>",
        "dimensions("
    ],
    "mutation_strategy": "Change the array size in the kernel function signature for an RTP array parameter (e.g., from int32 (&coeff)[16] to int32 (&coeff)[32]) without updating the corresponding dimensions() constraint in the graph, causing a compile-time size mismatch.",
    "repair_expectation": "Ensure the kernel RTP array parameter size matches the dimensions() specification in the graph constraint.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about RTP parameter size or dimensions mismatch.",
    "tags": [
        "array",
        "dimensions",
        "kernel_signature",
        "rtp",
        "rtp_parameters",
        "size_mismatch"
    ]
}


def _pick_different_size(original_size):
    """Pick a different array size to introduce a mismatch."""
    size = int(original_size)
    if size <= 1:
        return size * 2 if size > 0 else 8
    # Double the size, or halve if already large
    if size >= 64:
        return size // 2
    return size * 2


def find_mutation_candidates(project_files):
    """Find RTP array size parameters in kernel source/header files that can be mutated."""
    candidates = []

    # Pattern to match array reference parameters like: int32 (&name)[SIZE]
    # Also matches variations with different types (int32, int16, float, etc.)
    array_ref_pattern = re.compile(
        r'(\w+\s*\(\s*&\s*\w+\s*\)\s*\[)\s*(\d+)\s*(\])'
    )

    # More specific pattern for typed array references in function signatures
    # e.g., int32 (&coeff)[16]
    typed_array_ref_pattern = re.compile(
        r'((?:int32|int16|int8|uint32|uint16|uint8|int32_t|int16_t|uint32_t|uint16_t|float|cint16|cint32)\s*\(\s*&\s*\w+\s*\)\s*\[)\s*(\d+)\s*(\])'
    )
    pointer_dimension_pattern = re.compile(
        r'((?:dimensions|adf::dimensions)\s*\(\s*\w+\s*\)\s*=\s*\{\s*)(\d+)(\s*\})'
    )

    # Pattern for pointer-with-size style: type* name followed by size info
    # We focus on the array reference style as it's the primary match target

    # Identify kernel source and header files (typically .cc, .cpp, .h, .hpp)
    kernel_extensions = ('.cc', '.cpp', '.c', '.h', '.hpp', '.hh')

    for file_path, content in project_files.items():
        # Skip graph files - we only mutate kernel source/header
        # Graph files typically contain "graph" in name or have connect<parameter>
        is_likely_graph = ('graph' in file_path.lower() and
                          'connect<parameter>' in content or
                          'dimensions(' in content)

        if not any(file_path.endswith(ext) for ext in kernel_extensions):
            continue

        # We want to mutate kernel files, not graph files
        # A kernel file would have function definitions with array params
        # but not necessarily graph constructs

        # Search with typed pattern first (more specific)
        for match in typed_array_ref_pattern.finditer(content):
            original_size_str = match.group(2)
            original_size = int(original_size_str)
            new_size = _pick_different_size(original_size)

            if new_size == original_size:
                continue

            full_match = match.group(0)
            replacement = match.group(1) + str(new_size) + match.group(3)

            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": full_match,
                "replacement": replacement,
                "description": (
                    f"Changed RTP array size in kernel signature from "
                    f"{original_size} to {new_size} in '{file_path}', "
                    f"creating a mismatch with the graph dimensions() constraint."
                )
            })

        # If no typed matches, try the general pattern
        if not any(c["file_path"] == file_path for c in candidates):
            for match in array_ref_pattern.finditer(content):
                original_size_str = match.group(2)
                original_size = int(original_size_str)
                new_size = _pick_different_size(original_size)

                if new_size == original_size:
                    continue

                full_match = match.group(0)
                replacement = match.group(1) + str(new_size) + match.group(3)

                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": match.start(),
                    "end": match.end(),
                    "original": full_match,
                    "replacement": replacement,
                    "description": (
                        f"Changed RTP array size in kernel signature from "
                        f"{original_size} to {new_size} in '{file_path}', "
                        f"creating a mismatch with the graph dimensions() constraint."
                    )
                })

        for match in pointer_dimension_pattern.finditer(content):
            original_size = int(match.group(2))
            new_size = _pick_different_size(original_size)
            if new_size == original_size:
                continue
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(),
                "end": match.end(),
                "original": match.group(0),
                "replacement": match.group(1) + str(new_size) + match.group(3),
                "description": (
                    f"Changed RTP dimensions size from {original_size} to {new_size}, "
                    f"creating a mismatch with the kernel parameter contract."
                )
            })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a mutation to the project files, returning a new dict."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    if file_path not in new_files:
        return new_files

    content = new_files[file_path]
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = new_content
    return new_files
