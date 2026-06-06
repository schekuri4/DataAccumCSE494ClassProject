import re
import copy


BUG_FAMILY = {
    "family_id": "BF069",
    "bug_type": "plio_connect_to_nonexistent_kernel_port_index",
    "category": "graph_endpoint_indices",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_plio::create",
        "output_plio::create",
        "connect<>(plin[",
        "connect<>(plout[",
        ".in[",
        ".out[",
    ],
    "mutation_strategy": "Connect a PLIO to a kernel port index that does not exist. For example, connect a single input_plio to k[0].in[3] when the kernel only has in[0] and in[1] defined.",
    "repair_expectation": "Change the kernel port index in the connect statement to a valid port that exists in the kernel's interface.",
    "validation_signal": "WSL Vitis/AIE compile failure with port index out of range or unresolved endpoint during graph elaboration.",
    "tags": ["connect", "graph_endpoint_indices", "kernel_port", "nonexistent_index", "plio"],
}


def _is_graph_file(path):
    """Heuristic to identify graph header or source files."""
    lower = path.lower()
    # Common patterns for AIE graph files
    if "graph" in lower:
        return True
    if lower.endswith(".h") or lower.endswith(".hpp") or lower.endswith(".cpp") or lower.endswith(".cc"):
        return True
    return False


