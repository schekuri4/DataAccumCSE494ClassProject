import re
import copy

BUG_FAMILY = {
    "family_id": "BF104",
    "bug_type": "missing_adf_namespace_on_stream_connect",
    "category": "stream_scalar_interfaces",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::connect<",
        "connect<stream",
        "connect<adf::stream"
    ],
    "mutation_strategy": "Remove the adf:: namespace qualifier from connect<stream> or connect<adf::stream> in the graph definition, or remove 'using namespace adf;' while keeping unqualified connect calls, causing an unresolved symbol error.",
    "repair_expectation": "Add the adf:: namespace qualifier to the connect call or restore the 'using namespace adf;' directive.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about undeclared identifier 'connect' or 'stream' not found in current scope.",
    "tags": ["adf", "connect", "graph", "namespace", "stream", "stream_scalar_interfaces"]
}


def _is_graph_file(path):
    """Heuristic: graph headers/sources typically contain 'graph' in name or are .h/.cpp files."""
    lower = path.lower()
    # Accept any header or source file that could be a graph file
    return lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx'))


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Strategy 1: Remove adf:: from "adf::connect<stream" or "adf::connect<adf::stream"
        # Pattern: adf::connect< with optional adf:: before stream
        pattern1 = re.compile(r'adf::connect\s*<\s*(adf::)?stream')
        for m in pattern1.finditer(content):
            original = m.group(0)
            # Remove the leading "adf::" from connect
            replacement = original.replace('adf::connect', 'connect', 1)
            candidates.append({
                "file_path": file_path,
                "bug_type": "missing_adf_namespace_on_stream_connect",
                "category": "stream_scalar_interfaces",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": "Removed 'adf::' namespace qualifier from connect<stream> call, causing unresolved symbol error."
            })

        # Strategy 2: For "adf::connect<adf::stream", also try removing adf:: from stream
        pattern2 = re.compile(r'(adf::)?connect\s*<\s*adf::stream')
        for m in pattern2.finditer(content):
            original = m.group(0)
            # Only if it has adf:: before stream
            if 'adf::stream' in original:
                replacement = original.replace('adf::stream', 'stream', 1)
                # Avoid duplicates: check if this overlaps with strategy 1
                # Only add if the replacement is different from what strategy 1 would produce
                cand = {
                    "file_path": file_path,
                    "bug_type": "missing_adf_namespace_on_stream_connect",
                    "category": "stream_scalar_interfaces",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": "Removed 'adf::' namespace qualifier from 'stream' type in connect<adf::stream>, causing type not found error."
                }
                # Deduplicate by checking start/end/replacement
                if not any(c["start"] == cand["start"] and c["end"] == cand["end"] and c["replacement"] == cand["replacement"] for c in candidates):
                    candidates.append(cand)

        # Strategy 3: Remove 'using namespace adf;' if file has unqualified connect<stream calls
        using_pattern = re.compile(r'using\s+namespace\s+adf\s*;')
        unqualified_connect = re.compile(r'(?<!adf::)connect\s*<\s*(?:adf::)?stream')
        
        for m in using_pattern.finditer(content):
            # Check if there are unqualified connect<stream calls in the file
            # After removing using namespace, these would break
            if unqualified_connect.search(content):
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "missing_adf_namespace_on_stream_connect",
                    "category": "stream_scalar_interfaces",
                    "start": m.start(),
                    "end": m.end(),
                    "original": m.group(0),
                    "replacement": "/* using namespace adf; */",
                    "description": "Removed 'using namespace adf;' directive while unqualified connect<stream> calls remain, causing unresolved symbol error."
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
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
