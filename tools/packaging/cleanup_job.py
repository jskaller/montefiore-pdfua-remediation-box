#!/usr/bin/env python3
"""
cleanup_job.py
Clears jobs/ working directory entries after the operator has confirmed
a Jira upload. Output files in output/ are never touched.

The jobs/ directory contains intermediate artifacts that can be 3-5x the
size of the source PDF (rendered page images, repair checkpoints, veraPDF
XML, pdfplumber maps). This script removes them once they are no longer
needed for debugging.

Outcome-aware cleanup (v2):
  PASS                  ← safe to clean
  REVIEW_REQUIRED       ← refuse cleanup unless --force (preserve for inspection)
  FAIL or ESCALATION    ← refuse cleanup unless --force (preserve for debugging)

The outcome is read from STATUS.json at job_dir/STATUS.json. If STATUS.json
is missing or unreadable, cleanup is refused unless --force.

Safety checks before any deletion:
  1. Confirms the job dir is inside workspace/jobs/ — never deletes elsewhere
  2. Confirms a matching output entry exists in output/ — will not delete
     a job whose output has not been promoted
  3. Reads STATUS.json to determine outcome
  4. Refuses cleanup of REVIEW_REQUIRED/FAIL/ESCALATION outcomes without --force
  5. Requires --confirm flag — dry-run by default

Usage:
  # Dry run — see what would be deleted (safe, default)
  cleanup_job.py MM-17893_consent_form
  cleanup_job.py --ticket MM-17893

  # Actually delete (only allowed if outcome was PASS)
  cleanup_job.py MM-17893_consent_form --confirm

  # Force cleanup of non-PASS outcomes (use with caution)
  cleanup_job.py MM-17893_consent_form --confirm --force

  # Specify workspace root explicitly (default: $WORKSPACE_PATH env var)
  cleanup_job.py MM-17893_consent_form --confirm --workspace /path/to/workspace

Exit codes:
  0  success (or dry run completed)
  1  safety check failed or outcome blocks cleanup (no deletion performed)
  2  argument or environment error
"""
import sys, json, shutil, argparse, os
from pathlib import Path
from datetime import datetime, timezone


parser = argparse.ArgumentParser(
    description='Clear jobs/ working directory after Jira upload confirmed'
)
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('job_name', nargs='?',
                   help='Single job directory name e.g. MM-17893_consent_form')
group.add_argument('--ticket',
                   help='Clear all jobs for a ticket e.g. MM-17893')
parser.add_argument('--confirm', action='store_true',
                    help='Actually delete. Without this flag, dry run only.')
parser.add_argument('--force', action='store_true',
                    help='Allow cleanup of REVIEW_REQUIRED/FAIL/ESCALATION outcomes.')
parser.add_argument('--workspace', default=None,
                    help='Path to workspace root. Defaults to $WORKSPACE_PATH.')
parser.add_argument('--skip-output-check', action='store_true',
                    help='Skip the output/ presence check (use with caution).')
parser.add_argument('--skip-status-check', action='store_true',
                    help='Skip the STATUS.json outcome check (use with extreme caution).')
args = parser.parse_args()


# ── Resolve workspace root ────────────────────────────────────────────────────

workspace_root = args.workspace or os.environ.get('WORKSPACE_PATH', '')
if not workspace_root:
    print(json.dumps({
        'result': 'ERROR',
        'error': (
            'Workspace path not set. Either pass --workspace or set '
            'WORKSPACE_PATH environment variable.'
        )
    }, indent=2))
    sys.exit(2)

workspace = Path(workspace_root).expanduser().resolve()
jobs_dir   = workspace / 'jobs'
output_dir = workspace / 'output'

if not workspace.exists():
    print(json.dumps({
        'result': 'ERROR',
        'error': f'Workspace not found: {workspace}'
    }, indent=2))
    sys.exit(2)

if not jobs_dir.exists():
    print(json.dumps({
        'result': 'ERROR',
        'error': f'jobs/ directory not found inside workspace: {jobs_dir}'
    }, indent=2))
    sys.exit(2)


# ── Resolve which job dirs to clean ──────────────────────────────────────────

def find_job_dirs(ticket: str) -> list:
    """Return all jobs/ subdirs whose name starts with the ticket prefix."""
    prefix = ticket.rstrip('_') + '_'
    return [d for d in jobs_dir.iterdir()
            if d.is_dir() and d.name.startswith(prefix)]


if args.job_name:
    targets = [jobs_dir / args.job_name]
else:
    targets = find_job_dirs(args.ticket)

if not targets:
    label = args.job_name or f'ticket {args.ticket}'
    print(json.dumps({
        'result': 'OK',
        'note':   f'No jobs/ entries found for {label}. Nothing to clean.',
        'dry_run': not args.confirm
    }, indent=2))
    sys.exit(0)


# ── Safety checks and collection ─────────────────────────────────────────────

def infer_ticket(job_dir_name: str) -> str:
    """Extract ticket prefix from job dir name e.g. MM-17893_consent -> MM-17893."""
    parts = job_dir_name.split('_')
    for i, part in enumerate(parts):
        if '-' in part and i < len(parts) - 1:
            return '_'.join(parts[:i+1])
    return parts[0]


