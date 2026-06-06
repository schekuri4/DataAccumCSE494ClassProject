import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF018",
    "bug_type": "conditional_compilation_excludes_gmio_declaration",
    "category": "header_guards_and_preprocessor",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::GMIO",
        "adf::input_gmio",
        "adf::output_gmio",
        "#if defined(USE_PLIO)",
        "#else"
    ],
    "mutation_strategy": "Use #if defined(USE_PLIO) / #else to switch between PLIO and GMIO declarations, but define USE_PLIO in the build while leaving only GMIO connect<> statements in the unconditional graph body, causing references to undeclared GMIO port objects.",
    "repair_expectation": "Either undefine USE_PLIO to enable GMIO declarations, or move the connect<> statements into the appropriate conditional branch matching the declared port type.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared identifier errors for GMIO port objects.",
    "tags": [
        "conditional_compilation",
        "gmio",
        "header_guards_and_preprocessor",
        "ifdef",
        "plio",
        "port_declaration"
    ]
}


def _is_graph_file(filepath: str) -> bool:
    """Check if file is likely a graph header or source."""
    lower = filepath.lower()
    # Graph headers/sources typically contain 'graph' in name or are .h/.cpp files
    if 'graph' in lower:
        return True
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def _find_gmio_declarations(content: str) -> list[tuple[int, int, str]]:
    """Find GMIO port declarations (adf::GMIO, adf::input_gmio, adf::output_gmio, input_gmio, output_gmio, GMIO)."""
    results = []
    # Match lines declaring GMIO port objects
    # Patterns like: adf::GMIO varname; or input_gmio varname = ...; or adf::output_gmio varname;
    pattern = re.compile(
        r'^([ \t]*(?:adf::)?(?:GMIO|input_gmio|output_gmio)\s+\w+[^;]*;[ \t]*(?://[^\n]*)?)',
        re.MULTILINE
    )
    for m in pattern.finditer(content):
        results.append((m.start(), m.end(), m.group(0)))
    return results


