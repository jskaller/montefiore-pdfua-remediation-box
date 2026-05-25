#!/usr/bin/env python3
"""
parse_verapdf_summary.py
Parses one or more veraPDF XML output files and produces a concise JSON summary
of failures grouped by rule ID, suitable for driving the repair pipeline.

Usage: parse_verapdf_summary.py <verapdf_output.xml> [<verapdf_output2.xml> ...]
"""
import sys, json
from pathlib import Path
try:
    import xml.etree.ElementTree as ET
except ImportError as e:
    print(json.dumps({'result': 'ERROR', 'error': str(e)})); sys.exit(2)

if len(sys.argv) < 2:
    print('usage: parse_verapdf_summary.py <xml> [<xml2> ...]', file=sys.stderr)
    sys.exit(2)

all_failures = {}
files_parsed = []
parse_errors = []

for xml_path in sys.argv[1:]:
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        files_parsed.append(xml_path)

        # veraPDF XML structure: batchSummary > job > validationReport > ruleSummaries > ruleSummary
        # or: report > jobs > job > validationResult > details > rule
        ns = {'vera': 'http://www.verapdf.org/ValidationProfile'}

        # Try both schema variants
        for rule_elem in root.iter('ruleSummary'):
            rule_id  = rule_elem.get('specification', '') + '/' + rule_elem.get('clause', '')
            failures = int(rule_elem.get('failedChecks', 0))
            if failures > 0:
                desc = rule_elem.get('description', '')
                if rule_id not in all_failures:
                    all_failures[rule_id] = {
                        'rule_id':     rule_id,
                        'clause':      rule_elem.get('clause', ''),
                        'description': desc,
                        'failures':    0,
                        'sources':     []
                    }
                all_failures[rule_id]['failures'] += failures
                all_failures[rule_id]['sources'].append(xml_path)

        # Also handle flat <check> elements with status=failed
        for check in root.iter('check'):
            if check.get('status') == 'failed':
                rule_id = check.get('ruleId', 'unknown')
                if rule_id not in all_failures:
                    all_failures[rule_id] = {
                        'rule_id':     rule_id,
                        'clause':      '',
                        'description': check.get('message', ''),
                        'failures':    0,
                        'sources':     []
                    }
                all_failures[rule_id]['failures'] += 1
                all_failures[rule_id]['sources'].append(xml_path)

    except Exception as e:
        parse_errors.append({'file': xml_path, 'error': str(e)})

failures_list = sorted(all_failures.values(), key=lambda x: x['failures'], reverse=True)
total_failures = sum(f['failures'] for f in failures_list)

print(json.dumps({
    'result':        'PASS' if total_failures == 0 else 'FAIL',
    'files_parsed':  files_parsed,
    'parse_errors':  parse_errors,
    'total_failures': total_failures,
    'unique_rules_failing': len(failures_list),
    'failures_by_rule': failures_list
}, indent=2))
sys.exit(0 if total_failures == 0 else 1)