def find_output_for_job(job_dir_name: str) -> Path | None:
    """Find matching output/MM-17893_remediated/ for a job dir."""
    ticket = infer_ticket(job_dir_name)
    expected = output_dir / f'{ticket}_remediated'
    return expected if expected.exists() else None


def read_outcome(job_dir: Path) -> str:
    """Read overall_result from STATUS.json. Returns 'MISSING' if unreadable."""
    status_path = job_dir / 'STATUS.json'
    if not status_path.exists():
        return 'MISSING'
    try:
        data = json.loads(status_path.read_text())
        return data.get('overall_result', 'UNKNOWN')
    except Exception:
        return 'UNREADABLE'


# Outcomes that block cleanup unless --force given
PROTECTED_OUTCOMES = {'REVIEW_REQUIRED', 'FAIL', 'ESCALATION', 'INCOMPLETE'}

results = []
errors  = []
protected = []  # outcomes that blocked cleanup but aren't errors

for target in targets:
    entry = {
        'job_dir':   str(target),
        'job_name':  target.name,
        'exists':    target.exists(),
        'deleted':   False,
        'dry_run':   not args.confirm,
    }

    # Must exist
    if not target.exists():
        entry['error'] = 'Job directory does not exist'
        errors.append(entry)
        results.append(entry)
        continue

    # Must be inside workspace/jobs/ — guard against path traversal
    try:
        target.resolve().relative_to(jobs_dir.resolve())
    except ValueError:
        entry['error'] = (
            f'SAFETY: {target} is not inside {jobs_dir}. Refusing to delete.'
        )
        errors.append(entry)
        results.append(entry)
        continue

    # Read job outcome unless explicitly skipped
    outcome = 'SKIPPED' if args.skip_status_check else read_outcome(target)
    entry['outcome'] = outcome

    # Compute size up front so it's available for both protected and processed entries.
    # This lets operators see how much disk a protected job is consuming during dry-run.
    try:
        size_bytes = sum(
            f.stat().st_size for f in target.rglob('*') if f.is_file()
        )
        file_count = sum(1 for f in target.rglob('*') if f.is_file())
        entry['size_bytes'] = size_bytes
        entry['size_mb']    = round(size_bytes / 1_048_576, 2)
        entry['file_count'] = file_count
    except Exception:
        entry['size_bytes'] = 0
        entry['size_mb']    = 0
        entry['file_count'] = 0

    if not args.skip_status_check:
        if outcome in ('MISSING', 'UNREADABLE'):
            if not args.force:
                entry['protected_reason'] = (
                    f'STATUS.json {outcome.lower()}. '
                    f'Refusing cleanup without --force or --skip-status-check.'
                )
                protected.append(entry)
                results.append(entry)
                continue
        elif outcome in PROTECTED_OUTCOMES:
            if not args.force:
                entry['protected_reason'] = (
                    f'Job outcome was {outcome}. '
                    f'Refusing cleanup without --force. '
                    f'Job directory preserved for inspection/debugging.'
                )
                protected.append(entry)
                results.append(entry)
                continue

    # Must have a matching output/ entry (unless skipped)
    if not args.skip_output_check:
        output_match = find_output_for_job(target.name)
        if output_match is None:
            entry['error'] = (
                f'No matching output/ directory found for this job. '
                f'Expected: {output_dir}/{infer_ticket(target.name)}_remediated/. '
                f'Run with --skip-output-check only if you are certain the '
                f'output was promoted elsewhere.'
            )
            entry['output_found'] = False
            errors.append(entry)
            results.append(entry)
            continue
        entry['output_found'] = str(output_match)

    if args.confirm:
        try:
            shutil.rmtree(target)
            entry['deleted']    = True
            entry['deleted_at'] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            entry['error']   = f'Deletion failed: {e}'
            entry['deleted'] = False
            errors.append(entry)

    results.append(entry)


# ── Output ────────────────────────────────────────────────────────────────────

total_freed_mb = sum(
    r.get('size_mb', 0) for r in results if r.get('deleted')
)
would_free_mb = sum(
    r.get('size_mb', 0) for r in results
    if not r.get('deleted') and not r.get('error') and not args.confirm
)

output = {
    'result':           'OK' if not errors else 'PARTIAL' if results else 'FAIL',
    'dry_run':          not args.confirm,
    'force':            args.force,
    'jobs_processed':   len(results),
    'jobs_deleted':     sum(1 for r in results if r.get('deleted')),
    'jobs_protected':   len(protected),
    'jobs_errored':     len(errors),
    'freed_mb':         total_freed_mb if args.confirm else None,
    'would_free_mb':    would_free_mb if not args.confirm else None,
    'details':          results,
}

if not args.confirm:
    output['note'] = (
        'Dry run — no files deleted. '
        'Re-run with --confirm to actually delete.'
    )

print(json.dumps(output, indent=2))
# Exit non-zero only on genuine errors (path traversal, missing dir, deletion failure).
# Refusing cleanup of protected outcomes (REVIEW_REQUIRED/FAIL/ESCALATION) is
# correct behavior, not an error — exit 0.
sys.exit(0 if not errors else 1)
