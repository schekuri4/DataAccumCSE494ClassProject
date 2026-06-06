import re
import copy

BUG_FAMILY = {
    "family_id": "BF059",
    "bug_type": "connect_to_rtp_with_wrong_syntax",
    "category": "graph_connections",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::connect<parameter>",
        "connect<parameter>",
        "async(",
        "port<direction>"
    ],
    "mutation_strategy": "Use a regular connect<window<>> or connect<stream> template for an RTP port instead of the correct connect<parameter> syntax, or attempt to wire an RTP port using the same syntax as a data port. This creates a type mismatch at the RTP endpoint.",
    "repair_expectation": "Use connect<parameter>(src, dst) for RTP connections or the appropriate adf::read/write API for async RTP.",
    "validation_signal": "WSL Vitis/AIE compile failure with error about incompatible connection type for parameter port or RTP port type mismatch.",
    "tags": ["connect", "graph_connections", "parameter", "rtp", "template_syntax"]
}


def _is_graph_header(file_path):
    """Heuristic: graph headers are .h or .hpp files with 'graph' in the name or path."""
    lower = file_path.lower()
    if not (lower.endswith('.h') or lower.endswith('.hpp')):
        return False
    return True


def find_mutation_candidates(project_files):
    candidates = []

    # Patterns to find connect<parameter> usages (with optional adf:: prefix)
    # Pattern 1: adf::connect<adf::parameter> or adf::connect<parameter>
    # Pattern 2: connect<parameter> or connect<adf::parameter>
    pattern = re.compile(
        r'((?:adf::)?connect\s*<\s*(?:adf::)?parameter\s*>)'
    )

    # Replacement options that represent wrong syntax for RTP
    replacements = [
        ("connect<window<32>>", "Replaced connect<parameter> with connect<window<32>> for RTP port"),
        ("connect<stream>", "Replaced connect<parameter> with connect<stream> for RTP port"),
    ]

    for file_path, content in project_files.items():
        if not _is_graph_header(file_path):
            continue

        for match in pattern.finditer(content):
            original = match.group(1)
            start = match.start()
            end = match.end()

            # Determine appropriate replacement preserving adf:: prefix style
            if original.startswith("adf::"):
                repl_options = [
                    ("adf::connect<adf::window<32>>", "Replaced adf::connect<parameter> with adf::connect<adf::window<32>> for RTP port - type mismatch"),
                    ("adf::connect<adf::stream>", "Replaced adf::connect<parameter> with adf::connect<adf::stream> for RTP port - type mismatch"),
                ]
            else:
                repl_options = [
                    ("connect<window<32>>", "Replaced connect<parameter> with connect<window<32>> for RTP port - type mismatch"),
                    ("connect<stream>", "Replaced connect<parameter> with connect<stream> for RTP port - type mismatch"),
                ]

            for replacement, description in repl_options:
                candidates.append({
                    "file_path": file_path,
                    "bug_type": BUG_FAMILY["bug_type"],
                    "category": BUG_FAMILY["category"],
                    "start": start,
                    "end": end,
                    "original": original,
                    "replacement": replacement,
                    "description": description
                })

    return candidates


def apply_mutation(project_files, candidate):
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        mutated_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: replace first occurrence
        mutated_content = content.replace(original, candidate["replacement"], 1)

    new_files[file_path] = mutated_content
    return new_files
