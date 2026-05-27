#!/usr/bin/env python3
"""
lookup_repair_plan.py
Takes the JSON output of parse_verapdf_summary.py and produces an ordered
repair plan by looking up each failing rule ID in rule_repair_map.json.

Schema v2.0.0: rule entries use a strategies array with pass_rate, pass_count,
fail_count, doc_type_stats, and known_failure_modes. Strategies are sorted by
pass_rate desc / pass_count desc / doc_tag_overlap desc before selection.
Rules with manual:true and empty strategies emit OPENCLAW_REQUIRED signals.

Usage:
  lookup_repair_plan.py <parse_verapdf_summary_output.json>
    [--map <rule_repair_map.json>]
    [--taxonomy <doc_taxonomy.json>]
    [--doc-tags tag1,tag2,...]

  --map defaults to /app/tools/audit/rule_repair_map.json
  --taxonomy defaults to /app/tools/audit/doc_taxonomy.json
  --doc-tags comma-separated list of tags assigned to the current document

Output JSON:
  {
    "result": "PLAN_READY" | "ALL_MANUAL" | "NO_FAILURES",
    "repair_steps": [
      {
        "step": 1,
        "repair_script": "tools/repair/fix_pdfua_identifier.py",
        "strategy": "set_pdfua_identifier",
        "repair_order": 1,
        "run_last": false,
        "args_pattern": "<input.pdf> <output.pdf>",
        "rules_addressed": ["PDF/UA-1/6.2"],
        "confidence": "CONFIRMED",
        "pass_rate": 1.0,
        "pass_count": 3,
        "fail_count": 0
      },
      ...
    ],
    "openclaw_required": [     <- rules needing agent script generation
      {
        "rule_id": "PDF/UA-1/7.18.4",
        "description": "...",
        "failures": N,
        "reason": "manual_no_strategies" | "all_strategies_exhausted" | "unknown_rule",
        "strategies_attempted": [...]
      }
    ],
    "unknown_rules": [         <- rule IDs not in the map at all
      {
        "rule_id": "...",
        "description": "...",
        "failures": N
      }
    ]
  }

Exit codes:
  0  plan produced successfully (may include openclaw_required or unknown rules)
  2  usage error (missing input, unreadable map)
"""
import sys, json, argparse
from pathlib import Path
from collections import defaultdict

DEFAULT_MAP      = Path('/app/tools/audit/rule_repair_map.json')
DEFAULT_TAXONOMY = Path('/app/tools/audit/doc_taxonomy.json')

parser = argparse.ArgumentParser()
parser.add_argument('summary_json',
                    help='Output JSON from parse_verapdf_summary.py')
parser.add_argument('--map', default=str(DEFAULT_MAP),
                    help=f'Path to rule_repair_map.json (default: {DEFAULT_MAP})')
parser.add_argument('--taxonomy', default=str(DEFAULT_TAXONOMY),
                    help=f'Path to doc_taxonomy.json (default: {DEFAULT_TAXONOMY})')
parser.add_argument('--doc-tags', default='',
                    help='Comma-separated document tags for strategy ordering')
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

# Taxonomy is optional — missing it degrades doc_tag_overlap scoring to 0
taxonomy_tags = set()
try:
    taxonomy_data = json.loads(Path(args.taxonomy).read_text())
    taxonomy_tags = {t['tag'] for t in taxonomy_data.get('tags', [])}
except Exception:
    pass

# Parse doc tags
doc_tags = set()
if args.doc_tags:
    doc_tags = {t.strip() for t in args.doc_tags.split(',') if t.strip()}

# ── Early exit if no failures ─────────────────────────────────────────────────

failures = summary.get('failures_by_rule', [])
if not failures:
    print(json.dumps({
        'result': 'NO_FAILURES',
        'repair_steps': [],
        'openclaw_required': [],
        'unknown_rules': [],
        'note': 'veraPDF reported no failures — no repairs needed.'
    }, indent=2))
    sys.exit(0)

# ── Strategy scoring ──────────────────────────────────────────────────────────

def doc_tag_overlap_score(strategy: dict) -> int:
    """Count how many of the strategy's confirmed doc tags match the current document."""
    if not doc_tags:
        return 0
    confirmed_tags = {
        stat['tag']
        for stat in strategy.get('doc_type_stats', [])
        if stat.get('pass_count', 0) > 0
    }
    return len(doc_tags & confirmed_tags)


def sort_strategies(strategies: list) -> list:
    """Sort strategies by pass_rate desc, pass_count desc, doc_tag_overlap desc."""
    return sorted(
        strategies,
        key=lambda s: (
            s.get('pass_rate', 0.0),
            s.get('pass_count', 0),
            doc_tag_overlap_score(s)
        ),
        reverse=True
    )


# ── Match failures to map entries ─────────────────────────────────────────────

# Group by repair_script to avoid duplicate steps for the same script
script_to_rules  = defaultdict(list)
openclaw_required = []
unknown_rules    = []

