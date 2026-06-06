import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF096",
    "bug_type": "rtp_connect_missing_parameter_template",
    "category": "rtp_parameters",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "connect<parameter>(",
        "connect<window<",
        "connect<stream,"
    ],
    "mutation_strategy": "Replace connect<parameter>(...) with connect<window<N>>(...) or connect<stream>(...) for an RTP port connection, using an incorrect connection type template that is incompatible with RTP ports.",
    "repair_expectation": "Use connect<parameter>(...) for RTP port connections instead of window or stream connection types.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about invalid connection type for parameter port or template argument mismatch.",
    "tags": ["connect", "parameter", "rtp", "rtp_parameters", "template", "window"]
}


def _is_graph_header(file_path: str) -> bool:
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    # Typical graph headers: .h or .hpp files, often containing 'graph' in name
    if lower.endswith(('.h', '.hpp')):
        return True
    return False


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []
    
    # Pattern to match connect<parameter>(...) calls
    # This matches the full connect<parameter>(args) expression
    pattern = re.compile(
        r'connect\s*<\s*parameter\s*>\s*\('
    )
    
    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue
        
        for match in pattern.finditer(content):
            start = match.start()
            end = match.end()
            original = match.group(0)
            
            # Generate two replacement options, alternate between them based on position
            # Option 1: connect<window<32>>(
            # Option 2: connect<stream>(  -- but this changes template args count
            # We'll use window<32> as the primary replacement since it's a common mistake
            replacement_window = re.sub(
                r'connect\s*<\s*parameter\s*>\s*\(',
                'connect<window<32>>(',
                original
            )
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "rtp_connect_missing_parameter_template",
                "category": "rtp_parameters",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement_window,
                "description": (
                    f"Replace 'connect<parameter>(' with 'connect<window<32>>(' "
                    f"at offset {start} in {file_path}. This uses an incorrect "
                    f"connection type template incompatible with RTP ports."
                )
            })
            
            # Also offer stream variant
            replacement_stream = re.sub(
                r'connect\s*<\s*parameter\s*>\s*\(',
                'connect<stream>(',
                original
            )
            
            candidates.append({
                "file_path": file_path,
                "bug_type": "rtp_connect_missing_parameter_template",
                "category": "rtp_parameters",
                "start": start,
                "end": end,
                "original": original,
                "replacement": replacement_stream,
                "description": (
                    f"Replace 'connect<parameter>(' with 'connect<stream>(' "
                    f"at offset {start} in {file_path}. This uses an incorrect "
                    f"stream connection type for an RTP port."
                )
            })
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is still at the expected location
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
