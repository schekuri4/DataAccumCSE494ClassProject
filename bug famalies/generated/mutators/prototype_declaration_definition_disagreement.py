import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF040",
    "bug_type": "prototype_declaration_definition_disagreement",
    "category": "kernel_prototypes_and_signatures",
    "target_files": ["kernel header", "kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "void",
        "input_window<",
        "output_window<",
        "input_stream<",
        "output_stream<",
        '__attribute__((noinline))'
    ],
    "mutation_strategy": "Modify the kernel function prototype in the header to have a different parameter list than the definition in the .cc source file. For example, the header declares void filter(input_window<cint16>*, output_window<cint16>*) but the source defines void filter(input_window<cint16>*, input_window<cint16>*, output_window<cint16>*), creating a linker/compile mismatch.",
    "repair_expectation": "Make the kernel header declaration exactly match the kernel source definition in parameter types, count, and order.",
    "validation_signal": "WSL Vitis/AIE compile failure with conflicting declaration or undefined reference due to signature mismatch between header and source.",
    "tags": [
        "declaration_vs_definition",
        "header_source_disagreement",
        "kernel_prototypes_and_signatures",
        "prototype_mismatch"
    ]
}

# Pattern to match AIE kernel function declarations/definitions
# Matches: void func_name(params) with optional attributes and semicolons or braces
_KERNEL_FUNC_PATTERN = re.compile(
    r'(void\s+(\w+)\s*\()'   # return type and function name with opening paren
    r'([^)]*)'                # parameters
    r'(\)\s*(?:;|__attribute__|{|\n\s*{))',  # closing paren followed by ; or { or attribute
    re.MULTILINE
)

# Pattern for AIE-specific parameter types
_AIE_PARAM_PATTERN = re.compile(
    r'(input_window|output_window|input_stream|output_stream)\s*<\s*(\w+)\s*>\s*\*'
)


def _is_header_file(path: str) -> bool:
    return path.endswith('.h') or path.endswith('.hpp')


def _is_source_file(path: str) -> bool:
    return path.endswith('.cc') or path.endswith('.cpp') or path.endswith('.c')


def _extract_kernel_signatures(content: str) -> list[dict]:
    """Extract kernel function signatures with AIE-specific parameters."""
    results = []
    for match in _KERNEL_FUNC_PATTERN.finditer(content):
        params_str = match.group(3)
        # Check if parameters contain AIE-specific types
        if _AIE_PARAM_PATTERN.search(params_str):
            results.append({
                'func_name': match.group(2),
                'params': params_str,
                'full_match': match.group(0),
                'start': match.start(),
                'end': match.end(),
                'prefix': match.group(1),
                'suffix': match.group(4),
            })
    return results


