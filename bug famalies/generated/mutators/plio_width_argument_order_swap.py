import re
import copy

BUG_FAMILY = {
    "family_id": "BF079",
    "bug_type": "plio_width_argument_order_swap",
    "category": "plio_ports",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "input_plio::create(",
        "output_plio::create(",
        "plio_32_bits",
        "plio_64_bits",
        "plio_128_bits",
    ],
    "mutation_strategy": "Swap the order of arguments in the PLIO create() factory call, e.g., swap the filename string and the width enum, or swap the logical name and the filename, causing type mismatches in the function signature.",
    "repair_expectation": "Restore the correct argument order: create(logical_name, plio_width, filename) as specified by the ADF API.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about no matching function call, cannot convert argument from string to enum or vice versa.",
    "tags": ["argument_order", "factory", "plio", "plio_ports", "type_error"],
}


def _is_target_file(filepath):
    """Heuristic to identify graph header or graph source files."""
    lower = filepath.lower()
    # Common patterns for graph files in AIE projects
    if any(ext in lower for ext in ['.h', '.hpp', '.cpp', '.cc']):
        return True
    return False


def _find_plio_create_calls(content):
    """Find all PLIO create() calls and return match info."""
    # Pattern matches: input_plio::create(...) or output_plio::create(...)
    # We need to find the full call including balanced parentheses
    pattern = re.compile(
        r'((?:input_plio|output_plio)\s*::\s*create\s*)\(([^)]*)\)',
        re.DOTALL
    )
    results = []
    for m in pattern.finditer(content):
        prefix = m.group(1)
        args_str = m.group(2)
        full_match = m.group(0)
        start = m.start()
        end = m.end()
        
        # Parse arguments - split by comma but respect nested parens/strings
        args = _split_args(args_str)
        if len(args) >= 2:
            results.append({
                'start': start,
                'end': end,
                'prefix': prefix,
                'args': args,
                'args_str': args_str,
                'full_match': full_match,
            })
    return results


def _split_args(args_str):
    """Split comma-separated arguments respecting strings and nested parens."""
    args = []
    depth = 0
    current = []
    in_string = False
    string_char = None
    escaped = False
    
    for ch in args_str:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == '\\' and in_string:
            current.append(ch)
            escaped = True
            continue
        if ch in ('"', "'") and not in_string:
            in_string = True
            string_char = ch
            current.append(ch)
            continue
        if ch == string_char and in_string:
            in_string = False
            string_char = None
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
            args.append(''.join(current).strip())
            current = []
        else:
            current.append(ch)
    
    if current:
        args.append(''.join(current).strip())
    
    return [a for a in args if a]


def _has_plio_width(arg):
    """Check if an argument contains a plio width enum."""
    return bool(re.search(r'plio_(?:32|64|128)_bits', arg))


def _is_string_literal(arg):
    """Check if argument is or contains a string literal."""
    return bool(re.search(r'["\']', arg))


def find_mutation_candidates(project_files):
    candidates = []
    
    for filepath, content in project_files.items():
        if not _is_target_file(filepath):
            continue
        
        # Check if file contains relevant PLIO patterns
        has_plio = any(mt in content for mt in BUG_FAMILY["match_targets"])
        if not has_plio:
            continue
        
        calls = _find_plio_create_calls(content)
        
        for call_info in calls:
            args = call_info['args']
            prefix = call_info['prefix']
            
            if len(args) == 3:
                # Standard form: create(logical_name, plio_width, filename)
                # Strategy 1: Swap arg[1] (width) and arg[2] (filename)
                if _has_plio_width(args[1]) and _is_string_literal(args[2]):
                    swapped_args = [args[0], args[2], args[1]]
                    replacement = prefix + '(' + ', '.join(swapped_args) + ')'
                    candidates.append({
                        'file_path': filepath,
                        'bug_type': BUG_FAMILY['bug_type'],
                        'category': BUG_FAMILY['category'],
                        'start': call_info['start'],
                        'end': call_info['end'],
                        'original': call_info['full_match'],
                        'replacement': replacement,
                        'description': (
                            f"Swap plio_width enum and filename arguments in "
                            f"PLIO create() call, changing correct order "
                            f"(name, width, file) to (name, file, width)."
                        ),
                    })
                
                # Strategy 2: Swap arg[0] (logical_name) and arg[2] (filename)
                if _is_string_literal(args[0]) and _is_string_literal(args[2]) and args[0] != args[2]:
                    swapped_args = [args[2], args[1], args[0]]
                    replacement = prefix + '(' + ', '.join(swapped_args) + ')'
                    candidates.append({
                        'file_path': filepath,
                        'bug_type': BUG_FAMILY['bug_type'],
                        'category': BUG_FAMILY['category'],
                        'start': call_info['start'],
                        'end': call_info['end'],
                        'original': call_info['full_match'],
                        'replacement': replacement,
                        'description': (
                            f"Swap logical_name and filename arguments in "
                            f"PLIO create() call, changing correct order "
                            f"(name, width, file) to (file, width, name)."
                        ),
                    })
                
                # Strategy 3: Swap arg[0] (logical_name) and arg[1] (width)
                if _is_string_literal(args[0]) and _has_plio_width(args[1]):
                    swapped_args = [args[1], args[0], args[2]]
                    replacement = prefix + '(' + ', '.join(swapped_args) + ')'
                    candidates.append({
                        'file_path': filepath,
                        'bug_type': BUG_FAMILY['bug_type'],
                        'category': BUG_FAMILY['category'],
                        'start': call_info['start'],
                        'end': call_info['end'],
                        'original': call_info['full_match'],
                        'replacement': replacement,
                        'description': (
                            f"Swap logical_name and plio_width arguments in "
                            f"PLIO create() call, changing correct order "
                            f"(name, width, file) to (width, name, file)."
                        ),
                    })
            
            elif len(args) == 2:
                # Two-argument form: create(logical_name, plio_width) or similar
                # Swap the two arguments
                if (_is_string_literal(args[0]) and _has_plio_width(args[1])) or \
                   (_has_plio_width(args[0]) and _is_string_literal(args[1])) or \
                   (_is_string_literal(args[0]) and _is_string_literal(args[1]) and args[0] != args[1]):
                    swapped_args = [args[1], args[0]]
                    replacement = prefix + '(' + ', '.join(swapped_args) + ')'
                    candidates.append({
                        'file_path': filepath,
                        'bug_type': BUG_FAMILY['bug_type'],
                        'category': BUG_FAMILY['category'],
                        'start': call_info['start'],
                        'end': call_info['end'],
                        'original': call_info['full_match'],
                        'replacement': replacement,
                        'description': (
                            f"Swap the two arguments in PLIO create() call, "
                            f"causing type mismatch in function signature."
                        ),
                    })
    
    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    filepath = candidate['file_path']
    content = new_files[filepath]
    
    original = candidate['original']
    replacement = candidate['replacement']
    start = candidate['start']
    end = candidate['end']
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[filepath] = new_content
    return new_files
