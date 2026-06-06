BUG_FAMILY = {
    "family_id": "BF132",
    "bug_type": "output_buffer_extent_mismatch",
    "category": "buffer_interfaces",
    "target_files": [
        "kernel source",
        "graph header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "output_buffer<int32, extents<",
        "adf::buffer_size",
        "extents<adf::inherited_extent>"
    ],
    "mutation_strategy": "Change the extents template parameter in the output_buffer declaration to a fixed numeric value that conflicts with the graph-level buffer_size constraint, or replace extents<adf::inherited_extent> with extents<128> when the graph specifies a different size, causing a static extent mismatch at compile time.",
    "repair_expectation": "Align the extents template parameter with the graph-level buffer_size specification, or restore extents<adf::inherited_extent> to allow runtime inheritance.",
    "validation_signal": "WSL Vitis/AIE compile failure with extent mismatch or buffer size incompatibility error.",
    "tags": [
        "buffer_interfaces",
        "buffer_size",
        "extents",
        "graph_kernel_mismatch",
        "output_buffer"
    ]
}

import re
import copy


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []

    for file_path, content in project_files.items():
        # Strategy 1: Replace extents<adf::inherited_extent> with extents<128> in output_buffer declarations
        pattern_inherited = re.compile(
            r'(output_buffer\s*<[^>]*,\s*)extents<\s*adf::inherited_extent\s*>'
        )
        for match in pattern_inherited.finditer(content):
            original = 'extents<adf::inherited_extent>'
            # Find the exact position of "extents<adf::inherited_extent>" within the match
            extent_start = content.find('extents<adf::inherited_extent>', match.start())
            if extent_start == -1:
                # Try with possible whitespace variations
                sub_match = re.search(r'extents<\s*adf::inherited_extent\s*>', content[match.start():match.end()])
                if sub_match:
                    extent_start = match.start() + sub_match.start()
                    extent_end = match.start() + sub_match.end()
                    original_text = content[extent_start:extent_end]
                else:
                    continue
            else:
                extent_end = extent_start + len('extents<adf::inherited_extent>')
                original_text = 'extents<adf::inherited_extent>'

            # Determine a conflicting size - use 128 as default mismatch value
            replacement = 'extents<128>'

            candidates.append({
                "file_path": file_path,
                "bug_type": "output_buffer_extent_mismatch",
                "category": "buffer_interfaces",
                "start": extent_start,
                "end": extent_end,
                "original": original_text,
                "replacement": replacement,
                "description": (
                    f"Replace '{original_text}' with '{replacement}' in output_buffer declaration, "
                    f"causing a static extent mismatch with graph-level buffer_size constraint."
                )
            })

        # Strategy 2: Replace extents<N> with a different numeric value in output_buffer declarations
        pattern_numeric = re.compile(
            r'(output_buffer\s*<[^>]*,\s*)extents<\s*(\d+)\s*>'
        )
        for match in pattern_numeric.finditer(content):
            numeric_val = int(match.group(2))
            # Find the extents<N> portion
            extent_pattern = re.compile(r'extents<\s*' + str(numeric_val) + r'\s*>')
            sub_match = extent_pattern.search(content, match.start())
            if sub_match and sub_match.start() < match.end():
                extent_start = sub_match.start()
                extent_end = sub_match.end()
                original_text = content[extent_start:extent_end]

                # Pick a conflicting value
                if numeric_val == 128:
                    new_val = 256
                elif numeric_val == 256:
                    new_val = 128
                else:
                    new_val = numeric_val * 2 if numeric_val > 0 else 128

                replacement = f'extents<{new_val}>'

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "output_buffer_extent_mismatch",
                    "category": "buffer_interfaces",
                    "start": extent_start,
                    "end": extent_end,
                    "original": original_text,
                    "replacement": replacement,
                    "description": (
                        f"Change extent from {numeric_val} to {new_val} in output_buffer declaration, "
                        f"creating a mismatch with graph-level buffer_size."
                    )
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)
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