for failure in failures:
    rule_id  = failure.get('rule_id', '')
    desc     = failure.get('description', '')
    count    = failure.get('failures', 0)

    # Exact match only. veraPDF rule IDs are normalised by
    # parse_verapdf_summary.py to match rule_repair_map.json keys exactly.
    # If a rule isn't present by exact key, it's genuinely unknown.
    entry = rule_map.get(rule_id)

    # Unknown rule — not in map at all
    if entry is None:
        # Unknown rules appear in TWO output fields:
        #   - unknown_rules: documentation for the plan summary
        #   - openclaw_required: action list the orchestrator emits signals from
        # This is intentional. The orchestrator iterates openclaw_required only;
        # unknown_rules exists so consumers can distinguish "rule not in map" from
        # "rule in map but manual" without parsing the reason field.
        unknown_rules.append({
            'rule_id':     rule_id,
            'description': desc,
            'failures':    count,
            'reason':      'unknown_rule'
        })
        openclaw_required.append({
            'rule_id':              rule_id,
            'description':          desc,
            'failures':             count,
            'reason':               'unknown_rule',
            'strategies_attempted': []
        })
        continue

    # Manual rule with no strategies — emit OPENCLAW_REQUIRED
    if entry.get('manual', False) and not entry.get('strategies'):
        openclaw_required.append({
            'rule_id':              rule_id,
            'description':          entry.get('description', desc),
            'failures':             count,
            'reason':               'manual_no_strategies',
            'strategies_attempted': []
        })
        continue

    # Rule has strategies — sort and select best available
    strategies = sort_strategies(entry.get('strategies', []))

    if not strategies:
        # strategies array exists but is empty and manual:false — treat as openclaw
        openclaw_required.append({
            'rule_id':              rule_id,
            'description':          entry.get('description', desc),
            'failures':             count,
            'reason':               'all_strategies_exhausted',
            'strategies_attempted': []
        })
        continue

    # Use the highest-ranked strategy for the plan
    # (orchestrator will fall through to lower strategies on failure)
    best = strategies[0]
    script = best.get('repair_script')

    if not script:
        openclaw_required.append({
            'rule_id':              rule_id,
            'description':          entry.get('description', desc),
            'failures':             count,
            'reason':               'manual_no_strategies',
            'strategies_attempted': []
        })
        continue

    script_to_rules[script].append({
        'rule_id':           rule_id,
        'description':       entry.get('description', desc),
        'failures':          count,
        'repair_order':      best.get('repair_order', 99),
        'run_last':          best.get('run_last', False),
        'args_pattern':      best.get('args_pattern', ''),
        'confidence':        best.get('confidence', 'EXPECTED'),
        'strategy':          best.get('strategy', ''),
        'pass_rate':         best.get('pass_rate', 0.0),
        'pass_count':        best.get('pass_count', 0),
        'fail_count':        best.get('fail_count', 0),
        'all_strategies':    strategies,  # full list for orchestrator fallback
    })

# ── Build ordered repair steps ────────────────────────────────────────────────

repair_steps_raw = []
for script, rule_entries in script_to_rules.items():
    order    = max(r['repair_order'] for r in rule_entries)
    run_last = any(r['run_last'] for r in rule_entries)
    # Use the entry with highest pass_rate for step-level metadata
    best_entry = max(rule_entries, key=lambda r: (r['pass_rate'], r['pass_count']))
    repair_steps_raw.append({
        'repair_script':   script,
        'repair_order':    order,
        'run_last':        run_last,
        'args_pattern':    best_entry['args_pattern'],
        'rules_addressed': [r['rule_id'] for r in rule_entries],
        'confidence':      best_entry['confidence'],
        'strategy':        best_entry['strategy'],
        'pass_rate':       best_entry['pass_rate'],
        'pass_count':      best_entry['pass_count'],
        'fail_count':      best_entry['fail_count'],
        'all_strategies':  best_entry['all_strategies'],
    })

# Sort: run_last=True always goes to end, otherwise ascending repair_order
repair_steps_raw.sort(key=lambda s: (s['run_last'], s['repair_order']))

repair_steps = []
for i, step in enumerate(repair_steps_raw, start=1):
    repair_steps.append({
        'step':            i,
        'repair_script':   step['repair_script'],
        'strategy':        step['strategy'],
        'repair_order':    step['repair_order'],
        'run_last':        step['run_last'],
        'args_pattern':    step['args_pattern'],
        'rules_addressed': step['rules_addressed'],
        'confidence':      step['confidence'],
        'pass_rate':       step['pass_rate'],
        'pass_count':      step['pass_count'],
        'fail_count':      step['fail_count'],
        'all_strategies':  step['all_strategies'],
    })

# ── Result ────────────────────────────────────────────────────────────────────

result = 'PLAN_READY'
if not repair_steps and openclaw_required:
    result = 'ALL_MANUAL'

output = {
    'result':             result,
    'failures_total':     summary.get('total_failures', 0),
    'rules_failing':      len(failures),
    'doc_tags_applied':   sorted(doc_tags),
    'repair_steps':       repair_steps,
    'openclaw_required':  openclaw_required,
    'unknown_rules':      unknown_rules,
    'agent_instruction': (
        'Execute repair_steps in the order listed (step 1 first). '
        'Any step with run_last=true must execute after all others — '
        'no PDF save operations may occur after it. '
        'For openclaw_required entries: emit OPENCLAW_REQUIRED signal with '
        'full rule context so the agent can write or locate a repair script. '
        'For unknown_rules: the rule is not in the map — emit OPENCLAW_REQUIRED '
        'with reason=unknown_rule so the agent researches the rule before writing. '
        'After each repair step, if the rule still fails, fall through to the '
        'next strategy in all_strategies before emitting OPENCLAW_REQUIRED.'
    )
}

print(json.dumps(output, indent=2))
# Exit 0 on any successful run — the script's job is to produce the plan,
# not to adjudicate it. The orchestrator decides what to do based on result.
sys.exit(0)