def _find_gmio_block(content: str) -> list[tuple[int, int, str]]:
    """Find blocks of consecutive GMIO declarations."""
    lines = content.split('\n')
    blocks = []
    gmio_pattern = re.compile(
        r'^\s*(?:adf::)?(?:GMIO|input_gmio|output_gmio)\s+\w+'
    )
    
    i = 0
    while i < len(lines):
        if gmio_pattern.match(lines[i]):
            block_start = i
            while i < len(lines) and (gmio_pattern.match(lines[i]) or lines[i].strip() == ''):
                if lines[i].strip() == '' and (i + 1 >= len(lines) or not gmio_pattern.match(lines[i + 1])):
                    break
                i += 1
            block_end = i
            # Get the actual text range
            start_offset = sum(len(lines[j]) + 1 for j in range(block_start))
            end_offset = sum(len(lines[j]) + 1 for j in range(block_end)) - 1
            block_text = '\n'.join(lines[block_start:block_end])
            if block_text.strip():
                blocks.append((start_offset, end_offset, block_text))
        i += 1
    return blocks


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []
    
    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue
        
        # Check if file contains GMIO-related declarations
        has_gmio = any(kw in content for kw in ['adf::GMIO', 'adf::input_gmio', 'adf::output_gmio',
                                                  'input_gmio', 'output_gmio'])
        if not has_gmio:
            continue
        
        # Check that file also has connect<> statements referencing GMIO ports
        # (or at least looks like a graph definition)
        has_graph_context = 'adf::graph' in content or 'class' in content or 'connect<' in content
        if not has_graph_context:
            continue
        
        # Already has the conditional compilation pattern - skip
        if '#if defined(USE_PLIO)' in content or '#ifdef USE_PLIO' in content:
            continue
        
        # Find GMIO declaration lines/blocks
        gmio_decls = _find_gmio_declarations(content)
        
        if not gmio_decls:
            continue
        
        # Try to find a contiguous block of GMIO declarations
        # We'll wrap them in #if defined(USE_PLIO) ... PLIO version ... #else ... GMIO version ... #endif
        # but since USE_PLIO will be defined, the GMIO declarations will be excluded
        
        # Group consecutive GMIO declarations
        if len(gmio_decls) == 0:
            continue
        
        # Use the first GMIO declaration or a block of them
        # Find all consecutive GMIO decl lines
        lines = content.split('\n')
        gmio_line_indices = []
        gmio_line_pattern = re.compile(
            r'^\s*(?:adf::)?(?:GMIO|input_gmio|output_gmio)\s+\w+'
        )
        
        for idx, line in enumerate(lines):
            if gmio_line_pattern.match(line):
                gmio_line_indices.append(idx)
        
        if not gmio_line_indices:
            continue
        
        # Find contiguous groups
        groups = []
        current_group = [gmio_line_indices[0]]
        for i in range(1, len(gmio_line_indices)):
            # Allow gaps of empty/comment lines
            gap_lines = lines[gmio_line_indices[i-1]+1:gmio_line_indices[i]]
            all_blank_or_comment = all(
                l.strip() == '' or l.strip().startswith('//')
                for l in gap_lines
            )
            if all_blank_or_comment and (gmio_line_indices[i] - gmio_line_indices[i-1]) <= 3:
                current_group.append(gmio_line_indices[i])
            else:
                groups.append(current_group)
                current_group = [gmio_line_indices[i]]
        groups.append(current_group)
        
        # For each group, create a mutation candidate
        for group in groups:
            first_line = group[0]
            last_line = group[-1]
            
            # Extract the original block text
            original_lines = lines[first_line:last_line + 1]
            original_text = '\n'.join(original_lines)
            
            # Determine indentation from first line
            indent_match = re.match(r'^(\s*)', original_lines[0])
            indent = indent_match.group(1) if indent_match else '    '
            
            # Create PLIO equivalent declarations
            plio_lines = []
            for line in original_lines:
                # Replace GMIO types with PLIO types
                plio_line = line
                plio_line = re.sub(r'adf::output_gmio', 'adf::output_plio', plio_line)
                plio_line = re.sub(r'adf::input_gmio', 'adf::input_plio', plio_line)
                plio_line = re.sub(r'adf::GMIO', 'adf::PLIO', plio_line)
                plio_line = re.sub(r'\boutput_gmio\b', 'output_plio', plio_line)
                plio_line = re.sub(r'\binput_gmio\b', 'input_plio', plio_line)
                plio_line = re.sub(r'\bGMIO\b', 'PLIO', plio_line)
                # Adjust constructor args if needed (GMIO has different params than PLIO)
                plio_lines.append(plio_line)
            
            plio_text = '\n'.join(plio_lines)
            
            # Build the replacement: conditional that hides GMIO when USE_PLIO is defined
            replacement_text = (
                f"#if defined(USE_PLIO)\n"
                f"{plio_text}\n"
                f"#else\n"
                f"{original_text}\n"
                f"#endif"
            )
            
            # Calculate character offsets
            start_offset = sum(len(lines[j]) + 1 for j in range(first_line))
            end_offset = start_offset + len(original_text)
            
            # Extract GMIO variable names for description
            var_names = []
            var_pattern = re.compile(r'(?:adf::)?(?:GMIO|input_gmio|output_gmio)\s+(\w+)')
            for line in original_lines:
                vm = var_pattern.search(line)
                if vm:
                    var_names.append(vm.group(1))
            
            description = (
                f"Wrap GMIO declarations ({', '.join(var_names)}) in #if defined(USE_PLIO)/#else/#endif, "
                f"placing GMIO in the #else branch. When USE_PLIO is defined at build time, "
                f"GMIO port objects become undeclared but connect<> statements still reference them."
            )
            
            candidate = {
                "file_path": filepath,
                "bug_type": "conditional_compilation_excludes_gmio_declaration",
                "category": "header_guards_and_preprocessor",
                "start": start_offset,
                "end": end_offset,
                "original": original_text,
                "replacement": replacement_text,
                "description": description
            }
            candidates.append(candidate)
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_files = dict(project_files)  # shallow copy
    
    filepath = candidate["file_path"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    content = new_files[filepath]
    
    # Try exact replacement first
    if original in content:
        new_content = content.replace(original, replacement, 1)
    else:
        # Fall back to offset-based replacement
        start = candidate["start"]
        end = candidate["end"]
        new_content = content[:start] + replacement + content[end:]
    
    # Also add #define USE_PLIO at the top of the file (or after include guards)
    # to trigger the bug - the GMIO branch won't be compiled
    # We add it after any #pragma once or include guard #define
    define_line = "#define USE_PLIO 1\n"
    
    if define_line not in new_content:
        # Find a good insertion point
        insert_pos = 0
        
        # After #pragma once
        pragma_match = re.search(r'#pragma\s+once\s*\n', new_content)
        if pragma_match:
            insert_pos = pragma_match.end()
        else:
            # After include guard #define
            guard_match = re.search(r'#ifndef\s+\w+\s*\n#define\s+\w+\s*\n', new_content)
            if guard_match:
                insert_pos = guard_match.end()
            else:
                # After first #include or at very top
                include_match = re.search(r'(#include\s+[<"][^>"]+[>"]\s*\n)', new_content)
                if include_match:
                    insert_pos = include_match.end()
        
        new_content = new_content[:insert_pos] + define_line + new_content[insert_pos:]
    
    new_files[filepath] = new_content
    return new_files