def _generate_mutated_params(params_str: str) -> str:
    """Generate a mutated parameter list by adding an extra parameter."""
    # Parse individual parameters
    params = [p.strip() for p in params_str.split(',') if p.strip()]
    
    if not params:
        return params_str
    
    # Strategy: duplicate the first input parameter to create a mismatch
    # Find an input_window or input_stream parameter to duplicate
    for param in params:
        if 'input_window' in param or 'input_stream' in param:
            # Insert a duplicate of this parameter after the first occurrence
            idx = params.index(param)
            new_params = params[:idx+1] + [param] + params[idx+1:]
            return ', '.join(new_params)
    
    # Fallback: if no input param found, remove the last parameter
    if len(params) > 1:
        return ', '.join(params[:-1])
    
    # Last resort: change the type of the first parameter
    first = params[0]
    if 'input_window' in first:
        mutated = first.replace('input_window', 'output_window')
    elif 'output_window' in first:
        mutated = first.replace('output_window', 'input_window')
    elif 'input_stream' in first:
        mutated = first.replace('input_stream', 'output_stream')
    elif 'output_stream' in first:
        mutated = first.replace('output_stream', 'input_stream')
    else:
        return params_str  # Can't mutate
    
    return ', '.join([mutated] + params[1:])


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    """Find kernel function declarations in headers that can be mutated to disagree with source definitions."""
    candidates: list[dict[str, object]] = []
    
    # Identify header and source files
    headers = {p: c for p, c in project_files.items() if _is_header_file(p)}
    sources = {p: c for p, c in project_files.items() if _is_source_file(p)}
    
    if not headers or not sources:
        return candidates
    
    # For each header, find kernel signatures
    for header_path, header_content in headers.items():
        header_sigs = _extract_kernel_signatures(header_content)
        
        for sig in header_sigs:
            func_name = sig['func_name']
            
            # Check if there's a matching definition in any source file
            has_source_def = False
            for source_path, source_content in sources.items():
                source_sigs = _extract_kernel_signatures(source_content)
                for ssig in source_sigs:
                    if ssig['func_name'] == func_name:
                        has_source_def = True
                        break
                if has_source_def:
                    break
            
            if not has_source_def:
                continue
            
            # Generate mutated parameters for the header declaration
            original_params = sig['params']
            mutated_params = _generate_mutated_params(original_params)
            
            if mutated_params == original_params:
                continue  # No effective mutation possible
            
            original_text = sig['full_match']
            replacement_text = sig['prefix'] + mutated_params + sig['suffix']
            
            candidates.append({
                'file_path': header_path,
                'bug_type': 'prototype_declaration_definition_disagreement',
                'category': 'kernel_prototypes_and_signatures',
                'start': sig['start'],
                'end': sig['end'],
                'original': original_text,
                'replacement': replacement_text,
                'description': (
                    f"Mutated kernel function '{func_name}' declaration in header "
                    f"'{header_path}' to have different parameters than its source "
                    f"definition, creating a prototype/definition mismatch. "
                    f"Original params: ({original_params}) -> Mutated params: ({mutated_params})"
                )
            })
    
    # If no header-based candidates found, try a broader approach
    # Look for any void function with AIE params in headers using a more relaxed pattern
    if not candidates:
        broader_pattern = re.compile(
            r'(void\s+(\w+)\s*\()([^)]*(?:(?:input_window|output_window|input_stream|output_stream)\s*<[^>]+>\s*\*[^)]*))\)(\s*;)',
            re.MULTILINE
        )
        for header_path, header_content in headers.items():
            for match in broader_pattern.finditer(header_content):
                func_name = match.group(2)
                params_str = match.group(3)
                
                # Check source files for matching definition
                has_source_def = False
                for source_path, source_content in sources.items():
                    if func_name in source_content:
                        has_source_def = True
                        break
                
                if not has_source_def:
                    continue
                
                mutated_params = _generate_mutated_params(params_str)
                if mutated_params == params_str:
                    continue
                
                original_text = match.group(0)
                replacement_text = match.group(1) + mutated_params + ')' + match.group(4)
                
                candidates.append({
                    'file_path': header_path,
                    'bug_type': 'prototype_declaration_definition_disagreement',
                    'category': 'kernel_prototypes_and_signatures',
                    'start': match.start(),
                    'end': match.end(),
                    'original': original_text,
                    'replacement': replacement_text,
                    'description': (
                        f"Mutated kernel function '{func_name}' declaration in header "
                        f"'{header_path}' to have different parameters than its source "
                        f"definition, creating a prototype/definition mismatch. "
                        f"Original params: ({params_str}) -> Mutated params: ({mutated_params})"
                    )
                })
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply the mutation to create a header/source prototype disagreement."""
    new_files = dict(project_files)  # Shallow copy of the dict
    
    file_path = candidate['file_path']
    original = candidate['original']
    replacement = candidate['replacement']
    
    if file_path not in new_files:
        return new_files
    
    content = new_files[file_path]
    
    # Try position-based replacement first
    start = candidate.get('start')
    end = candidate.get('end')
    
    if start is not None and end is not None:
        # Verify the original text is at the expected position
        if content[start:end] == original:
            new_files[file_path] = content[:start] + replacement + content[end:]
            return new_files
    
    # Fallback to string replacement (first occurrence only)
    if original in content:
        new_files[file_path] = content.replace(original, replacement, 1)
    
    return new_files
