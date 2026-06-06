import re
import copy

BUG_FAMILY = {
    "family_id": "BF019",
    "bug_type": "header_guard_prevents_rtp_api_inclusion",
    "category": "header_guards_and_preprocessor",
    "target_files": [
        "graph header",
        "shared utility header"
    ],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::connect<adf::parameter>",
        "adf::async",
        "adf::runtime<ratio>",
        "#ifndef ADF_H",
        "#include <adf.h>"
    ],
    "mutation_strategy": "Define a macro (e.g., ADF_H or _ADF_H_) before #include <adf.h> that collides with the internal include guard of adf.h or a project header containing RTP port declarations, preventing RTP-related APIs (connect<parameter>, async, runtime<ratio>) from being declared.",
    "repair_expectation": "Remove or rename the conflicting macro definition that shadows the adf.h include guard.",
    "validation_signal": "WSL Vitis/AIE compile failure with undeclared adf::parameter, adf::async, or adf::runtime<ratio> errors.",
    "tags": [
        "adf_h",
        "header_guards_and_preprocessor",
        "include_guard",
        "macro_shadow",
        "parameter",
        "rtp"
    ]
}


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates = []
    
    # Look for header files that include <adf.h> and use RTP-related APIs
    rtp_patterns = [
        re.compile(r'adf::connect\s*<\s*adf::parameter\s*>'),
        re.compile(r'adf::async'),
        re.compile(r'adf::runtime\s*<\s*ratio\s*>'),
    ]
    
    # Pattern to find #include <adf.h> or #include "adf.h"
    include_adf_pattern = re.compile(r'^(\s*#\s*include\s*[<"]adf\.h[>"])', re.MULTILINE)
    
    for file_path, content in project_files.items():
        # Target graph headers and shared utility headers (.h, .hpp files)
        if not (file_path.endswith('.h') or file_path.endswith('.hpp')):
            continue
        
        # Check if file includes adf.h
        include_match = include_adf_pattern.search(content)
        if not include_match:
            continue
        
        # Check if file uses RTP-related APIs or is likely a graph header
        has_rtp_usage = any(p.search(content) for p in rtp_patterns)
        is_graph_header = 'graph' in file_path.lower() or 'adf::graph' in content or 'class' in content
        
        if not (has_rtp_usage or is_graph_header):
            continue
        
        # Found a valid mutation site: insert #define ADF_H before #include <adf.h>
        include_start = include_match.start()
        include_end = include_match.end()
        original_line = include_match.group(1)
        
        # Try different guard macro names that might collide
        for guard_macro in ["ADF_H", "_ADF_H_", "__ADF_H__", "_ADF_H"]:
            replacement = "#define {}\n{}".format(guard_macro, original_line)
            
            candidate = {
                "file_path": file_path,
                "bug_type": "header_guard_prevents_rtp_api_inclusion",
                "category": "header_guards_and_preprocessor",
                "start": include_start,
                "end": include_end,
                "original": original_line,
                "replacement": replacement,
                "description": (
                    "Define macro '{}' before #include <adf.h> to shadow the internal "
                    "include guard of adf.h, preventing RTP-related API declarations "
                    "(adf::parameter, adf::async, adf::runtime<ratio>) from being included."
                ).format(guard_macro)
            }
            candidates.append(candidate)
    
    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    new_project_files = dict(project_files)  # shallow copy of the dict
    
    file_path = candidate["file_path"]
    content = new_project_files[file_path]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: find and replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_project_files[file_path] = new_content
    return new_project_files
