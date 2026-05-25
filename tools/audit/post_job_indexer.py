#!/usr/bin/env python3
"""
post_job_indexer.py
Runs after a completed remediation job and updates rule_repair_map.json
with confirmed outcomes. Follows the principle: write once, update on deviation.

Behaviour:
  - Rule ID already in map, same script used, outcome PASS → increment confirmed_jobs only
  - Rule ID already in map, different script used → add edge_case entry, don't overwrite primary
  - Rule ID already in map, outcome FAIL → downgrade confidence, log failure_mode
  - Rule ID not in map, outcome PASS → add new entry with confidence EXPECTED (not CONFIRMED —
    one job isn't enough; needs manual review before promoting to CONFIRMED)
  - Rule ID not in map, outcome FAIL → add with confidence MANUAL, flag for review

Usage:
  post_job_indexer.py <job_dir> --map <rule_repair_map.json>

  <job_dir>  — the jobs/{TICKET}_{basename}/ directory for the completed job
  --map      — path to rule_repair_map.json (default: tools/audit/rule_repair_map.json)

The script reads:
  $JOB/audit/failures.json         — pre-repair rule failures (from parse_verapdf_summary.py)
  $JOB/audit/repair_plan.json      — the repair plan that was executed (from lookup_repair_plan.py)
  $JOB/STATUS.json                 — final job outcome and any deviation log

Exit codes:
  0  map updated successfully
  1  nothing to update (no failures.json or job did not PASS)
  2  usage error
"""
import sys, json, argparse
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_MAP = Path('tools/audit/rule_repair_map.json')

parser = argparse.ArgumentParser()
parser.add_argument('job_dir', help='Path to the job directory')
parser.add_argument('--map', default=str(DEFAULT_MAP),
                    help=f'Path to rule_repair_map.json (default: {DEFAULT_MAP})')
parser.add_argument('--dry-run', action='store_true',
                    help='Print proposed changes without writing to map')
args = parser.parse_args()

job_dir = Path(args.job_dir)
map_path = Path(args.map)

# ── Load inputs ───────────────────────────────────────────────────────────────

def load_json(path, label):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f'WARNING: could not read {label} at {path}: {e}', file=sys.stderr)
        return None

failures_data    = load_json(job_dir / 'audit' / 'failures.json',    'failures.json')
repair_plan_data = load_json(job_dir / 'audit' / 'repair_plan.json', 'repair_plan.json')
status_data      = load_json(job_dir / 'STATUS.json',                'STATUS.json')
map_data         = load_json(map_path,                                'rule_repair_map.json')

if not failures_data:
    print(json.dumps({
        'result': 'SKIPPED',
        'reason': 'No failures.json found — pre-repair parse may not have run'
    }, indent=2))
    sys.exit(1)

if not map_data:
    print(json.dumps({'result': 'ERROR', 'reason': f'Cannot read map at {map_path}'}))
    sys.exit(2)

overall = status_data.get('overall_result', 'UNKNOWN') if status_data else 'UNKNOWN'
job_id  = job_dir.name
today   = datetime.now(timezone.utc).date().isoformat()

# ── Build lookup: rule_id → repair_step from the plan ─────────────────────────

plan_by_rule = {}
if repair_plan_data:
    for step in repair_plan_data.get('repair_steps', []):
        for rule_id in step.get('rules_addressed', []):
            plan_by_rule[rule_id] = step

# ── Build deviation log from STATUS.json ─────────────────────────────────────

deviations = {}
if status_data:
    for entry in status_data.get('deviation_log', []):
        rule_id = entry.get('rule_id')
        if rule_id:
            deviations[rule_id] = entry

# ── Process each failing rule ─────────────────────────────────────────────────

rule_map  = map_data.get('rules', {})
changes   = []
additions = []

