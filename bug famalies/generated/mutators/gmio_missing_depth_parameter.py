import re
from copy import deepcopy

BUG_FAMILY = {
    "family_id": "BF084",
    "bug_type": "gmio_missing_depth_parameter",
    "category": "gmio_ports",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["gmio::create", "adf::GMIO::create"],
    "mutation_strategy": "Remove or omit the required depth (bandwidth) parameter from the gmio::create factory call, leaving insufficient arguments so the template instantiation fails.",
    "repair_expectation": "Add the missing depth/bandwidth parameter to the gmio::create call with an appropriate value.",
    "validation_signal": "WSL Vitis/AIE compile failure indicating too few arguments or no matching overload for gmio::create.",
    "tags": [
        "compile_error",
        "depth",
        "factory",
        "gmio",
        "gmio_ports",
        "missing_parameter"
    ]
}


def _is_graph_header(filepath):
    """Heuristic: graph headers are .h or .hpp files likely containing graph definitions."""
    lower = filepath.lower()
    return lower.endswith('.h') or lower.endswith('.hpp')


def find_mutation_candidates(project_files):
    candidates = []
    
    # Pattern to match gmio::create or adf::GMIO::create calls with their arguments
    # We look for calls that have at least 3 arguments (name, burst_length, depth)
    # The depth parameter is typically the last numeric argument
    pattern = re.compile(
        r'((?:adf::)?(?:GMIO|gmio)::create)\s*\('
    )
    
    for filepath, content in project_files.items():
        if not _is_graph_header(filepath):
            continue
        
        # Find all gmio::create calls
        for match in pattern.finditer(content):
            call_start = match.start()
            paren_start = content.index('(', match.end() - 1)
            
            # Find matching closing paren
            depth = 0
            pos = paren_start
            while pos < len(content):
                if content[pos] == '(':
                    depth += 1
                elif content[pos] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                pos += 1
            
            if depth != 0:
                continue  # unbalanced parens, skip
            
            paren_end = pos  # position of closing ')'
            
            # Extract the full call including parens
            full_call = content[call_start:paren_end + 1]
            args_str = content[paren_start + 1:paren_end]
            
            # Split arguments respecting nested parens and strings
            args = _split_args(args_str)
            
            if len(args) < 3:
                # Already has too few args, not a valid mutation site
                continue
            
            # The depth/bandwidth parameter is typically the last argument
            # Remove the last argument (depth parameter)
            last_arg = args[-1]
            
            # Build the replacement: remove the last comma and last argument
            # Find the last comma position in args_str
            new_args = args[:-1]
            new_args_str = ", ".join(a.strip() for a in new_args)
            
            original_text = full_call
            prefix = match.group(1)
            replacement_text = f"{prefix}({new_args_str})"
            
            candidate = {
                "file_path": filepath,
                "bug_type": "gmio_missing_depth_parameter",
                "category": "gmio_ports",
                "start": call_start,
                "end": paren_end + 1,
                "original": original_text,
                "replacement": replacement_text,
                "description": (
                    f"Remove the depth/bandwidth parameter ('{last_arg.strip()}') "
                    f"from {prefix}() call, causing a compile error due to "
                    f"insufficient arguments."
                )
            }
            candidates.append(candidate)
    
    return candidates


def _split_args(args_str):
    """Split comma-separated arguments respecting nested parens and quotes."""
    args = []
    depth = 0
    current = []
    in_string = False
    escape_next = False
    quote_char = None
    
    for ch in args_str:
        if escape_next:
            current.append(ch)
            escape_next = False
            continue
        if ch == '\\' and in_string:
            current.append(ch)
            escape_next = True
            continue
        if ch in ('"', "'") and not in_string:
            in_string = True
            quote_char = ch
            current.append(ch)
            continue
        if in_string and ch == quote_char:
            in_string = False
            quote_char = None
            current.append(ch)
            continue
        if in_string:
            current.append(ch)
            continue
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(current))
            current = []
        else:
            current.append(ch)
    
    if current or args:
        args.append(''.join(current))
    
    return args


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict
    filepath = candidate["file_path"]
    content = new_files[filepath]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[filepath] = new_content
    return new_files
