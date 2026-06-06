import re
import copy

BUG_FAMILY = {
    "family_id": "BF094",
    "bug_type": "rtp_async_missing_async_annotation",
    "category": "rtp_parameters",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["async(", "adf::async", "connect<parameter>"],
    "mutation_strategy": "Remove the async() constraint from an RTP port that the kernel expects to read asynchronously (via adf::rtpValue or trigger-less access), or add async() to a synchronous RTP that the kernel reads synchronously, causing a compile-time constraint violation.",
    "repair_expectation": "Add or remove the async() constraint to match the intended synchronous/asynchronous RTP behavior declared in the kernel and graph.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about RTP synchronization mode mismatch or missing async constraint.",
    "tags": ["async", "constraint", "rtp", "rtp_parameters", "synchronization"]
}


def _is_graph_header(path):
    """Heuristic: graph headers are .h/.hpp files likely containing graph definitions."""
    lower = path.lower()
    if not (lower.endswith('.h') or lower.endswith('.hpp')):
        return False
    return True


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        # Strategy 1: Remove existing async() constraint
        # Match patterns like: async(port_name) or adf::async(port_name)
        pattern_async = re.compile(
            r'((?:adf::)?async\s*\(\s*[^)]*\)\s*;?[ \t]*\n?)'
        )
        for m in pattern_async.finditer(content):
            candidates.append({
                "file_path": file_path,
                "bug_type": "rtp_async_missing_async_annotation",
                "category": "rtp_parameters",
                "start": m.start(),
                "end": m.end(),
                "original": m.group(0),
                "replacement": "",
                "description": "Remove async() constraint from RTP port, causing synchronization mode mismatch."
            })

        # Strategy 2: For connect<parameter> lines without async, add async() constraint
        # Match lines like: connect<parameter>(kernel.inout[0], port);
        # or: adf::connect<adf::parameter>(...)
        pattern_connect_param = re.compile(
            r'((?:adf::)?connect\s*<\s*(?:adf::)?parameter\s*>\s*\(\s*([^,]+),\s*([^)]+)\)\s*;)'
        )
        for m in pattern_connect_param.finditer(content):
            line_start = content.rfind('\n', 0, m.start()) + 1
            line_end = content.find('\n', m.end())
            if line_end == -1:
                line_end = len(content)
            # Check that there's no async() already associated nearby (within a few lines after)
            context_after = content[m.end():min(m.end() + 200, len(content))]
            # Extract the port name from second argument (likely the RTP port)
            port_arg = m.group(3).strip()
            # Check if async is already applied to this port nearby
            async_check = re.compile(r'(?:adf::)?async\s*\(\s*' + re.escape(port_arg) + r'\s*\)')
            if not async_check.search(content):
                # Add an async() constraint after the connect line
                insertion_point = m.end()
                # Find end of line
                eol = content.find('\n', m.end())
                if eol == -1:
                    eol = len(content)
                original_segment = content[m.end():eol]
                # We'll insert async() on the next line
                async_line = "\n    async(" + port_arg + ");"
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "rtp_async_missing_async_annotation",
                    "category": "rtp_parameters",
                    "start": m.end(),
                    "end": m.end(),
                    "original": "",
                    "replacement": async_line,
                    "description": "Add async() constraint to a synchronous RTP port, causing synchronization mode mismatch."
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text matches
    if content[start:end] != original:
        # Fallback: try to find it
        idx = content.find(original)
        if idx != -1:
            start = idx
            end = idx + len(original)
        else:
            # Cannot apply mutation safely, return unchanged
            return new_files

    new_content = content[:start] + replacement + content[end:]
    new_files[file_path] = new_content
    return new_files
