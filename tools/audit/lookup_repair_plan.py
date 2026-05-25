#!/usr/bin/env python3
"""
lookup_repair_plan.py
Takes the JSON output of parse_verapdf_summary.py and produces an ordered
repair plan by looking up each failing rule ID in rule_repair_map.json.

The agent runs this immediately after parse_verapdf_summary.py. The output
tells it exactly which scripts to run and in what order, without reasoning
from scratch.

Usage:
  lookup_repair_plan.py <parse_verapdf_summary_output.json> [--map <rule_repair_map.json>]

  --map defaults to /app/tools/audit/rule_repair_map.json

Output JSON:
  {
    "result": "PLAN_READY" | "ALL_MANUAL" | "NO_FAILURES",
    "repair_steps": [          ← ordered list, execute in sequence
      {
        "step": 1,
        "repair_script": "tools/repair/fix_pdfua_identifier.py",
        "repair_order": 1,
        "run_last": false,
        "args_pattern": "<input.pdf> <output.pdf>",
        "rules_addressed": ["PDF/UA-1/6.2"],
        "confidence": "CONFIRMED"
      },
      ...
    ],
    "manual_escalations": [    ← rules that cannot be auto-repaired
      {
        "rule_id": "PDF/UA-1/7.2-1",
        "description": "...",
        "notes": "..."
      }
    ],
    "unknown_rules": [         ← rule IDs not in the map (agent must reason)
      {
        "rule_id": "...",
        "description": "...",
        "failures": N
      }
    ]
  }

Exit codes:
  0  plan produced (may include manual escalations or unknown rules)
  1  all failures require manual escalation
  2  usage error
"""
import sys, json, argparse
from pathlib import Path
from collections import defaultdict

DEFAULT_MAP = Path('/app/tools/audit/rule_repair_map.json')

parser = argparse.ArgumentParser()
parser.add_argument('summary_json',
                    help='Output JSON from parse_verapdf_summary.py')
parser.add_argument('--map', default=str(DEFAULT_MAP),
                    help=f'Path to rule_repair_map.json (default: {DEFAULT_MAP})')
args = parser.parse_args()

# ── Load inputs ───────────────────────────────────────────────────────────────

try:
    summary = json.loads(Path(args.summary_json).read_text())
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'Cannot read summary: {e}'}))
    sys.exit(2)

try:
    rule_map_data = json.loads(Path(args.map).read_text())
    rule_map = rule_map_data.get('rules', {})
except Exception as e:
    print(json.dumps({'result': 'ERROR', 'error': f'Cannot read rule map: {e}'}))
    sys.exit(2)

# ── Early exit if no failures ─────────────────────────────────────────────────

failures = summary.get('failures_by_rule', [])
if not failures:
    print(json.dumps({
        'result': 'NO_FAILURES',
        'repair_steps': [],
        'manual_escalations': [],
        'unknown_rules': [],
        'note': 'veraPDF reported no failures — no repairs needed.'
    }, indent=2))
    sys.exit(0)

# ── Match failures to map entries ─────────────────────────────────────────────

# Group by repair_script to avoid duplicate steps for the same script
# (multiple rules may map to the same fix)
script_to_rules  = defaultdict(list)
manual_escalations = []
unknown_rules    = []

for failure in failures:
    rule_id  = failure.get('rule_id', '')
    desc     = failure.get('description', '')
    count    = failure.get('failures', 0)

    # Try exact match first, then prefix match on clause
    entry = rule_map.get(rule_id)
    if not entry:
        # Try matching just the clause portion (handles minor spec string variation)
        for map_key, map_entry in rule_map.items():
            if rule_id.endswith(map_entry.get('clause', '__no_match__')):
                entry = map_entry
                break

    if entry is None:
        unknown_rules.append({
            'rule_id':     rule_id,
            'description': desc,
            'failures':    count
        })
        continue

    if entry.get('repair_script') is None:
        manual_escalations.append({
            'rule_id':     rule_id,
            'description': desc,
            'failures':    count,
            'notes':       entry.get('notes', '')
        })
        continue

    script = entry['repair_script']
    script_to_rules[script].append({
        'rule_id':      rule_id,
        'description':  desc,
        'failures':     count,
        'repair_order': entry['repair_order'],
        'run_last':     entry.get('run_last', False),
        'args_pattern': entry.get('args_pattern', ''),
        'confidence':   entry.get('confidence', 'EXPECTED'),
        'notes':        entry.get('notes', '')
    })

# ── Build ordered repair steps ────────────────────────────────────────────────

# For each script, take the highest repair_order among its matched rules
# (they should all be the same, but be safe)
repair_steps_raw = []
for script, rule_entries in script_to_rules.items():
    order    = max(r['repair_order'] for r in rule_entries)
    run_last = any(r['run_last'] for r in rule_entries)
    conf     = rule_entries[0]['confidence']  # all entries for same script share confidence
    pattern  = rule_entries[0]['args_pattern']
    notes    = rule_entries[0]['notes']
    repair_steps_raw.append({
        'repair_script':   script,
        'repair_order':    order,
        'run_last':        run_last,
        'args_pattern':    pattern,
        'rules_addressed': [r['rule_id'] for r in rule_entries],
        'confidence':      conf,
        'notes':           notes
    })

# Sort: run_last=True always goes to end, otherwise ascending repair_order
repair_steps_raw.sort(key=lambda s: (s['run_last'], s['repair_order']))

repair_steps = []
for i, step in enumerate(repair_steps_raw, start=1):
    repair_steps.append({
        'step':            i,
        'repair_script':   step['repair_script'],
        'repair_order':    step['repair_order'],
        'run_last':        step['run_last'],
        'args_pattern':    step['args_pattern'],
        'rules_addressed': step['rules_addressed'],
        'confidence':      step['confidence'],
        'notes':           step['notes']
    })

# ── Result ────────────────────────────────────────────────────────────────────

result = 'PLAN_READY'
if not repair_steps and manual_escalations:
    result = 'ALL_MANUAL'

output = {
    'result':               result,
    'failures_total':       summary.get('total_failures', 0),
    'rules_failing':        len(failures),
    'repair_steps':         repair_steps,
    'manual_escalations':   manual_escalations,
    'unknown_rules':        unknown_rules,
    'agent_instruction': (
        'Execute repair_steps in the order listed (step 1 first). '
        'Any step with run_last=true must execute after all others — '
        'no PDF save operations may occur after it. '
        'For manual_escalations: set job result to REVIEW_REQUIRED or FAIL '
        'and document in STATUS.json. '
        'For unknown_rules: reason from AGENTS.md and document the outcome '
        'in STATUS.json so rule_repair_map.json can be updated.'
    )
}

print(json.dumps(output, indent=2))
sys.exit(0 if result == 'PLAN_READY' else 1)
