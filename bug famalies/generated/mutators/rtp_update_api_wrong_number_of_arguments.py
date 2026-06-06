import re
import copy

BUG_FAMILY = {
    "family_id": "BF100",
    "bug_type": "rtp_update_api_wrong_number_of_arguments",
    "category": "rtp_parameters",
    "target_files": ["graph source", "graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["update(", "graph::update", "myGraph.update("],
    "mutation_strategy": "Call the graph update() API with incorrect arguments—for example, passing an array pointer and size for a scalar RTP, or passing only a scalar value for an array RTP that requires a pointer and size—causing a compile-time argument count or type error.",
    "repair_expectation": "Pass the correct arguments to update(): a single scalar value for scalar RTPs, or a pointer and element count for array RTPs.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about wrong number of arguments or incompatible argument types in update() call.",
    "tags": ["arguments", "compile_time", "rtp", "rtp_parameters", "scalar_vs_array", "update_api"]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find all graph update() calls and propose argument mutations."""
    candidates = []
    
    # Match patterns like: <identifier>.update(<args>)
    # This covers myGraph.update(...), gr.update(...), etc.
    # Also match graph::update patterns
    update_pattern = re.compile(
        r'(\w+\s*\.\s*update\s*\()'  # group1: prefix up to opening paren
        r'([^;]*?)'                    # group2: arguments
        r'(\)\s*;)',                   # group3: closing paren and semicolon
        re.MULTILINE
    )
    
    # Target file extensions for graph source/header
    target_extensions = ('.cpp', '.cc', '.cxx', '.h', '.hpp', '.hxx', '.graph.h', '.graph.cpp')
    
    for file_path, content in project_files.items():
        # Check if file could be a graph source or header
        if not any(file_path.endswith(ext) for ext in target_extensions):
            continue
        
        for match in update_pattern.finditer(content):
            prefix = match.group(1)
            args_str = match.group(2).strip()
            suffix = match.group(3)
            
            full_match = match.group(0)
            start = match.start()
            end = match.end()
            
            # Parse arguments by splitting on commas (simple heuristic, ignores nested parens)
            # Count top-level commas
            args = _split_args(args_str)
            num_args = len(args)
            
            if num_args == 0:
                continue
            
            # Determine mutation based on argument count
            if num_args == 1:
                # Likely a scalar RTP update with one argument
                # Mutate to add a spurious size argument (as if it were an array RTP)
                scalar_arg = args[0].strip()
                replacement_args = f"{scalar_arg}, 1"
                description = (
                    f"Changed scalar RTP update() call from 1 argument to 2 arguments "
                    f"(added spurious size parameter), simulating array RTP calling convention."
                )
            elif num_args == 2:
                # Likely an array RTP update with pointer and size
                # Mutate to remove the size argument (as if it were a scalar RTP)
                replacement_args = args[0].strip()
                description = (
                    f"Changed array RTP update() call from 2 arguments to 1 argument "
                    f"(removed size parameter), simulating scalar RTP calling convention."
                )
            elif num_args >= 3:
                # Remove last argument
                replacement_args = ", ".join(a.strip() for a in args[:-1])
                description = (
                    f"Removed last argument from update() call with {num_args} arguments."
                )
            else:
                continue
            
            replacement = f"{prefix}{replacement_args}{suffix}"
            
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": start,
                "end": end,
                "original": full_match,
                "replacement": replacement,
                "description": description
            })
    
    return candidates


def _split_args(args_str: str) -> list[str]:
    """Split arguments at top-level commas, respecting parentheses and angle brackets."""
    args = []
    depth = 0
    current = []
    for ch in args_str:
        if ch in ('(', '<', '[', '{'):
            depth += 1
            current.append(ch)
        elif ch in (')', '>', ']', '}'):
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            args.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        token = ''.join(current).strip()
        if token:
            args.append(''.join(current))
    return args


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate to the project files."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_files[file_path]
    
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
    
    new_files[file_path] = new_content
    return new_files
