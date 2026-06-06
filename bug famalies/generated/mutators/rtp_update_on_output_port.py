import re
import copy

BUG_FAMILY = {
    "family_id": "BF092",
    "bug_type": "rtp_update_on_output_port",
    "category": "rtp_parameters",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["update(", "read(", "port<direction::out>", "port<direction::in>"],
    "mutation_strategy": "Call graph::update() on a port that is declared as direction::out (an output RTP), or call graph::read() on a port declared as direction::in (an input RTP), producing a compile-time type error.",
    "repair_expectation": "Use update() only on input RTP ports and read() only on output RTP ports, matching the port direction declaration.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about incompatible RTP access API for the given port direction.",
    "tags": ["api_mismatch", "direction", "read", "rtp", "rtp_parameters", "update"]
}


def _is_graph_file(path):
    """Heuristic: graph headers (.h/.hpp) and graph sources (.cpp/.cc) typically contain 'graph' in name or content."""
    lower = path.lower()
    exts = ('.h', '.hpp', '.cpp', '.cc', '.c')
    return any(lower.endswith(ext) for ext in exts)


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Strategy 1: Find update() calls and check if the port argument is declared as direction::out
        # Strategy 2: Find read() calls and check if the port argument is declared as direction::in

        # First, collect port declarations and their directions
        # Pattern: port<direction::in> portname or port<direction::out> portname
        port_dirs = {}
        port_decl_pattern = re.compile(r'port<\s*direction::(in|out)\s*>\s+(\w+)')
        for m in port_decl_pattern.finditer(content):
            direction = m.group(1)
            port_name = m.group(2)
            port_dirs[port_name] = direction

        # Find update() calls - pattern like: update(portname, ...) or .update(portname, ...)
        update_pattern = re.compile(r'(\b\w*\.?\s*update\s*\(\s*)(\w+)')
        for m in update_pattern.finditer(content):
            port_name = m.group(2)
            if port_name in port_dirs and port_dirs[port_name] == 'in':
                # This is a correct usage: update on input port
                # Mutate to read() to break it
                full_match = m.group(0)
                replacement = full_match.replace('update', 'read', 1)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "rtp_update_on_output_port",
                    "category": "rtp_parameters",
                    "start": m.start(),
                    "end": m.end(),
                    "original": full_match,
                    "replacement": replacement,
                    "description": f"Changed update() to read() on input RTP port '{port_name}', which is invalid for direction::in ports."
                })

        # Find read() calls
        read_pattern = re.compile(r'(\b\w*\.?\s*read\s*\(\s*)(\w+)')
        for m in read_pattern.finditer(content):
            port_name = m.group(2)
            if port_name in port_dirs and port_dirs[port_name] == 'out':
                # This is a correct usage: read on output port
                # Mutate to update() to break it
                full_match = m.group(0)
                replacement = full_match.replace('read', 'update', 1)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "rtp_update_on_output_port",
                    "category": "rtp_parameters",
                    "start": m.start(),
                    "end": m.end(),
                    "original": full_match,
                    "replacement": replacement,
                    "description": f"Changed read() to update() on output RTP port '{port_name}', which is invalid for direction::out ports."
                })

        # Also handle the case where update is already called on an out port or read on in port
        # but more commonly we want to CREATE the bug from correct code.
        # Additional: if we find update() on a port not in our map, or if there are no
        # candidates yet, try a simpler approach - just swap update<->read in any RTP call
        if not candidates:
            # Broader pattern: any line with update( or read( that references known ports
            for m in update_pattern.finditer(content):
                port_name = m.group(2)
                if port_name in port_dirs and port_dirs[port_name] == 'out':
                    # Already buggy or intentional - we can still offer to ensure it's buggy
                    pass  # skip, already wrong
                elif port_name in port_dirs:
                    pass  # already handled above

            for m in read_pattern.finditer(content):
                port_name = m.group(2)
                if port_name in port_dirs and port_dirs[port_name] == 'in':
                    pass  # already wrong

        # Fallback: if no port declarations found but we see update/read calls,
        # try swapping them anyway (less precise but still useful)
        if not candidates and not port_dirs:
            # Look for any update() call and swap to read()
            simple_update = re.compile(r'\bupdate\s*\(')
            for m in simple_update.finditer(content):
                original = content[m.start():m.end()]
                replacement = original.replace('update', 'read', 1)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "rtp_update_on_output_port",
                    "category": "rtp_parameters",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": "Changed update() to read() on RTP port call, potentially mismatching port direction."
                })

            simple_read = re.compile(r'\bread\s*\(')
            for m in simple_read.finditer(content):
                original = content[m.start():m.end()]
                replacement = original.replace('read', 'update', 1)
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "rtp_update_on_output_port",
                    "category": "rtp_parameters",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": "Changed read() to update() on RTP port call, potentially mismatching port direction."
                })

    return candidates


def apply_mutation(project_files, candidate):
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
