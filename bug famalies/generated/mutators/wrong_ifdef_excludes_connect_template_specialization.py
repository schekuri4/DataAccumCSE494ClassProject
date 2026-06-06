import re
import copy

BUG_FAMILY = {
    "family_id": "BF020",
    "bug_type": "wrong_ifdef_excludes_connect_template_specialization",
    "category": "header_guards_and_preprocessor",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::connect<adf::window<",
        "adf::connect<adf::stream>",
        "#ifdef USE_STREAM",
        "#ifdef USE_WINDOW",
        "connect<>"
    ],
    "mutation_strategy": "Use #ifdef USE_STREAM to conditionally compile stream-based connect<adf::stream> statements and #ifdef USE_WINDOW for window-based connect<adf::window<N>>, but define both macros simultaneously causing duplicate or conflicting connections on the same port, or define neither causing no connections to be compiled, resulting in unconnected port errors.",
    "repair_expectation": "Define exactly one of USE_STREAM or USE_WINDOW, or restructure with #elif to ensure mutual exclusivity of connection types.",
    "validation_signal": "WSL Vitis/AIE compile failure with port connectivity errors, duplicate connection errors, or unconnected kernel port errors during ADF graph compilation.",
    "tags": [
        "connect",
        "header_guards_and_preprocessor",
        "ifdef",
        "mutual_exclusion",
        "stream",
        "template",
        "window"
    ]
}


def _is_graph_header(file_path):
    """Heuristic to identify graph header files."""
    lower = file_path.lower()
    if lower.endswith(('.h', '.hpp')):
        if 'graph' in lower:
            return True
    return False


