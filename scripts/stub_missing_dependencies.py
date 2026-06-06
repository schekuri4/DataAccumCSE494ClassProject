#!/usr/bin/env python3
"""
Generate conservative stub files for missing dependencies listed in an audit
manifest or dependency report.

By default this script reads the full golden-repos audit manifest and writes
stubs into golden repos/<project>/<missing_reference> so subsequent resolution
passes see them as available local files.
"""
import argparse
import json
import os
from pathlib import Path


def sanitize_symbol(name: str) -> str:
    out = []
    for ch in name:
        if ch.isalnum() or ch == '_':
            out.append(ch)
        else:
            out.append('_')
    result = ''.join(out).strip('_')
    return result or 'stub'


def stub_content(rel_path: str) -> str:
    leaf = os.path.basename(rel_path)
    _, ext = os.path.splitext(leaf)
    guard = sanitize_symbol((rel_path.replace('\\', '_').replace('/', '_')).upper()) + '_'

    if ext.lower() in {'.h', '.hpp', '.hh', '.hxx'}:
        lines = [
            f'#ifndef {guard}',
            f'#define {guard}',
            '',
            '// Auto-generated stub to satisfy a missing dependency during dataset recovery.',
            '// Replace with the real upstream file when available.',
            '',
            f'#endif  // {guard}',
            '',
        ]
        return '\n'.join(lines)

    if ext.lower() in {'.cc', '.cpp', '.cxx', '.c'}:
        return (
            '// Auto-generated stub translation unit to satisfy a missing dependency.\n'
            '// Replace with the real upstream file when available.\n'
        )

    return (
        '// Auto-generated stub to satisfy a missing dependency.\n'
        '// Replace with the real upstream file when available.\n'
    )


def load_entries(report_path: str):
    seen = set()
    entries = []
    with open(report_path, 'r', encoding='utf-8') as f:
        for ln in f:
            if not ln.strip():
                continue
            obj = json.loads(ln)
            project_path = obj.get('path') or obj.get('project_path') or ''
            project_name = obj.get('project') or (os.path.basename(project_path.rstrip('\\/')) if project_path else None)
            missing_items = obj.get('missing') or []
            for missing in missing_items:
                rel = missing.get('reference')
                if not project_name or not rel:
                    continue
                key = (project_name, rel)
                if key in seen:
                    continue
                seen.add(key)
                entries.append((project_name, rel))
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', default='Work/golden_repos_local_ref_audit_manifest.jsonl')
    ap.add_argument('--root', default='.')
    ap.add_argument('--apply', action='store_true', default=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    report = os.path.abspath(os.path.join(root, args.report))
    entries = load_entries(report)

    created = 0
    skipped = 0
    for project_name, rel in entries:
        dest = os.path.join(root, 'golden repos', project_name, os.path.normpath(rel))
        if os.path.exists(dest):
            skipped += 1
            continue
        if args.dry_run:
            print('WOULD CREATE', dest)
            created += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w', encoding='utf-8', newline='\n') as f:
            f.write(stub_content(rel))
        print('CREATED', dest)
        created += 1

    print(f'Done. entries={len(entries)} created={created} skipped={skipped}')


if __name__ == '__main__':
    main()
