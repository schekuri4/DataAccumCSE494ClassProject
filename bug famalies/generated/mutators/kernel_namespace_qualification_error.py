import re
import copy

BUG_FAMILY = {
    "family_id": "BF024",
    "bug_type": "kernel_namespace_qualification_error",
    "category": "graph_kernel_binding",
    "target_files": ["graph header"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "kernel::create(",
        "using namespace adf",
        "adf::kernel"
    ],
    "mutation_strategy": "Remove the 'using namespace adf;' directive and also remove or corrupt the adf:: namespace prefix on kernel::create, connect<>, port<>, or PLIO::create calls. Alternatively, add a wrong namespace prefix like 'aie::kernel::create' instead of 'adf::kernel::create'.",
    "repair_expectation": "Add proper 'adf::' namespace qualification or restore the 'using namespace adf;' directive.",
    "validation_signal": "WSL Vitis/AIE compile failure with 'kernel' is not a member of the specified namespace or undeclared identifier errors.",
    "tags": ["adf", "graph_kernel_binding", "kernel_create", "namespace", "qualification"]
}


def _is_graph_header(filepath):
    """Heuristic to identify graph header files."""
    lower = filepath.lower()
    # Graph headers are typically .h or .hpp files with 'graph' in the name or path
    if not (lower.endswith('.h') or lower.endswith('.hpp')):
        return False
    return True


def _file_looks_like_graph_header(content):
    """Check if file content looks like an AIE graph header."""
    indicators = ['kernel', 'graph', 'adf', 'connect', 'port', 'PLIO']
    count = sum(1 for ind in indicators if ind in content)
    return count >= 2


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_header(filepath):
            continue
        if not _file_looks_like_graph_header(content):
            continue

        # Strategy 1: Remove 'using namespace adf;' directive
        using_pattern = re.compile(r'^[ \t]*using\s+namespace\s+adf\s*;[ \t]*\r?\n?', re.MULTILINE)
        for match in using_pattern.finditer(content):
            candidates.append({
                "file_path": filepath,
                "bug_type": "kernel_namespace_qualification_error",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": match.end(),
                "original": match.group(),
                "replacement": "",
                "description": "Remove 'using namespace adf;' directive, causing unqualified names to fail resolution."
            })

        # Strategy 2: Replace 'adf::kernel::create' with 'aie::kernel::create' (wrong namespace)
        adf_kernel_create_pattern = re.compile(r'adf::kernel::create\s*\(')
        for match in adf_kernel_create_pattern.finditer(content):
            original = match.group()
            replacement = original.replace('adf::', 'aie::', 1)
            candidates.append({
                "file_path": filepath,
                "bug_type": "kernel_namespace_qualification_error",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": "Replace 'adf::kernel::create(' with 'aie::kernel::create(' (wrong namespace prefix)."
            })

        # Strategy 3: Replace 'adf::kernel' with just 'kernel' (remove namespace qualifier)
        adf_kernel_pattern = re.compile(r'adf::kernel')
        for match in adf_kernel_pattern.finditer(content):
            candidates.append({
                "file_path": filepath,
                "bug_type": "kernel_namespace_qualification_error",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": match.end(),
                "original": match.group(),
                "replacement": "kernel",
                "description": "Remove 'adf::' prefix from 'adf::kernel', leaving unqualified 'kernel'."
            })

        # Strategy 4: Replace 'adf::connect' with 'connect' (remove namespace qualifier)
        adf_connect_pattern = re.compile(r'adf::connect')
        for match in adf_connect_pattern.finditer(content):
            candidates.append({
                "file_path": filepath,
                "bug_type": "kernel_namespace_qualification_error",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": match.end(),
                "original": match.group(),
                "replacement": "connect",
                "description": "Remove 'adf::' prefix from 'adf::connect', leaving unqualified 'connect'."
            })

        # Strategy 5: Replace 'adf::port' with 'port'
        adf_port_pattern = re.compile(r'adf::port')
        for match in adf_port_pattern.finditer(content):
            candidates.append({
                "file_path": filepath,
                "bug_type": "kernel_namespace_qualification_error",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": match.end(),
                "original": match.group(),
                "replacement": "port",
                "description": "Remove 'adf::' prefix from 'adf::port', leaving unqualified 'port'."
            })

        # Strategy 6: Replace 'adf::PLIO::create' with 'aie::PLIO::create'
        adf_plio_pattern = re.compile(r'adf::PLIO::create\s*\(')
        for match in adf_plio_pattern.finditer(content):
            original = match.group()
            replacement = original.replace('adf::', 'aie::', 1)
            candidates.append({
                "file_path": filepath,
                "bug_type": "kernel_namespace_qualification_error",
                "category": "graph_kernel_binding",
                "start": match.start(),
                "end": match.end(),
                "original": original,
                "replacement": replacement,
                "description": "Replace 'adf::PLIO::create(' with 'aie::PLIO::create(' (wrong namespace prefix)."
            })

        # Strategy 7: For files using 'using namespace adf;' with unqualified 'kernel::create(',
        # replace 'kernel::create(' with 'aie::kernel::create(' (adding wrong prefix)
        if using_pattern.search(content):
            unqualified_kernel_create = re.compile(r'(?<!adf::)(?<!aie::)(?<!\w)kernel::create\s*\(')
            for match in unqualified_kernel_create.finditer(content):
                # Make sure it's not inside the 'using namespace' line itself
                original = match.group()
                replacement = 'aie::' + original
                candidates.append({
                    "file_path": filepath,
                    "bug_type": "kernel_namespace_qualification_error",
                    "category": "graph_kernel_binding",
                    "start": match.start(),
                    "end": match.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": "Add wrong namespace prefix 'aie::' to unqualified 'kernel::create('."
                })

    return candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate to the project files."""
    new_files = dict(project_files)  # shallow copy of the dict
    filepath = candidate["file_path"]
    content = new_files[filepath]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]

    # Verify the original text is still at the expected position
    if content[start:end] == original:
        new_content = content[:start] + candidate["replacement"] + content[end:]
    else:
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, candidate["replacement"], 1)

    new_files[filepath] = new_content
    return new_files