def _is_graph_header_by_content(file_path, content):
    """Check if file looks like an AIE graph header by content."""
    lower = file_path.lower()
    if not lower.endswith(('.h', '.hpp', '.hh')):
        return False
    # Check for graph-related content
    if 'adf::graph' in content or 'class' in content:
        if 'connect' in content or 'adf::connect' in content:
            return True
    if 'graph' in lower:
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        # Identify graph header files
        if not (_is_graph_header(file_path) or _is_graph_header_by_content(file_path, content)):
            continue

        # Strategy 1: Find existing #ifdef USE_STREAM ... #else ... #endif pattern
        # and mutate to remove the mutual exclusivity (e.g., change #else to #endif\n#ifdef USE_WINDOW)
        # causing both blocks to be compiled when both macros are defined.

        # Pattern: #ifdef USE_STREAM with connect<adf::stream> followed by #else with connect<adf::window<
        pattern_ifdef_stream_else = re.compile(
            r'(#ifdef\s+USE_STREAM\s*\n)'
            r'(.*?)'
            r'(#else\s*\n)'
            r'(.*?)'
            r'(#endif)',
            re.DOTALL
        )

        for m in pattern_ifdef_stream_else.finditer(content):
            # Check that the blocks contain connect statements
            stream_block = m.group(2)
            window_block = m.group(4)
            if ('connect' in stream_block or 'adf::connect' in stream_block) and \
               ('connect' in window_block or 'adf::connect' in window_block):
                original = m.group(0)
                # Mutation: replace #else with #endif\n#ifdef USE_WINDOW\n
                # This means both blocks compile when both macros are defined
                replacement = (
                    m.group(1) +
                    m.group(2) +
                    '#endif\n#ifdef USE_WINDOW\n' +
                    m.group(4) +
                    m.group(5)
                )
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "wrong_ifdef_excludes_connect_template_specialization",
                    "category": "header_guards_and_preprocessor",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        "Changed #ifdef USE_STREAM / #else / #endif to "
                        "#ifdef USE_STREAM / #endif / #ifdef USE_WINDOW / #endif, "
                        "breaking mutual exclusivity. When both macros are defined, "
                        "both stream and window connections compile causing duplicate "
                        "connection errors."
                    )
                })

        # Pattern: #ifdef USE_WINDOW with connect<adf::window< followed by #else with connect<adf::stream>
        pattern_ifdef_window_else = re.compile(
            r'(#ifdef\s+USE_WINDOW\s*\n)'
            r'(.*?)'
            r'(#else\s*\n)'
            r'(.*?)'
            r'(#endif)',
            re.DOTALL
        )

        for m in pattern_ifdef_window_else.finditer(content):
            stream_block = m.group(4)
            window_block = m.group(2)
            if ('connect' in stream_block or 'adf::connect' in stream_block) and \
               ('connect' in window_block or 'adf::connect' in window_block):
                original = m.group(0)
                replacement = (
                    m.group(1) +
                    m.group(2) +
                    '#endif\n#ifdef USE_STREAM\n' +
                    m.group(4) +
                    m.group(5)
                )
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "wrong_ifdef_excludes_connect_template_specialization",
                    "category": "header_guards_and_preprocessor",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        "Changed #ifdef USE_WINDOW / #else / #endif to "
                        "#ifdef USE_WINDOW / #endif / #ifdef USE_STREAM / #endif, "
                        "breaking mutual exclusivity. When both macros are defined, "
                        "both window and stream connections compile causing duplicate "
                        "connection errors."
                    )
                })

        # Strategy 2: Find #elif pattern and break it
        pattern_elif = re.compile(
            r'(#ifdef\s+(USE_STREAM|USE_WINDOW)\s*\n)'
            r'(.*?)'
            r'(#elif\s+defined\s*\(\s*(USE_STREAM|USE_WINDOW)\s*\)\s*\n)'
            r'(.*?)'
            r'(#endif)',
            re.DOTALL
        )

        for m in pattern_elif.finditer(content):
            first_macro = m.group(2)
            second_macro = m.group(5)
            block1 = m.group(3)
            block2 = m.group(6)
            if ('connect' in block1 or 'connect' in block2):
                original = m.group(0)
                # Break #elif into separate #ifdef blocks
                replacement = (
                    m.group(1) +
                    m.group(3) +
                    '#endif\n#ifdef ' + second_macro + '\n' +
                    m.group(6) +
                    m.group(7)
                )
                candidates.append({
                    "file_path": file_path,
                    "bug_type": "wrong_ifdef_excludes_connect_template_specialization",
                    "category": "header_guards_and_preprocessor",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        f"Changed #ifdef {first_macro} / #elif defined({second_macro}) to "
                        f"separate #ifdef blocks, breaking mutual exclusivity. Both blocks "
                        f"compile when both macros are defined."
                    )
                })

        # Strategy 3: If there are unconditional connect statements (no #ifdef guards),
        # wrap them in conflicting #ifdef blocks that require both macros
        # Look for connect<adf::stream> and connect<adf::window< on separate lines without guards
        lines = content.split('\n')
        stream_lines = []
        window_lines = []
        in_ifdef = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#ifdef') or stripped.startswith('#if '):
                in_ifdef = True
            elif stripped.startswith('#endif'):
                in_ifdef = False
            elif not in_ifdef:
                if 'connect<adf::stream>' in line or 'connect< adf::stream >' in line:
                    stream_lines.append(i)
                elif 'connect<adf::window<' in line or 'connect< adf::window<' in line:
                    window_lines.append(i)

        # If we have both stream and window connects without guards, wrap them
        if stream_lines and window_lines and not candidates:
            # Find a contiguous region containing both types
            all_connect_lines = sorted(stream_lines + window_lines)
            first_line = all_connect_lines[0]
            last_line = all_connect_lines[-1]

            # Build the original block
            original_lines = lines[first_line:last_line + 1]
            original = '\n'.join(original_lines)

            # Build replacement: wrap stream connects in #ifdef USE_STREAM,
            # window connects in #ifdef USE_WINDOW (non-exclusive)
            new_lines = []
            in_stream_block = False
            in_window_block = False

            for i in range(first_line, last_line + 1):
                line = lines[i]
                is_stream = i in stream_lines
                is_window = i in window_lines

                if is_stream:
                    if not in_stream_block:
                        if in_window_block:
                            new_lines.append('#endif')
                            in_window_block = False
                        new_lines.append('#ifdef USE_STREAM')
                        in_stream_block = True
                    new_lines.append(line)
                elif is_window:
                    if not in_window_block:
                        if in_stream_block:
                            new_lines.append('#endif')
                            in_stream_block = False
                        new_lines.append('#ifdef USE_WINDOW')
                        in_window_block = True
                    new_lines.append(line)
                else:
                    if in_stream_block:
                        new_lines.append('#endif')
                        in_stream_block = False
                    if in_window_block:
                        new_lines.append('#endif')
                        in_window_block = False
                    new_lines.append(line)

            if in_stream_block:
                new_lines.append('#endif')
            if in_window_block:
                new_lines.append('#endif')

            replacement = '\n'.join(new_lines)

            if original != replacement:
                # Calculate character offsets
                start_offset = len('\n'.join(lines[:first_line])) + (1 if first_line > 0 else 0)
                end_offset = start_offset + len(original)

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "wrong_ifdef_excludes_connect_template_specialization",
                    "category": "header_guards_and_preprocessor",
                    "start": start_offset,
                    "end": end_offset,
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        "Wrapped stream and window connect statements in separate "
                        "#ifdef USE_STREAM and #ifdef USE_WINDOW blocks without mutual "
                        "exclusivity. Defining both macros causes duplicate connections; "
                        "defining neither causes unconnected ports."
                    )
                })

        # Strategy 4: If there's a single connect<> (generic) and no ifdef guards,
        # replace it with guarded stream/window versions that both compile
        if not candidates:
            generic_connect_pattern = re.compile(
                r'([ \t]*)(adf::)?connect<>\s*\(\s*([^)]+)\s*\)\s*;'
            )
            for m in generic_connect_pattern.finditer(content):
                indent = m.group(1)
                args = m.group(3)
                original = m.group(0)

                # Create both stream and window versions under separate #ifdefs (non-exclusive)
                replacement = (
                    f'{indent}#ifdef USE_STREAM\n'
                    f'{indent}adf::connect<adf::stream>({args});\n'
                    f'{indent}#endif\n'
                    f'{indent}#ifdef USE_WINDOW\n'
                    f'{indent}adf::connect<adf::window<256>>({args});\n'
                    f'{indent}#endif'
                )

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "wrong_ifdef_excludes_connect_template_specialization",
                    "category": "header_guards_and_preprocessor",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": (
                        "Replaced generic connect<>() with separate #ifdef USE_STREAM "
                        "and #ifdef USE_WINDOW blocks without mutual exclusivity. "
                        "Defining both macros causes duplicate connections on the same port; "
                        "defining neither causes unconnected port errors."
                    )
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    replacement = candidate["replacement"]

    # Use exact string replacement anchored at the expected position
    start = candidate["start"]
    end = candidate["end"]

    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)

    new_files[file_path] = new_content
    return new_files
