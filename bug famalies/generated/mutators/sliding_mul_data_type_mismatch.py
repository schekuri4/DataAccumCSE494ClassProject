import re
import copy
from typing import Any

BUG_FAMILY: dict[str, Any] = {
    "family_id": "BF263",
    "bug_type": "sliding_mul_data_type_mismatch",
    "category": "sliding_mul_and_mac",
    "target_files": ["kernel source"],
    "artifact_handling": "modify_existing_file",
    "match_targets": [
        "aie::sliding_mul_ops",
        "aie::sliding_mac_ops",
        "DataStepX",
        "CoeffType",
        "DataType",
    ],
    "mutation_strategy": (
        "Change the DataType or CoeffType template parameter to a type that is "
        "incompatible with the chosen Lanes/Points combination. For example, use "
        "float as CoeffType with int16 as DataType in sliding_mul_ops, or use "
        "cint32 where only cint16 is supported for the given lane configuration."
    ),
    "repair_expectation": (
        "Restore the correct data type pairing that is supported by the AIE "
        "sliding_mul hardware intrinsic for the given Lanes and Points."
    ),
    "validation_signal": (
        "WSL Vitis/AIE compile failure with type mismatch or no matching "
        "specialization for the sliding_mul_ops template."
    ),
    "tags": [
        "compile_time",
        "data_type",
        "sliding_mul",
        "sliding_mul_and_mac",
        "type_mismatch",
    ],
}

# Mapping of AIE types to incompatible replacements for mutation
_TYPE_MUTATIONS: dict[str, str] = {
    "int16": "float",
    "int16_t": "float",
    "int32": "cint32",
    "int32_t": "cint32",
    "cint16": "cint32",
    "cint32": "float",
    "float": "int16",
    "int8": "float",
    "int8_t": "float",
    "uint8": "cint16",
    "uint8_t": "cint16",
    "uint16": "cint32",
    "uint16_t": "cint32",
}

# Known AIE data types to look for in template parameters
_AIE_TYPES = sorted(_TYPE_MUTATIONS.keys(), key=lambda x: -len(x))


def _is_kernel_source(path: str) -> bool:
    """Heuristic: kernel source files are .cpp, .cc, .h, .hpp files."""
    lower = path.lower()
    return any(lower.endswith(ext) for ext in (".cpp", ".cc", ".h", ".hpp", ".c"))


def _find_sliding_ops_instances(content: str):
    """Find all sliding_mul/sliding_mac template instantiations."""
    # Match old ops-style and modern direct APIs.
    pattern = re.compile(
        r'((?:::)?aie::sliding_m(?:ul|ac)(?:_ops)?)\s*<([^>]+)>'
    )
    return list(pattern.finditer(content))


def _get_incompatible_type(original_type: str) -> str | None:
    """Return an incompatible type for mutation."""
    return _TYPE_MUTATIONS.get(original_type)


