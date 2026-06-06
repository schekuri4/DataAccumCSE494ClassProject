import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF030",
    "bug_type": "gmio_port_binding_to_kernel_port_error",
    "category": "graph_kernel_binding",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "GMIO::create(",
        "adf::GMIO",
        "connect<",
        "gmio"
    ],
    "mutation_strategy": (
        "Connect a GMIO port directly to a kernel port that expects a window or stream "
        "connection without proper intermediate buffering, or mismatch the GMIO burst length "
        "and buffer size with what the kernel window expects. Alternatively, use wrong GMIO "
        "constructor parameters (e.g., wrong burst_length not a power of 2, or wrong "
        "logical_name string that conflicts)."
    ),
    "repair_expectation": (
        "Correct the GMIO creation parameters and ensure proper connection topology between "
        "GMIO and kernel ports with matching buffer sizes."
    ),
    "validation_signal": "WSL Vitis/AIE compile failure with GMIO connection error or invalid burst_length parameter.",
    "tags": [
        "buffer_size",
        "burst_length",
        "connection",
        "gmio",
        "graph_kernel_binding",
        "port_binding"
    ]
}


def _is_graph_header(file_path: str) -> bool:
    """Heuristic: graph headers are .h or .hpp files with 'graph' in name or path."""
    lower = file_path.lower()
    if not (lower.endswith('.h') or lower.endswith('.hpp')):
        return False
    return True  # Consider all headers as potential graph headers


