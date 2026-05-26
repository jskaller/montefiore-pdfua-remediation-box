#!/usr/bin/env python3
"""
parse_verapdf_summary.py
Parses one or more veraPDF XML output files and produces a concise JSON
summary of failures grouped by rule ID, suitable for driving the repair
pipeline via lookup_repair_plan.py.

Supports both veraPDF schemas:
  Arlington:  report > jobs > job > arlingtonReport  > details > rule[@deviations]
  Greenfield: report > jobs > job > validationReport > details > rule[@failedChecks]

Both schemas share the same inner <rule clause="..." status="failed"> structure.
Greenfield is the standard veraPDF distribution and implements the full
Matterhorn Protocol. Arlington only validates the PDF object model.

Rule IDs are normalized to the form used in rule_repair_map.json:
  "PDF/UA-1/{clause}"   for ISO 14289-1:2014 rules
  "WCAG-2-2/{clause}"   for WCAG rules
  "{spec}/{clause}"     for anything else

Usage:
  parse_verapdf_summary.py <verapdf_output.xml> [<verapdf_output2.xml> ...]

Output JSON:
  result               PASS | FAIL
  files_parsed         list of input files successfully parsed
  parse_errors         list of {file, error} for files that failed
  total_failures       total failed checks across all rules
  unique_rules_failing number of distinct normalized rule IDs with failures
  failures_by_rule     list sorted by failure count descending

Exit codes: 0=PASS, 1=FAIL, 2=usage/parse error
"""
import sys, json, re
from pathlib import Path

try:
    import xml.etree.ElementTree as ET
except ImportError as e:
    print(json.dumps({'result': 'ERROR', 'error': str(e)}))
    sys.exit(2)

if len(sys.argv) < 2:
    print('usage: parse_verapdf_summary.py <xml> [<xml2> ...]', file=sys.stderr)
    sys.exit(2)

# ── Spec string normalisation ─────────────────────────────────────────────────
# Maps veraPDF specification strings to rule_repair_map.json key prefixes.

SPEC_NORMALISE = {
    'ISO 14289-1:2014':      'PDF/UA-1',
    'ISO 14289-1':           'PDF/UA-1',
    'PDF/UA-1':              'PDF/UA-1',
    'WCAG2':                 'WCAG-2-2-Machine',
    'WCAG 2.2':              'WCAG-2-2-Machine',
    'WCAG-2-2-Machine':      'WCAG-2-2-Machine',
    'ISO 32000-1':           'ISO-32000-1',
    'ISO 32000-1:2008':      'ISO-32000-1',
}

def normalise_spec(spec):
    return SPEC_NORMALISE.get(spec, spec)

def normalise_rule_id(spec, clause):
    norm_spec = normalise_spec(spec)
    return f'{norm_spec}/{clause}' if norm_spec and clause else clause or spec or 'unknown'

def strip_ns(tag):
    return re.sub(r'\{[^}]+\}', '', tag)

def iter_tag(root, tag):
    for elem in root.iter():
        if strip_ns(elem.tag) == tag:
            yield elem

# ── Parse ─────────────────────────────────────────────────────────────────────

all_failures = {}
files_parsed = []
parse_errors = []

def record(rule_id, clause, spec, description, count, source):
    if rule_id not in all_failures:
        all_failures[rule_id] = {
            'rule_id':     rule_id,
            'clause':      clause,
            'spec_raw':    spec,
            'description': description,
            'failures':    0,
            'sources':     []
        }
    all_failures[rule_id]['failures'] += count
    if source not in all_failures[rule_id]['sources']:
        all_failures[rule_id]['sources'].append(source)

for xml_path in sys.argv[1:]:
    if not Path(xml_path).exists():
        parse_errors.append({'file': xml_path, 'error': 'file not found'})
        continue

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        found = False

        # ── Primary: Greenfield schema (standard veraPDF) ─────────────────
        # validationReport > details > rule[@failedChecks > 0]
        for report in iter_tag(root, 'validationReport'):
            is_compliant = report.get('isCompliant', 'true').lower()
            if is_compliant == 'true':
                found = True
                continue
            for details in iter_tag(report, 'details'):
                for rule in iter_tag(details, 'rule'):
                    failed = int(rule.get('failedChecks', 0))
                    if failed > 0:
                        spec   = rule.get('specification', '')
                        clause = rule.get('clause', '')
                        desc   = rule.get('description', '')
                        rule_id = normalise_rule_id(spec, clause)
                        record(rule_id, clause, spec, desc, failed, xml_path)
                        found = True

        # ── Secondary: Arlington schema ───────────────────────────────────
        # arlingtonReport > details > rule[@deviations > 0]
        for report in iter_tag(root, 'arlingtonReport'):
            is_compliant = report.get('isCompliant', 'true').lower()
            if is_compliant == 'true':
                # Document is compliant — no failures to parse
                found = True
                continue
            for details in iter_tag(report, 'details'):
                for rule in iter_tag(details, 'rule'):
                    deviations = int(rule.get('deviations', 0))
                    if deviations > 0:
                        spec   = rule.get('specification', '')
                        clause = rule.get('clause', '')
                        desc   = rule.get('description', '')
                        rule_id = normalise_rule_id(spec, clause)
                        record(rule_id, clause, spec, desc, deviations, xml_path)
                        found = True

        # ── Fallback: ruleSummary schema (older veraPDF) ──────────────────
        for elem in iter_tag(root, 'ruleSummary'):
            failed = int(elem.get('failedChecks', 0))
            if failed > 0:
                spec   = elem.get('specification', '')
                clause = elem.get('clause', '')
                rule_id = normalise_rule_id(spec, clause)
                record(rule_id, clause, spec,
                       elem.get('description', ''), failed, xml_path)
                found = True

        # ── Fallback: flat testAssertion elements ─────────────────────────
        for elem in iter_tag(root, 'testAssertion'):
            if elem.get('status', '').upper() == 'FAILED':
                raw = elem.get('ruleId', 'unknown')
                record(raw, '', '', elem.get('message', ''), 1, xml_path)
                found = True

        files_parsed.append(xml_path)

        if not found and Path(xml_path).stat().st_size > 100:
            parse_errors.append({
                'file':  xml_path,
                'error': 'no recognisable veraPDF rule elements found — '
                         'file may be compliant or schema unrecognised'
            })

    except ET.ParseError as e:
        parse_errors.append({'file': xml_path, 'error': f'XML parse error: {e}'})
    except Exception as e:
        parse_errors.append({'file': xml_path, 'error': str(e)})

# ── Output ────────────────────────────────────────────────────────────────────

failures_list  = sorted(all_failures.values(),
                        key=lambda x: x['failures'], reverse=True)
total_failures = sum(f['failures'] for f in failures_list)

print(json.dumps({
    'result':               'PASS' if total_failures == 0 else 'FAIL',
    'files_parsed':         files_parsed,
    'parse_errors':         parse_errors,
    'total_failures':       total_failures,
    'unique_rules_failing': len(failures_list),
    'failures_by_rule':     failures_list
}, indent=2))

sys.exit(0 if total_failures == 0 else 1)
