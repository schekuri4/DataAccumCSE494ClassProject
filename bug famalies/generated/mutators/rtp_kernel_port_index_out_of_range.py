import re
import copy


BUG_FAMILY = {
    "family_id": "BF097",
    "bug_type": "rtp_kernel_port_index_out_of_range",
    "category": "rtp_parameters",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "kernel::in[",
        "kernel::out[",
        "kernel::inout[",
        "connect<parameter>("
    ],
    "mutation_strategy": "Use an out-of-range port index when connecting an RTP (e.g., mykernel.in[3] when the kernel only has ports 0-2), causing a compile-time index-out-of-bounds error on the kernel port array.",
    "repair_expectation": "Use the correct port index that corresponds to the RTP parameter position in the kernel function signature.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about port index out of range or undefined port access.",
    "tags": [
        "compile_time",
        "kernel",
        "out_of_range",
        "port_index",
        "rtp",
        "rtp_parameters"
    ]
}


def _is_graph_header(filepath):
    """Heuristic: graph headers are .h/.hpp files likely containing graph definitions."""
    lower = filepath.lower()
    if lower.endswith(('.h', '.hpp')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to find RTP connect statements with kernel port indexing
    # Matches patterns like: connect<parameter>(kernel_inst.in[0], ...)
    # or connect<parameter>(..., kernel_inst.out[1])
    # We look for port accesses within connect<parameter> lines
    rtp_connect_pattern = re.compile(
        r'connect\s*<\s*parameter\s*>\s*\('
    )

    # Pattern to match kernel port access like: something.in[N], something.out[N], something.inout[N]
    port_access_pattern = re.compile(
        r'(\w+)\s*\.\s*(in|out|inout)\s*\[\s*(\d+)\s*\]'
    )

    for filepath, content in project_files.items():
        if not _is_graph_header(filepath):
            continue

        lines = content.split('\n')
        for line_idx, line in enumerate(lines):
            # Check if this line contains a connect<parameter> call
            if not rtp_connect_pattern.search(line):
                continue

            # Find all port accesses in this line
            for match in port_access_pattern.finditer(line):
                kernel_name = match.group(1)
                port_direction = match.group(2)
                port_index = int(match.group(3))
                original_expr = match.group(0)

                # Create a mutated index that is out of range
                # Increment by a value that makes it clearly out of range
                mutated_index = port_index + 3  # e.g., 0 -> 3, 1 -> 4, etc.

                mutated_expr = f"{kernel_name}.{port_direction}[{mutated_index}]"

                # Calculate start and end positions in the file
                line_start = sum(len(l) + 1 for l in lines[:line_idx])
                abs_start = line_start + match.start()
                abs_end = line_start + match.end()

                candidate = {
                    "file_path": filepath,
                    "bug_type": "rtp_kernel_port_index_out_of_range",
                    "category": "rtp_parameters",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original_expr,
                    "replacement": mutated_expr,
                    "description": (
                        f"Changed RTP kernel port index from "
                        f"{kernel_name}.{port_direction}[{port_index}] to "
                        f"{kernel_name}.{port_direction}[{mutated_index}] "
                        f"(out-of-range index) in connect<parameter> call at line {line_idx + 1}."
                    )
                }
                candidates.append(candidate)

    return candidates


def apply_mutation(project_files, candidate):
    mutated_files = dict(project_files)

    filepath = candidate["file_path"]
    content = mutated_files[filepath]
    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        mutated_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence in the file
        mutated_content = content.replace(original, replacement, 1)

    mutated_files[filepath] = mutated_content
    return mutated_files
