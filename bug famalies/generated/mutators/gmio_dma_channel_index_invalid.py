import re


BUG_FAMILY = {
    "family_id": "BF_MANUAL_GMIO_001",
    "bug_type": "gmio_dma_channel_index_invalid",
    "category": "gmio_ports",
    "target_files": ["graph source", "graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": ["adf::location<adf::dma>", "adf::dma_channel"],
    "mutation_strategy": (
        "Change a GMIO DMA channel lane/index argument to an invalid high value "
        "while leaving the GMIO port and shim placement otherwise unchanged."
    ),
    "repair_expectation": "Restore the legal DMA channel argument used by the graph placement constraint.",
    "validation_signal": "WSL Vitis/AIE compile/elaboration failure for an invalid GMIO DMA channel placement.",
    "tags": ["dma_channel", "gmio", "graph_placement", "single_span"],
}


_DMA_PATTERN = re.compile(
    r'(adf::dma_channel\s*\(\s*adf::shim_tile\s*,\s*[^,]+,\s*[^,]+,\s*)(\d+)(\s*\))'
)


def _is_graph_file(path):
    return path.lower().endswith((".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh"))


def find_mutation_candidates(project_files):
    candidates = []
    for file_path, content in project_files.items():
        if not _is_graph_file(file_path):
            continue
        if "dma_channel" not in content or "GMIO" not in content:
            continue
        for match in _DMA_PATTERN.finditer(content):
            original = match.group(2)
            replacement = "99"
            if original == replacement:
                continue
            candidates.append({
                "file_path": file_path,
                "bug_type": BUG_FAMILY["bug_type"],
                "category": BUG_FAMILY["category"],
                "start": match.start(2),
                "end": match.end(2),
                "original": original,
                "replacement": replacement,
                "description": (
                    f"Changed GMIO DMA channel index from {original} to 99, "
                    f"which is outside legal shim DMA channel range."
                ),
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
    if content[start:end] == original:
        new_files[file_path] = content[:start] + replacement + content[end:]
    else:
        new_files[file_path] = content.replace(original, replacement, 1)
    return new_files
