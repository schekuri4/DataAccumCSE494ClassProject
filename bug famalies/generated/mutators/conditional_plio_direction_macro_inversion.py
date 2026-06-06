import re
import copy

BUG_FAMILY = {
    "family_id": "BF013",
    "bug_type": "conditional_plio_direction_macro_inversion",
    "category": "header_guards_and_preprocessor",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::input_plio",
        "adf::output_plio",
        "#ifdef INPUT_MODE",
        "#if defined"
    ],
    "mutation_strategy": "Introduce a preprocessor conditional that selects between input_plio and output_plio based on a macro, but invert the branches so that when INPUT_MODE is defined, output_plio is instantiated (and vice versa), causing connect<> template type mismatches in the graph.",
    "repair_expectation": "Swap the bodies of the #ifdef/#else branches so input_plio is used when INPUT_MODE is defined.",
    "validation_signal": "WSL Vitis/AIE compile failure with template argument mismatch in adf::connect or port direction errors.",
    "tags": ["connect", "direction", "header_guards_and_preprocessor", "ifdef", "inverted_branch", "plio"]
}


def _is_graph_file(path):
    """Heuristic: graph headers/sources typically contain 'graph' in name or are .h/.cpp files."""
    lower = path.lower()
    # Accept files that look like graph headers or sources
    if 'graph' in lower:
        return True
    # Also accept .h or .cpp files that might be graph files
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue

        # Strategy 1: Find existing #ifdef INPUT_MODE or #if defined(INPUT_MODE) blocks
        # that have input_plio in one branch and output_plio in the other, then swap them
        ifdef_pattern = re.compile(
            r'(#\s*if(?:def|\s+defined\s*\(\s*)INPUT_MODE\s*\)?\s*\n)'
            r'(.*?)'
            r'(#\s*else\s*\n)'
            r'(.*?)'
            r'(#\s*endif)',
            re.DOTALL
        )

        for m in ifdef_pattern.finditer(content):
            if_branch = m.group(2)
            else_branch = m.group(4)
            # Check that branches contain plio references
            if ('input_plio' in if_branch or 'output_plio' in if_branch) and \
               ('input_plio' in else_branch or 'output_plio' in else_branch):
                original = m.group(0)
                # Swap the branches (invert)
                replacement = m.group(1) + else_branch + m.group(3) + if_branch + m.group(5)
                if original != replacement:
                    candidates.append({
                        "file_path": file_path,
                        "bug_type": "conditional_plio_direction_macro_inversion",
                        "category": "header_guards_and_preprocessor",
                        "start": m.start(),
                        "end": m.end(),
                        "original": original,
                        "replacement": replacement,
                        "description": "Inverted #ifdef INPUT_MODE branches so input_plio and output_plio are swapped, causing direction mismatch."
                    })

        # Strategy 2: Find bare adf::input_plio declarations and wrap them in an inverted conditional
        # This introduces the bug where none existed before
        if not any(c["file_path"] == file_path for c in candidates):
            input_plio_pattern = re.compile(
                r'^([ \t]*)(adf::input_plio\s+\w+[^;]*;)',
                re.MULTILINE
            )
            for m in input_plio_pattern.finditer(content):
                indent = m.group(1)
                original_decl = m.group(2)
                # Create an output_plio version by replacing input_plio with output_plio
                output_version = original_decl.replace('input_plio', 'output_plio')

                original = m.group(0)
                # Inverted: when INPUT_MODE defined, use output_plio; else use input_plio
                replacement = (
                    f"{indent}#ifdef INPUT_MODE\n"
                    f"{indent}{output_version}\n"
                    f"{indent}#else\n"
                    f"{indent}{original_decl}\n"
                    f"{indent}#endif"
                )

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "conditional_plio_direction_macro_inversion",
                    "category": "header_guards_and_preprocessor",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": "Wrapped adf::input_plio in inverted #ifdef INPUT_MODE so output_plio is used when INPUT_MODE is defined."
                })

            # Strategy 3: Same for output_plio
            output_plio_pattern = re.compile(
                r'^([ \t]*)(adf::output_plio\s+\w+[^;]*;)',
                re.MULTILINE
            )
            for m in output_plio_pattern.finditer(content):
                indent = m.group(1)
                original_decl = m.group(2)
                input_version = original_decl.replace('output_plio', 'input_plio')

                original = m.group(0)
                # Inverted: when INPUT_MODE is NOT defined, use input_plio (wrong direction)
                replacement = (
                    f"{indent}#ifdef INPUT_MODE\n"
                    f"{indent}{original_decl}\n"
                    f"{indent}#else\n"
                    f"{indent}{input_version}\n"
                    f"{indent}#endif"
                )

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "conditional_plio_direction_macro_inversion",
                    "category": "header_guards_and_preprocessor",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": "Wrapped adf::output_plio in inverted #ifdef INPUT_MODE so input_plio is used when INPUT_MODE is not defined."
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)
    file_path = candidate["file_path"]
    content = new_files[file_path]

    original = candidate["original"]
    replacement = candidate["replacement"]

    # Use position-based replacement for precision
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