def find_mutation_candidates(project_files: dict[str, str]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []

    for file_path, content in project_files.items():
        if not _is_kernel_source(file_path):
            continue

        matches = _find_sliding_ops_instances(content)
        for match in matches:
            op_name = match.group(1)
            template_params = match.group(2)
            template_start = match.start(2)

            # Parse template parameters (comma-separated)
            params = [p.strip() for p in template_params.split(',')]

            # For aie::sliding_mul_ops / sliding_mac_ops, typical template signature:
            # <Lanes, Points, CoeffStep, DataStepX, DataStepY, CoeffType, DataType, AccumTag>
            # We look for type parameters that match known AIE types

            # Track position within the template parameter string
            param_offset = 0
            for i, param in enumerate(params):
                # Find the actual position of this param in the template string
                param_pos_in_template = template_params.find(param, param_offset)
                if param_pos_in_template == -1:
                    param_offset += len(param) + 1
                    continue

                # Check if this parameter is a known AIE type
                stripped_param = param.strip()
                for aie_type in _AIE_TYPES:
                    if stripped_param == aie_type:
                        replacement_type = _get_incompatible_type(aie_type)
                        if replacement_type is None:
                            continue

                        # Calculate absolute positions in the file
                        abs_start = template_start + param_pos_in_template
                        abs_end = abs_start + len(stripped_param)

                        # Determine if this is likely CoeffType or DataType based on position
                        # Typically CoeffType comes before DataType in the parameter list
                        type_role = "CoeffType" if i <= len(params) - 2 else "DataType"
                        # Better heuristic: look at surrounding context for named params
                        # For positional, in standard AIE API:
                        # param index 5 = CoeffType, index 6 = DataType (0-indexed)
                        if i == 5:
                            type_role = "CoeffType"
                        elif i == 6:
                            type_role = "DataType"

                        description = (
                            f"Change {type_role} from '{aie_type}' to incompatible "
                            f"'{replacement_type}' in {op_name} template instantiation"
                        )

                        candidates.append({
                            "file_path": file_path,
                            "bug_type": "sliding_mul_data_type_mismatch",
                            "category": "sliding_mul_and_mac",
                            "start": abs_start,
                            "end": abs_end,
                            "original": aie_type,
                            "replacement": replacement_type,
                            "description": description,
                        })
                        break  # Only one mutation per parameter

                param_offset = param_pos_in_template + len(param)

        if 'sliding_mul' in content or 'sliding_mac' in content or 'mul4' in content or 'mac4' in content:
            # Modern sliding APIs often infer coefficient/data element types
            # from nearby vector declarations instead of spelling them in the
            # template argument list.
            vector_decl_pattern = re.compile(
                r'\b(v(?:4|8|16|32)(?:c?int(?:8|16|32)|float)|'
                r'(?:const\s+)?(?:int16_t|int32_t|uint8_t|uint16_t|float))\b'
            )
            for decl_match in vector_decl_pattern.finditer(content):
                original_type = decl_match.group(1)
                normalized = original_type
                replacement_type = None
                if original_type.startswith('v'):
                    vector_map = {
                        'v4int16': 'v4float',
                        'v8int16': 'v8float',
                        'v16int16': 'v16float',
                        'v32int16': 'v32float',
                        'v4cint16': 'v4cint32',
                        'v8cint16': 'v8cint32',
                        'v16cint16': 'v16cint32',
                        'v4int32': 'v4cint32',
                        'v8int32': 'v8cint32',
                        'v16int32': 'v16cint32',
                    }
                    replacement_type = vector_map.get(original_type)
                else:
                    normalized = original_type.replace('const ', '')
                    replacement_type = _get_incompatible_type(normalized)

                if replacement_type is None:
                    continue

                candidates.append({
                    "file_path": file_path,
                    "bug_type": "sliding_mul_data_type_mismatch",
                    "category": "sliding_mul_and_mac",
                    "start": decl_match.start(1),
                    "end": decl_match.end(1),
                    "original": original_type,
                    "replacement": replacement_type,
                    "description": (
                        f"Changed nearby sliding multiply data/coefficient type "
                        f"from '{original_type}' to incompatible '{replacement_type}'."
                    ),
                })

        # Also look for using/typedef declarations that define DataType or CoeffType
        # used with sliding_mul/mac
        typedef_pattern = re.compile(
            r'(using\s+(?:DataType|CoeffType)\s*=\s*)(' + '|'.join(re.escape(t) for t in _AIE_TYPES) + r')\s*;'
        )
        for td_match in typedef_pattern.finditer(content):
            original_type = td_match.group(2)
            replacement_type = _get_incompatible_type(original_type)
            if replacement_type is None:
                continue

            # Check if this file also contains sliding_mul_ops or sliding_mac_ops
            if 'sliding_mul' not in content and 'sliding_mac' not in content:
                continue

            abs_start = td_match.start(2)
            abs_end = td_match.end(2)

            # Determine role from the alias name
            prefix = td_match.group(1)
            if 'CoeffType' in prefix:
                type_role = "CoeffType"
            else:
                type_role = "DataType"

            description = (
                f"Change {type_role} typedef from '{original_type}' to incompatible "
                f"'{replacement_type}' affecting sliding_mul/mac operations"
            )

            candidates.append({
                "file_path": file_path,
                "bug_type": "sliding_mul_data_type_mismatch",
                "category": "sliding_mul_and_mac",
                "start": abs_start,
                "end": abs_end,
                "original": original_type,
                "replacement": replacement_type,
                "description": description,
            })

        # Also look for template aliases or variable declarations with these types
        # near sliding_mul/mac usage
        decl_pattern = re.compile(
            r'((?:static\s+)?(?:constexpr\s+)?(?:auto|' +
            '|'.join(re.escape(t) for t in _AIE_TYPES) +
            r'))\b'
        )
        # Look for DataStepX assignments that might indicate type context
        # and direct type mentions in sliding_mul template args via macros or constexpr

    return candidates


def apply_mutation(project_files: dict[str, str], candidate: dict[str, object]) -> dict[str, str]:
    """Apply a single mutation candidate, returning a new project_files dict."""
    new_files = dict(project_files)  # shallow copy of the dict

    file_path = candidate["file_path"]
    content = new_files[file_path]

    start = candidate["start"]
    end = candidate["end"]
    original = candidate["original"]
    replacement = candidate["replacement"]

    # Verify the original text is at the expected position
    actual = content[start:end]
    if actual != original:
        # Fallback: try to find and replace first occurrence
        idx = content.find(original)
        if idx == -1:
            return new_files
        new_content = content[:idx] + replacement + content[idx + len(original):]
    else:
        new_content = content[:start] + replacement + content[end:]

    new_files[file_path] = new_content
    return new_files
