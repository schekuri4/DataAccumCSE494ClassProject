import re
import copy

BUG_FAMILY = {
    "family_id": "BF455",
    "bug_type": "misspelled_runtime_api",
    "category": "api_spelling_regressions",
    "target_files": ["graph header", "graph source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "adf::runtime<ratio>",
        "runtime<ratio>",
        "adf::async(",
        "adf::read_access"
    ],
    "mutation_strategy": "Misspell the runtime constraint API used in graph definitions, such as runtime -> runtme or runtime -> runetime, or typo the ratio token used in the template expression so the constraint cannot be parsed.",
    "repair_expectation": "Restore the exact adf::runtime<ratio>(...) syntax and any required compile-time ratio arguments.",
    "validation_signal": "WSL Vitis/AIE compile failure with an undeclared identifier or template parsing error for the runtime API.",
    "tags": ["adf", "api_spelling_regressions", "compile_time", "constraint", "runtime", "spelling"]
}

# Patterns and their misspellings
_MUTATION_PATTERNS = [
    # Pattern: adf::runtime<ratio> variants (with possible whitespace and actual ratio args)
    {
        "regex": r'(adf\s*::\s*)runtime(\s*<\s*)ratio(\s*>)',
        "group_replacements": {
            # Misspell 'runtime' -> 'runtme'
            "runtime_typo": (1, "runtme", 2, None, 3, None),
        },
        "description_template": "Misspelled 'adf::runtime<ratio>' as 'adf::runtme<ratio>'"
    },
    {
        "regex": r'(?<!adf\s*::\s*)(?<!\w)runtime(\s*<\s*)ratio(\s*>)',
        "simple_replace": ("runtime", "runtme"),
        "description_template": "Misspelled 'runtime<ratio>' as 'runtme<ratio>'"
    },
    # Misspell ratio -> rato in adf::runtime<ratio>
    {
        "regex": r'(adf\s*::\s*runtime\s*<\s*)ratio(\s*>)',
        "simple_replace": ("ratio", "rato"),
        "description_template": "Misspelled 'ratio' as 'rato' in adf::runtime<ratio>"
    },
    # adf::async( -> adf::asyc(
    {
        "regex": r'(adf\s*::\s*)async(\s*\()',
        "simple_replace": ("async", "asyc"),
        "description_template": "Misspelled 'adf::async(' as 'adf::asyc('"
    },
    # adf::read_access -> adf::read_acess
    {
        "regex": r'(adf\s*::\s*)read_access',
        "simple_replace": ("read_access", "read_acess"),
        "description_template": "Misspelled 'adf::read_access' as 'adf::read_acess'"
    },
]


def _is_graph_file(filepath):
    """Heuristic to identify graph header or graph source files."""
    lower = filepath.lower()
    # Common patterns for graph files in AIE projects
    if 'graph' in lower:
        return True
    # Also consider .h/.hpp/.cpp files that might contain graph definitions
    if lower.endswith(('.h', '.hpp', '.cpp', '.cc', '.cxx')):
        return True
    return False


def find_mutation_candidates(project_files):
    candidates = []

    for filepath, content in project_files.items():
        if not _is_graph_file(filepath):
            continue

        # Check for each match target pattern
        # Pattern 1: adf::runtime<ratio> with possible whitespace and real ratio expressions
        # We use a broad regex to capture runtime<...> patterns
        
        # Match adf::runtime< ... > where the template arg contains 'ratio'
        for m in re.finditer(r'adf\s*::\s*runtime\s*<\s*ratio\s*(?:\([^)]*\))?\s*>', content):
            original = m.group(0)
            # Misspell runtime -> runtme
            replacement = re.sub(r'runtime', 'runtme', original, count=1)
            candidates.append({
                "file_path": filepath,
                "bug_type": "misspelled_runtime_api",
                "category": "api_spelling_regressions",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": "Misspelled 'runtime' as 'runtme' in adf::runtime<ratio> constraint"
            })
            # Also offer ratio -> rato variant
            replacement2 = re.sub(r'ratio', 'rato', original, count=1)
            if replacement2 != original:
                candidates.append({
                    "file_path": filepath,
                    "bug_type": "misspelled_runtime_api",
                    "category": "api_spelling_regressions",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement2,
                    "description": "Misspelled 'ratio' as 'rato' in adf::runtime<ratio> constraint"
                })

        # Match standalone runtime<ratio> (not preceded by adf::)
        for m in re.finditer(r'(?<!::\s{0,5})(?<!\w)runtime\s*<\s*ratio\s*(?:\([^)]*\))?\s*>', content):
            # Make sure it's not part of adf::runtime (check preceding text)
            preceding = content[max(0, m.start()-10):m.start()]
            if '::' in preceding:
                continue
            original = m.group(0)
            replacement = re.sub(r'runtime', 'runetime', original, count=1)
            candidates.append({
                "file_path": filepath,
                "bug_type": "misspelled_runtime_api",
                "category": "api_spelling_regressions",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": "Misspelled 'runtime' as 'runetime' in runtime<ratio> constraint"
            })

        # Match adf::runtime with broader template args (e.g., runtime<ratio(N,D)>)
        for m in re.finditer(r'adf\s*::\s*runtime\s*<\s*ratio\s*\([^)]*\)\s*>', content):
            original = m.group(0)
            replacement = re.sub(r'runtime', 'runtme', original, count=1)
            # Avoid duplicates
            dup = False
            for c in candidates:
                if c["file_path"] == filepath and c["start"] == m.start() and c["replacement"] == replacement:
                    dup = True
                    break
            if not dup:
                candidates.append({
                    "file_path": filepath,
                    "bug_type": "misspelled_runtime_api",
                    "category": "api_spelling_regressions",
                    "start": m.start(),
                    "end": m.end(),
                    "original": original,
                    "replacement": replacement,
                    "description": "Misspelled 'runtime' as 'runtme' in adf::runtime<ratio(...)> constraint"
                })

        # Match adf::async(
        for m in re.finditer(r'adf\s*::\s*async\s*\(', content):
            original = m.group(0)
            replacement = re.sub(r'async', 'asyc', original, count=1)
            candidates.append({
                "file_path": filepath,
                "bug_type": "misspelled_runtime_api",
                "category": "api_spelling_regressions",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": "Misspelled 'async' as 'asyc' in adf::async() call"
            })

        # Match adf::read_access
        for m in re.finditer(r'adf\s*::\s*read_access', content):
            original = m.group(0)
            replacement = re.sub(r'read_access', 'read_acess', original, count=1)
            candidates.append({
                "file_path": filepath,
                "bug_type": "misspelled_runtime_api",
                "category": "api_spelling_regressions",
                "start": m.start(),
                "end": m.end(),
                "original": original,
                "replacement": replacement,
                "description": "Misspelled 'read_access' as 'read_acess' in adf::read_access"
            })

    # Deduplicate candidates based on (file_path, start, end, replacement)
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = (c["file_path"], c["start"], c["end"], c["replacement"])
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    return unique_candidates


def apply_mutation(project_files, candidate):
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_files = dict(project_files)  # shallow copy of the dict
    
    filepath = candidate["file_path"]
    content = new_files[filepath]
    
    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]
    
    # Verify the original text is at the expected position
    if content[start:end] == original:
        new_content = content[:start] + replacement + content[end:]
    else:
        # Fallback: replace first occurrence
        new_content = content.replace(original, replacement, 1)
    
    new_files[filepath] = new_content
    return new_files