for failure in failures_data.get('failures_by_rule', []):
    rule_id = failure.get('rule_id', '')
    if rule_id == 'unknown':
        continue  # Can't learn from unidentified rules

    plan_step  = plan_by_rule.get(rule_id)
    deviation  = deviations.get(rule_id)
    existing   = rule_map.get(rule_id)

    # Determine what script was actually used and whether it worked
    if deviation:
        # Agent deviated from plan — use deviation log
        script_used = deviation.get('script_used')
        outcome     = deviation.get('outcome', 'UNKNOWN')
    elif plan_step:
        # Agent followed plan — assume PASS if job overall passed
        script_used = plan_step.get('repair_script')
        outcome     = 'PASS' if overall == 'PASS' else 'UNCERTAIN'
    else:
        # Rule wasn't in plan — agent reasoned from scratch
        script_used = None
        outcome     = 'UNKNOWN'

    if existing:
        # ── Known rule — update only ──────────────────────────────────────
        existing_script = existing.get('repair_script')

        if outcome == 'PASS' and script_used == existing_script:
            # Expected outcome — increment confirmed_jobs, update date
            old_count = existing.get('confirmed_jobs', 0)
            existing['confirmed_jobs'] = old_count + 1
            existing['last_confirmed'] = today
            # Promote confidence if enough jobs
            if existing['confirmed_jobs'] >= 3 and existing.get('confidence') == 'EXPECTED':
                existing['confidence'] = 'CONFIRMED'
                changes.append(f'{rule_id}: promoted to CONFIRMED ({existing["confirmed_jobs"]} jobs)')
            else:
                changes.append(f'{rule_id}: confirmed_jobs → {existing["confirmed_jobs"]}')

        elif outcome == 'PASS' and script_used and script_used != existing_script:
            # Different script worked — add edge case, don't overwrite primary
            edge_cases = existing.setdefault('edge_cases', [])
            edge_cases.append({
                'job_id':      job_id,
                'date':        today,
                'script_used': script_used,
                'note':        deviation.get('note', '') if deviation else ''
            })
            changes.append(f'{rule_id}: added edge_case (different script worked: {script_used})')

        elif outcome in ('FAIL', 'UNCERTAIN') and script_used:
            # Script failed — downgrade confidence, log failure mode
            if existing.get('confidence') == 'CONFIRMED':
                existing['confidence'] = 'EXPECTED'
                changes.append(f'{rule_id}: downgraded to EXPECTED (script failed on job {job_id})')
            failure_modes = existing.setdefault('failure_modes', [])
            failure_modes.append({
                'job_id': job_id,
                'date':   today,
                'note':   deviation.get('note', '') if deviation else 'Script did not resolve rule'
            })

    else:
        # ── New rule — add to map ─────────────────────────────────────────
        if script_used and outcome == 'PASS':
            new_entry = {
                'clause':        failure.get('clause', ''),
                'description':   failure.get('description', ''),
                'repair_script': script_used,
                'repair_order':  plan_step.get('repair_order') if plan_step else None,
                'run_last':      plan_step.get('run_last', False) if plan_step else False,
                'args_pattern':  plan_step.get('args_pattern', '<input.pdf> <output.pdf>') if plan_step else '',
                'confidence':    'EXPECTED',  # Needs more jobs before CONFIRMED
                'confirmed_jobs': 1,
                'last_confirmed': today,
                'notes':         f'First seen on job {job_id}. Promote to CONFIRMED after 2 more successful jobs.'
            }
            rule_map[rule_id] = new_entry
            additions.append(f'{rule_id}: added as EXPECTED (first seen, script: {script_used})')
        else:
            new_entry = {
                'clause':        failure.get('clause', ''),
                'description':   failure.get('description', ''),
                'repair_script': None,
                'repair_order':  None,
                'run_last':      False,
                'args_pattern':  None,
                'confidence':    'MANUAL',
                'confirmed_jobs': 0,
                'notes':         f'First seen on job {job_id}. Could not be auto-repaired — manual review required.'
            }
            rule_map[rule_id] = new_entry
            additions.append(f'{rule_id}: added as MANUAL (could not auto-repair)')

# ── Update map metadata ───────────────────────────────────────────────────────

meta = map_data.get('_meta', {})
meta['confirmed_jobs'] = meta.get('confirmed_jobs', 0) + (1 if overall == 'PASS' else 0)
meta['last_updated']   = today
map_data['_meta']      = meta
map_data['rules']      = rule_map

# ── Write or dry-run ─────────────────────────────────────────────────────────

summary = {
    'result':       'DRY_RUN' if args.dry_run else 'UPDATED',
    'job_id':       job_id,
    'job_outcome':  overall,
    'rules_updated': changes,
    'rules_added':   additions,
    'map_path':      str(map_path)
}

if not changes and not additions:
    summary['result'] = 'NO_CHANGES'
    summary['note']   = 'All rules already in map with expected outcomes — nothing to update.'
    print(json.dumps(summary, indent=2))
    sys.exit(0)

if args.dry_run:
    print(json.dumps(summary, indent=2))
    sys.exit(0)

map_path.write_text(json.dumps(map_data, indent=2))
print(json.dumps(summary, indent=2))
sys.exit(0)
