#!/usr/bin/env python3
import sys, json, xml.etree.ElementTree as ET
for f in sys.argv[1:]:
    root=ET.parse(f).getroot()
    text=''.join(root.itertext())
    # lightweight fallback parser for CI smoke use
    out={'file':f,'contains_passed': 'passed' in text.lower(), 'contains_failed': 'failed' in text.lower()}
    print(json.dumps(out))