def _has_plio_context(content):
    """Check if file contains PLIO-related constructs."""
    plio_indicators = ["input_plio", "output_plio", "plio", "PLIO"]
    return any(ind in content for ind in plio_indicators)


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match connect statements involving PLIO and kernel ports
    # Examples:
    #   connect<>(plin[0].out[0], k[0].in[0]);
    #   connect<>(k[0].out[0], plout[0].in[0]);
    #   connect(plin[0].out[0], kernel0.in[0]);
    #   adf::connect<>(plin.out[0], fir.in[0]);

    # Pattern 1: connect statements where a kernel port (.in[N] or .out[N]) is the destination/source
    # We look for .in[<number>] or .out[<number>] that appears as a kernel port (not plio port)
    # In typical AIE graphs, PLIO connects to kernel like:
    #   connect<>(plio_name.out[0], kernel_name.in[N])
    #   connect<>(kernel_name.out[N], plio_name.in[0])

    # Regex to find connect statements with kernel port indices
    # Match the full connect statement and capture the kernel port index
    connect_pattern = re.compile(
        r'(connect\s*<[^>]*>\s*\(\s*'  # connect<...>(
        r'[^,]+,'                        # first argument (source)
        r'\s*'
        r'[^)]*?'                        # start of second argument
        r')'
        r'(\.(in|out)\[(\d+)\])'         # kernel port .in[N] or .out[N] in second arg
        r'(\s*\)\s*;)'                   # closing );
    )

    # Also match when kernel port is in the first argument (kernel.out[N] -> plio)
    connect_pattern_first_arg = re.compile(
        r'(connect\s*<[^>]*>\s*\(\s*'   # connect<...>(
        r'[^,]*?'                        # start of first argument
        r')'
        r'(\.(in|out)\[(\d+)\])'         # kernel port .in[N] or .out[N]
        r'(\s*,[^)]+\)\s*;)'            # , second_arg);
    )

    # Simpler general pattern: any .in[N] or .out[N] in a connect line involving plio
    line_pattern = re.compile(
        r'^(.*connect.*(?:plio|plin|plout|PLIO).*)$', re.MULTILINE
    )

    # Port index pattern within a line
    port_index_pattern = re.compile(r'\.(in|out)\[(\d+)\]')

    for path, content in project_files.items():
        if not _is_graph_file(path):
            continue

        # Look for connect lines that involve PLIO
        for line_match in line_pattern.finditer(content):
            line = line_match.group(0)
            line_start = line_match.start()

            # Find all port indices in this line
            port_matches = list(port_index_pattern.finditer(line))

            if len(port_matches) < 2:
                # Need at least two port references (plio port + kernel port)
                # If only one, still try to mutate it if it's likely a kernel port
                if len(port_matches) == 1:
                    pm = port_matches[0]
                    # Skip if this looks like a PLIO's own port (usually .out[0] or .in[0] on plio)
                    # Check if the port is preceded by a plio-like name
                    before_port = line[:pm.start()]
                    if re.search(r'(?:plio|plin|plout|PLIO)\w*\s*$', before_port):
                        continue
                    # This is likely a kernel port - mutate it
                    port_type = pm.group(1)
                    original_index = int(pm.group(2))
                    # Create a nonexistent index
                    new_index = original_index + 3  # offset by 3 to likely exceed valid range
                    if new_index == original_index:
                        new_index = original_index + 5

                    original_text = pm.group(0)
                    replacement_text = f".{port_type}[{new_index}]"

                    abs_start = line_start + pm.start()
                    abs_end = line_start + pm.end()

                    candidates.append({
                        "file_path": path,
                        "bug_type": "plio_connect_to_nonexistent_kernel_port_index",
                        "category": "graph_endpoint_indices",
                        "start": abs_start,
                        "end": abs_end,
                        "original": original_text,
                        "replacement": replacement_text,
                        "description": (
                            f"Changed kernel port index from {original_text} to "
                            f"{replacement_text} in PLIO connect statement, "
                            f"creating a reference to a nonexistent port index."
                        ),
                    })
                continue

            # With multiple port references, identify which is the kernel port
            # Heuristic: the port NOT immediately preceded by a plio-like identifier is the kernel port
            for pm in port_matches:
                before_port = line[:pm.start()]
                # If preceded by plio/plin/plout name, skip (it's the PLIO's port)
                if re.search(r'(?:plio|plin|plout|PLIO)\w*\s*$', before_port, re.IGNORECASE):
                    continue

                port_type = pm.group(1)
                original_index = int(pm.group(2))
                # Create a nonexistent index (add 3 to go out of range)
                new_index = original_index + 3
                if new_index == original_index:
                    new_index = original_index + 5

                original_text = pm.group(0)
                replacement_text = f".{port_type}[{new_index}]"

                abs_start = line_start + pm.start()
                abs_end = line_start + pm.end()

                candidates.append({
                    "file_path": path,
                    "bug_type": "plio_connect_to_nonexistent_kernel_port_index",
                    "category": "graph_endpoint_indices",
                    "start": abs_start,
                    "end": abs_end,
                    "original": original_text,
                    "replacement": replacement_text,
                    "description": (
                        f"Changed kernel port index from {original_text} to "
                        f"{replacement_text} in PLIO connect statement, "
                        f"creating a reference to a nonexistent port index."
                    ),
                })

    # If no PLIO-specific connect lines found, try broader approach
    if not candidates:
        # Look for any connect statement with .in[N] or .out[N] in graph-like files
        general_connect_pattern = re.compile(
            r'^(.*connect.*)$', re.MULTILINE
        )
        for path, content in project_files.items():
            if not _is_graph_file(path):
                continue
            if not _has_plio_context(content):
                continue

            for line_match in general_connect_pattern.finditer(content):
                line = line_match.group(0)
                line_start = line_match.start()

                port_matches = list(port_index_pattern.finditer(line))
                for pm in port_matches:
                    port_type = pm.group(1)
                    original_index = int(pm.group(2))
                    new_index = original_index + 3

                    original_text = pm.group(0)
                    replacement_text = f".{port_type}[{new_index}]"

                    if original_text == replacement_text:
                        continue

                    abs_start = line_start + pm.start()
                    abs_end = line_start + pm.end()

                    candidates.append({
                        "file_path": path,
                        "bug_type": "plio_connect_to_nonexistent_kernel_port_index",
                        "category": "graph_endpoint_indices",
                        "start": abs_start,
                        "end": abs_end,
                        "original": original_text,
                        "replacement": replacement_text,
                        "description": (
                            f"Changed kernel port index from {original_text} to "
                            f"{replacement_text} in connect statement (file has PLIO context), "
                            f"creating a reference to a nonexistent port index."
                        ),
                    })
                    break  # One candidate per line is enough
                if candidates:
                    break

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate to the project files."""
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
