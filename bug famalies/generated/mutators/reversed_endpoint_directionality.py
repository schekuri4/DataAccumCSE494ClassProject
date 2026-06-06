import re
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF052",
    "bug_type": "reversed_endpoint_directionality",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["connect<", ".out[", ".in[", "adf::connect"],
    "mutation_strategy": "Swap the source and destination arguments in a connect statement, e.g., change connect<window<256>>(k1.out[0], k2.in[0]) to connect<window<256>>(k2.in[0], k1.out[0]), reversing the data flow direction so an input port is used as source and output port as destination.",
    "repair_expectation": "Swap the arguments back so the output port is the first argument (source) and the input port is the second argument (destination).",
    "validation_signal": "WSL Vitis/AIE compile failure with error about invalid port direction or cannot connect input to input / output to output.",
    "tags": ["connect", "directionality", "endpoint", "graph_connections", "port_direction"],
}


def _is_graph_header(file_path: str) -> bool:
    """Heuristic: graph headers are .h or .hpp files with 'graph' in name or path."""
    lower = file_path.lower()
    # Accept any header file that likely contains graph definitions
    if lower.endswith(('.h', '.hpp')):
        return True
    return False


def _find_connect_statements(content: str):
    """Find connect<...>(..., ...) statements and return match info."""
    # Pattern matches: (optional adf::)connect<...>(arg1, arg2)
    # We need to handle nested angle brackets and parentheses carefully
    results = []
    
    # Match connect statements with template args and two function args
    # Pattern: (adf::)?connect<...>(arg1, arg2)
    # We use a approach that finds 'connect<' then manually parses brackets
    
    # Find all positions where connect< appears
    connect_pattern = re.compile(r'(?:adf\s*::\s*)?connect\s*<')
    
    for m in connect_pattern.finditer(content):
        start_pos = m.start()
        # Parse the template arguments (angle brackets)
        pos = m.end()
        angle_depth = 1
        while pos < len(content) and angle_depth > 0:
            if content[pos] == '<':
                angle_depth += 1
            elif content[pos] == '>':
                angle_depth -= 1
            pos += 1
        
        if angle_depth != 0:
            continue
        
        # Now skip whitespace and expect '('
        while pos < len(content) and content[pos] in ' \t\n\r':
            pos += 1
        
        if pos >= len(content) or content[pos] != '(':
            continue
        
        paren_start = pos
        pos += 1  # skip '('
        
        # Parse the two arguments separated by comma, handling nested parens/brackets
        paren_depth = 1
        args = []
        arg_start = pos
        
        while pos < len(content) and paren_depth > 0:
            ch = content[pos]
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    args.append(content[arg_start:pos].strip())
                    break
            elif ch == ',' and paren_depth == 1:
                args.append(content[arg_start:pos].strip())
                arg_start = pos + 1
            pos += 1
        
        if len(args) != 2:
            continue
        
        end_pos = pos + 1  # include closing ')'
        
        original = content[start_pos:end_pos]
        
        # Build the replacement by swapping args
        # Reconstruct: everything before '(' + '(' + arg2 + ', ' + arg1 + ')'
        prefix = content[start_pos:paren_start + 1]
        replacement = prefix + args[1] + ", " + args[0] + ")"
        
        results.append({
            "start": start_pos,
            "end": end_pos,
            "original": original,
            "replacement": replacement,
            "arg1": args[0],
            "arg2": args[1],
        })
    
    return results


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []
    
    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue
        
        # Check if file has any connect-related content
        has_connect = any(target in content for target in ["connect<", "adf::connect"])
        if not has_connect:
            continue
        
        connects = _find_connect_statements(content)
        
        for conn in connects:
            description = (
                f"Swap source and destination in connect statement: "
                f"'{conn['arg1']}' <-> '{conn['arg2']}', "
                f"reversing data flow directionality."
            )
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": conn["start"],
                "end": conn["end"],
                "original": conn["original"],
                "replacement": conn["replacement"],
                "description": description,
            })
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
    original = candidate["original"]
    start = candidate["start"]
    end = candidate["end"]
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[file_path] = new_content
    return new_files
