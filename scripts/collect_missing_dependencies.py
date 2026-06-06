#!/usr/bin/env python3
"""
Collect missing dependency files reported by the recovered-missing-refs audit and try to locate them
across the workspace. Optionally copy found files into the corresponding golden repo under 'golden repos/'.

Usage: python scripts/collect_missing_dependencies.py --manifest <manifest.jsonl> [--root .] [--apply]

This is a conservative helper: by default it only prints candidates. Use --apply to copy files into
golden repos/<project>/ preserving the reference subpath.
"""
import argparse
import json
import os
import shutil
import time
import urllib.request
import urllib.parse
from pathlib import Path


def load_manifest(manifest_path):
    objs = []
    with open(manifest_path, 'r', encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                objs.append(json.loads(ln))
            except Exception:
                # some lines may be truncated or not JSON objects; ignore
                continue
    return objs


def find_candidates(root_dirs, basename):
    """Search the provided roots for files ending with basename. Return list of absolute paths."""
    matches = []
    seen = set()
    for root in root_dirs:
        for dirpath, dirs, files in os.walk(root):
            for fn in files:
                if fn == basename:
                    p = os.path.join(dirpath, fn)
                    if p not in seen:
                        seen.add(p)
                        matches.append(os.path.abspath(p))
    return matches


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', required=True)
    p.add_argument('--root', default='.')
    p.add_argument('--apply', action='store_true', help='Copy found files into golden repos')
    p.add_argument('--overwrite-stubs', action='store_true', help='Replace existing stub files with recovered real files')
    p.add_argument('--fetch-remote', action='store_true', help='Try GitHub code search to fetch missing files when no local candidate exists')
    p.add_argument('--verbose', '-v', action='store_true')
    args = p.parse_args()

    manifest_path = os.path.abspath(args.manifest)
    root = os.path.abspath(args.root)
    workspace_roots = [root,
                       os.path.join(root, 'external'),
                       os.path.join(root, 'Work'),
                       os.path.join(root, 'golden repos'),
                       os.path.join(root, 'data'),
                       os.path.join(root, 'raw')]

    objs = load_manifest(manifest_path)
    results = []
    for obj in objs:
        project_path = obj.get('path') or obj.get('project')
        project_name = None
        if project_path:
            project_name = os.path.basename(project_path)
        missing = obj.get('missing') or []
        for m in missing:
            ref = m.get('reference')
            if not ref:
                continue
            basename = os.path.basename(ref)
            candidates = find_candidates(workspace_roots, basename)
            results.append({
                'project': project_name,
                'project_path': project_path,
                'missing_reference': ref,
                'basename': basename,
                'candidates': candidates,
            })
            if args.verbose:
                print('PROJECT', project_name, 'REF', ref, 'FOUND', len(candidates))

            if args.apply and candidates and project_name:
                # pick the first candidate and copy it into golden repos/<project_name>/<ref_parent>
                src = candidates[0]
                dest_base = os.path.join(root, 'golden repos', project_name)
                # preserve the relative reference path: e.g. weights/foo.h -> dest_base/weights/foo.h
                dest_path = os.path.join(dest_base, os.path.normpath(ref))
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                should_copy = not os.path.exists(dest_path)
                if not should_copy and args.overwrite_stubs:
                    try:
                        with open(dest_path, 'r', encoding='utf-8', errors='replace') as existing:
                            existing_text = existing.read(4096)
                        should_copy = 'Auto-generated stub' in existing_text or 'Replace with the real upstream file' in existing_text
                    except Exception:
                        should_copy = False
                if should_copy:
                    try:
                        shutil.copy2(src, dest_path)
                        print('COPIED', src, '->', dest_path)
                    except Exception as exc:
                        if args.verbose:
                            print('SKIP COPY FAILED:', src, '->', dest_path, 'err:', exc)
                else:
                    if args.verbose:
                        print('SKIP (exists):', dest_path)

            # If no GITHUB token in env, attempt to load from common .env locations under the workspace root or venv
            if args.fetch_remote:
                gh_token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
                if not gh_token:
                    # try reading from .venv/.env or .env in workspace root
                    for candidate_env in [os.path.join(root, '.venv', '.env'), os.path.join(root, '.env'), os.path.join(root, '.venv', '.env')]:
                        try:
                            if os.path.exists(candidate_env):
                                with open(candidate_env, 'r', encoding='utf-8') as ef:
                                    for line in ef:
                                        line=line.strip()
                                        if not line or line.startswith('#'):
                                            continue
                                        if '=' in line:
                                            k,v = line.split('=',1)
                                            k=k.strip(); v=v.strip()
                                            if k in ('GITHUB_TOKEN','GH_TOKEN') and v:
                                                os.environ[k]=v
                                                gh_token=v
                                                break
                        except Exception:
                            continue

            # if no local candidates, optionally try remote fetch via GitHub code search
            if (not candidates) and args.fetch_remote and project_name:
                basename = os.path.basename(ref)
                print('TRY REMOTE SEARCH FOR', basename)
                try:
                    q = urllib.parse.quote_plus(f'filename:{basename}')
                    url = f'https://api.github.com/search/code?q={q}&per_page=5'
                    headers = {'User-Agent': 'aide-collector'}
                    # Use GITHUB_TOKEN from environment if available to authenticate requests
                    gh_token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
                    if gh_token:
                        headers['Authorization'] = f'token {gh_token}'
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.load(resp)
                        items = data.get('items', [])
                        for it in items:
                            repo = it.get('repository', {})
                            full = repo.get('full_name')
                            branch = repo.get('default_branch') or 'main'
                            path = it.get('path')
                            if not full or not path:
                                continue
                            raw = f'https://raw.githubusercontent.com/{full}/{branch}/{path}'
                            try:
                                # fetch raw file; if token present include Authorization header
                                raw_req = urllib.request.Request(raw, headers=headers)
                                with urllib.request.urlopen(raw_req, timeout=15) as r2:
                                    content = r2.read()
                                    dest_base = os.path.join(root, 'golden repos', project_name)
                                    dest_path = os.path.join(dest_base, os.path.normpath(ref))
                                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                                    should_copy = not os.path.exists(dest_path)
                                    if not should_copy and args.overwrite_stubs:
                                        try:
                                            with open(dest_path, 'r', encoding='utf-8', errors='replace') as existing:
                                                existing_text = existing.read(4096)
                                            should_copy = 'Auto-generated stub' in existing_text or 'Replace with the real upstream file' in existing_text
                                        except Exception:
                                            should_copy = False
                                    if should_copy:
                                        try:
                                            with open(dest_path, 'wb') as outp:
                                                outp.write(content)
                                            print('FETCHED', raw, '->', dest_path)
                                            candidates = [dest_path]
                                            break
                                        except Exception as exc:
                                            if args.verbose:
                                                print('SKIP FETCH FAILED:', raw, '->', dest_path, 'err:', exc)
                            except Exception:
                                # try next candidate
                                continue
                    # be polite to the API
                    time.sleep(1)
                except Exception as e:
                    if args.verbose:
                        print('remote search failed for', basename, 'err:', e)

    # write a simple report
    report_path = os.path.join(root, 'Work', 'collected_missing_deps_report.jsonl')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as outf:
        for r in results:
            outf.write(json.dumps(r) + '\n')

    print('\nDone. Report written to', report_path)
    n_missing = sum(1 for r in results if not r['candidates'])
    print('Missing references with no candidates found:', n_missing)


if __name__ == '__main__':
    main()
