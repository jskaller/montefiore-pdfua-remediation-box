#!/usr/bin/env python3
"""
checksums.py
Generates SHA-256 checksums for all files in a deliverable package directory,
writes a SHA256SUMS.txt, and optionally verifies against an existing one.

Usage:
  checksums.py generate <dir> [--out SHA256SUMS.txt]
  checksums.py verify   <dir> <SHA256SUMS.txt>
"""
import sys, json, hashlib, argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['generate', 'verify'])
parser.add_argument('dir')
parser.add_argument('checksum_file', nargs='?')
parser.add_argument('--out', default='SHA256SUMS.txt')
args = parser.parse_args()

root = Path(args.dir)
if not root.exists():
    print(json.dumps({'result': 'ERROR', 'error': f'Directory not found: {root}'}))
    sys.exit(2)

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()

if args.mode == 'generate':
    out_path = root / args.out
    entries = []
    for p in sorted(root.rglob('*')):
        if p.is_file() and p != out_path:
            rel = p.relative_to(root)
            digest = sha256(p)
            entries.append((digest, str(rel)))

    lines = [f'{d}  {r}\n' for d, r in entries]
    out_path.write_text(''.join(lines))

    print(json.dumps({
        'result':   'OK',
        'mode':     'generate',
        'dir':      str(root),
        'files':    len(entries),
        'written':  str(out_path)
    }, indent=2))
    sys.exit(0)

elif args.mode == 'verify':
    checksum_path = Path(args.checksum_file or (root / 'SHA256SUMS.txt'))
    if not checksum_path.exists():
        print(json.dumps({'result': 'ERROR', 'error': f'Checksum file not found: {checksum_path}'}))
        sys.exit(2)

    expected = {}
    for line in checksum_path.read_text().splitlines():
        line = line.strip()
        if '  ' in line:
            digest, path = line.split('  ', 1)
            expected[path] = digest

    results = []
    for rel_path, exp_digest in expected.items():
        full_path = root / rel_path
        if not full_path.exists():
            results.append({'file': rel_path, 'status': 'MISSING'})
        else:
            actual = sha256(full_path)
            results.append({
                'file':   rel_path,
                'status': 'OK' if actual == exp_digest else 'MISMATCH',
                'expected': exp_digest,
                'actual':   actual
            })

    failures = [r for r in results if r['status'] != 'OK']
    print(json.dumps({
        'result':   'PASS' if not failures else 'FAIL',
        'mode':     'verify',
        'checked':  len(results),
        'failures': len(failures),
        'details':  results
    }, indent=2))
    sys.exit(0 if not failures else 1)