def _has_gmio_content(content: str) -> bool:
    """Check if file contains GMIO-related constructs."""
    for target in BUG_FAMILY["match_targets"]:
        if target.lower() in content.lower():
            return True
    return False


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue
        if not _has_gmio_content(content):
            continue

        # Strategy 1: Mutate GMIO::create() burst_length to non-power-of-2
        # Pattern: GMIO::create("name", burst_length, buffer_size)
        pattern_create = re.compile(
            r'(GMIO::create\s*\(\s*"[^"]*"\s*,\s*)(\d+)(\s*,\s*\d+\s*\))'
        )
        for m in pattern_create.finditer(content):
            burst_length = int(m.group(2))
            # Mutate to a non-power-of-2 value
            bad_burst = burst_length + 1
            # Make sure it's not a power of 2
            if bad_burst & (bad_burst - 1) == 0:
                bad_burst += 1
            replacement = m.group(1) + str(bad_burst) + m.group(3)
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": replacement,
                "description": (
                    f"Changed GMIO burst_length from {burst_length} (power of 2) to "
                    f"{bad_burst} (not power of 2), causing invalid GMIO parameter error."
                )
            })

        # Strategy 2: Mutate buffer_size in GMIO::create to mismatch kernel window
        pattern_create2 = re.compile(
            r'(GMIO::create\s*\(\s*"[^"]*"\s*,\s*\d+\s*,\s*)(\d+)(\s*\))'
        )
        for m in pattern_create2.finditer(content):
            buffer_size = int(m.group(2))
            # Halve the buffer size to create mismatch
            bad_size = buffer_size // 3 if buffer_size > 3 else 1
            replacement = m.group(1) + str(bad_size) + m.group(3)
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": replacement,
                "description": (
                    f"Changed GMIO buffer_size from {buffer_size} to {bad_size}, "
                    f"creating mismatch with kernel window size expectation."
                )
            })

        # Strategy 3: Change connect<> type for GMIO connections
        # Look for connect< ... > involving gmio
        # Pattern: connect< stream > or connect< window<...> > near gmio references
        pattern_connect = re.compile(
            r'(connect\s*<\s*)(window\s*<[^>]*>|stream)(\s*>\s*\([^)]*gmio[^)]*\))',
            re.IGNORECASE
        )
        for m in pattern_connect.finditer(content):
            conn_type = m.group(2).strip()
            if conn_type == "stream":
                new_type = "window<128>"
            else:
                new_type = "stream"
            replacement = m.group(1) + new_type + m.group(3)
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": replacement,
                "description": (
                    f"Changed GMIO connection type from '{conn_type}' to '{new_type}', "
                    f"creating port binding type mismatch."
                )
            })

        # Strategy 4: Direct connect of GMIO to kernel port - change connection target
        # Pattern: connect<...>(gmio_port.out[0], kernel_port.in[0])
        # or adf::connect(gmio.out[0], k.in[0])
        pattern_direct = re.compile(
            r'((?:adf::)?connect\s*(?:<[^>]*>)?\s*\(\s*)'
            r'([a-zA-Z_]\w*\s*\.\s*out\s*\[\s*\d+\s*\])'
            r'(\s*,\s*)'
            r'([a-zA-Z_]\w*\s*\.\s*in\s*\[\s*\d+\s*\])'
            r'(\s*\))'
        )
        for m in pattern_direct.finditer(content):
            # Check if this involves a gmio by looking at surrounding context
            line_start = content.rfind('\n', 0, m.start()) + 1
            line_end = content.find('\n', m.end())
            if line_end == -1:
                line_end = len(content)
            line_context = content[max(0, m.start() - 200):min(len(content), m.end() + 50)].lower()
            if 'gmio' not in line_context and 'gm' not in m.group(2).lower():
                continue

            # Mutate: swap the connection to directly connect without proper buffering
            # by removing any intermediate buffer reference or changing port index
            src_port = m.group(2)
            dst_port = m.group(4)
            # Change the destination port index to create binding error
            new_dst = re.sub(r'\[\s*(\d+)\s*\]', lambda x: f'[{int(x.group(1)) + 1}]', dst_port)
            if new_dst != dst_port:
                replacement = m.group(1) + src_port + m.group(3) + new_dst + m.group(5)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": m.start(),
                    "end": m.end(),
                    "original": m.group(0),
                    "replacement": replacement,
                    "description": (
                        f"Changed GMIO connection destination port index, "
                        f"creating kernel port binding mismatch."
                    )
                })

        # Strategy 5: Mutate adf::GMIO port declarations - change port type
        pattern_port_decl = re.compile(
            r'((?:adf::)?port\s*<\s*)((?:adf::)?input_gmio|(?:adf::)?output_gmio)(\s*>)'
        )
        for m in pattern_port_decl.finditer(content):
            port_type = m.group(2)
            if 'input' in port_type:
                new_type = port_type.replace('input', 'output')
            else:
                new_type = port_type.replace('output', 'input')
            replacement = m.group(1) + new_type + m.group(3)
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": replacement,
                "description": (
                    f"Swapped GMIO port direction from '{port_type}' to '{new_type}', "
                    f"causing port binding direction mismatch."
                )
            })

        # Strategy 6: Mutate GMIO logical name to create conflict
        pattern_name = re.compile(
            r'(GMIO::create\s*\(\s*")([^"]+)(")'
        )
        seen_names: list[str] = []
        name_matches = list(pattern_name.finditer(content))
        for m in name_matches:
            seen_names.append(m.group(2))

        if len(seen_names) >= 2:
            # Make second GMIO have same name as first (conflict)
            m = name_matches[1]
            conflicting_name = seen_names[0]
            if m.group(2) != conflicting_name:
                replacement = m.group(1) + conflicting_name + m.group(3)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": m.start(),
                    "end": m.end(),
                    "original": m.group(0),
                    "replacement": replacement,
                    "description": (
                        f"Changed GMIO logical_name from '{m.group(2)}' to "
                        f"'{conflicting_name}', creating a name conflict between GMIO ports."
                    )
                })

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate to the project files."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected location
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: find and replace first occurrence
        idx = content.find(original)
        if idx != -1:
            new_content = content[:idx] + candidate["replacement"] + content[idx + len(original):]
        else:
            # Cannot apply mutation, return unchanged
            new_content = content

    new_files[file_path] = new_content
    return new_files
