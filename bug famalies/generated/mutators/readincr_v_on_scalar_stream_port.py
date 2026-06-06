import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF106",
    "bug_type": "readincr_v_on_scalar_stream_port",
    "category": "stream_scalar_interfaces",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "readincr_v<",
        "input_stream_int32*",
        "input_stream_float*",
        "readincr("
    ],
    "mutation_strategy": "Replace a scalar readincr() call with readincr_v<N>() on a scalar input_stream pointer (not input_stream<type, N>), or use an incorrect lane count N that does not match the stream's declared vector width, causing a compile-time type error.",
    "repair_expectation": "Use the correct scalar readincr() for scalar stream ports, or change the port to a vector stream type matching the readincr_v lane count.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about readincr_v template parameter mismatch or incompatible stream type for vector read.",
    "tags": [
        "lane_mismatch",
        "readincr_v",
        "scalar_stream",
        "stream_scalar_interfaces",
        "vector_api"
    ]
}


def _is_kernel_source(file_path: str) -> bool:
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = file_path.lower()
    return any(lower.endswith(ext) for ext in ('.cpp', '.cc', '.c', '.h', '.hpp', '.hh'))


def _file_has_scalar_stream(content: str) -> bool:
    """Check if file declares or uses scalar stream pointers."""
    # Match input_stream_int32*, input_stream_float*, input_stream_int16*, etc.
    # but NOT input_stream<type, N> (templated vector streams)
    scalar_stream_pattern = re.compile(
        r'input_stream_(int32|int16|int8|float|cint16|cint32)\s*\*'
    )
    return bool(scalar_stream_pattern.search(content))


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    # Pattern to match scalar readincr() calls like:
    #   readincr(stream_ptr)
    #   readincr( stream_ptr )
    # We want to replace with readincr_v<N>(stream_ptr) which will cause type error
    readincr_pattern = re.compile(
        r'\breadincr\s*\(\s*([^)]+?)\s*\)'
    )

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        # Strategy 1: Find readincr() calls in files with scalar stream declarations
        # and replace them with readincr_v<N>() to cause type mismatch
        if _file_has_scalar_stream(content):
            for match in readincr_pattern.finditer(content):
                original = match.group(0)
                arg = match.group(1).strip()

                # Don't mutate if it's already readincr_v
                # (the regex shouldn't match readincr_v anyway due to word boundary)
                start = match.start()
                end = match.end()

                # Replace readincr(arg) with readincr_v<8>(arg)
                replacement = f"readincr_v<8>({arg})"

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "readincr_v_on_scalar_stream_port",
                    "category": "stream_scalar_interfaces",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Replace scalar readincr({arg}) with readincr_v<8>({arg}) "
                        f"on a scalar input_stream pointer, causing a compile-time "
                        f"type error due to vector read on scalar stream port."
                    )
                })

        # Strategy 2: Find readincr_v<N>() calls where the file uses scalar streams
        # and change the lane count to a mismatched value
        if _file_has_scalar_stream(content):
            readincr_v_pattern = re.compile(
                r'\breadincr_v\s*<\s*(\d+)\s*>\s*\(\s*([^)]+?)\s*\)'
            )
            for match in readincr_v_pattern.finditer(content):
                original = match.group(0)
                current_lanes = int(match.group(1))
                arg = match.group(2).strip()
                start = match.start()
                end = match.end()

                # Change lane count to something different
                new_lanes = 16 if current_lanes != 16 else 32

                replacement = f"readincr_v<{new_lanes}>({arg})"

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "readincr_v_on_scalar_stream_port",
                    "category": "stream_scalar_interfaces",
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Change readincr_v lane count from {current_lanes} to "
                        f"{new_lanes} on scalar stream port argument '{arg}', "
                        f"causing lane count mismatch error."
                    )
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, Any]) -> dict[str, str]:
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
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
