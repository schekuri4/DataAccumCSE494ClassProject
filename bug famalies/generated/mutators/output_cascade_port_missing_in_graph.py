import re
import copy

BUG_FAMILY = {
    "family_id": "BF143",
    "bug_type": "output_cascade_port_missing_in_graph",
    "category": "cascade_streams",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "output_cascade",
        "port<output>",
        "adf::port<output>",
        "kernel::create"
    ],
    "mutation_strategy": "Remove or comment out the output_cascade port declaration in the graph for a kernel that has an output_cascade parameter, then attempt to connect it, causing an undeclared port reference in the connect statement.",
    "repair_expectation": "Re-add the output_cascade port declaration (e.g., port<output> cascout;) and ensure it is properly connected.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared identifier or missing port error from aiecompiler.",
    "tags": [
        "cascade_streams",
        "graph",
        "missing_port",
        "output_cascade",
        "port_declaration"
    ]
}


def _is_graph_header(file_path):
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    return lower.endswith('.h') or lower.endswith('.hpp')


def _file_has_graph_indicators(content):
    """Check if file content looks like an AIE graph header."""
    return ('kernel::create' in content or 'adf::kernel::create' in content) and \
           ('graph' in content.lower())


def find_mutation_candidates(project_files):
    candidates = []

    # Pattern to match output cascade port declarations like:
    #   port<output> cascout;
    #   adf::port<output> casc_out;
    # Possibly with various whitespace
    port_decl_pattern = re.compile(
        r'^([ \t]*)((?:adf::)?port\s*<\s*output\s*>\s+\w*casc\w*\s*;)',
        re.MULTILINE | re.IGNORECASE
    )

    # Broader pattern: any port<output> declaration that contains "casc" in the variable name
    # or is near output_cascade keywords
    port_decl_pattern2 = re.compile(
        r'^([ \t]*)((?:adf::)?port\s*<\s*output\s*>\s+(\w+)\s*;)',
        re.MULTILINE
    )

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue
        if not _file_has_graph_indicators(content):
            continue

        # First try: look for port<output> declarations with "casc" in the name
        for match in port_decl_pattern.finditer(content):
            indent = match.group(1)
            decl_line = match.group(2)
            full_match = match.group(0)
            start = match.start()
            end = match.end()

            # Verify there's a connect statement referencing this port variable
            # Extract variable name
            var_match = re.search(r'port\s*<\s*output\s*>\s+(\w+)', decl_line)
            if var_match:
                var_name = var_match.group(1)
                # Check if this variable is used in a connect statement
                if re.search(r'connect\s*[<(].*' + re.escape(var_name), content) or \
                   re.search(re.escape(var_name), content[end:]):
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "output_cascade_port_missing_in_graph",
                        "category": "cascade_streams",
                        "start": start,
                        "end": end,
                        "original": full_match,
                        "replacement": indent + "// " + decl_line + "  // MUTATED: removed output_cascade port",
                        "description": f"Comment out output_cascade port declaration '{decl_line.strip()}' to cause undeclared port reference error."
                    })

        # If no cascade-specific ports found, look for any port<output> that might be cascade-related
        # by checking if 'output_cascade' or 'cascade' appears in the file
        if not any(c["file_path"] == file_path for c in candidates):
            if 'cascade' in content.lower() or 'output_cascade' in content.lower():
                for match in port_decl_pattern2.finditer(content):
                    indent = match.group(1)
                    decl_line = match.group(2)
                    var_name = match.group(3)
                    full_match = match.group(0)
                    start = match.start()
                    end = match.end()

                    # Check if this port is connected to something cascade-related
                    cascade_connect = re.search(
                        r'(output_cascade|cascade).*' + re.escape(var_name) + r'|' +
                        re.escape(var_name) + r'.*(output_cascade|cascade)',
                        content, re.IGNORECASE
                    )
                    if cascade_connect:
                        candidates.append({
                            "file_path": file_path,
                            "bug_type": "output_cascade_port_missing_in_graph",
                            "category": "cascade_streams",
                            "start": start,
                            "end": end,
                            "original": full_match,
                            "replacement": indent + "// " + decl_line + "  // MUTATED: removed output_cascade port",
                            "description": f"Comment out output cascade port declaration '{decl_line.strip()}' to cause undeclared port reference error."
                        })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    replacement = candidate["replacement"]
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
