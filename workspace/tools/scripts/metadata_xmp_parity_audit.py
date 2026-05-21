#!/usr/bin/env python3
import sys, json, re
from pathlib import Path
try:
    import fitz
except Exception as e:
    print(json.dumps({'result':'ERROR','error':f'PyMuPDF unavailable: {e}'})); sys.exit(2)
if len(sys.argv)<2:
    print('usage: metadata_xmp_parity_audit.py <pdf>', file=sys.stderr); sys.exit(2)
pdf=sys.argv[1]
doc=fitz.open(pdf)
meta=doc.metadata or {}
xmp=doc.get_xml_metadata() or ''
required='Montefiore Einstein'
checks=[]
for k in ['author','creator','producer']:
    checks.append({'field':k,'info_value':meta.get(k,''),'pass':meta.get(k,'')==required})
for label,pattern in [('xmp_creator_tool',r'<xmp:CreatorTool>(.*?)</xmp:CreatorTool>'),('xmp_producer',r'<pdf:Producer>(.*?)</pdf:Producer>')]:
    m=re.search(pattern,xmp,re.S)
    val=m.group(1).strip() if m else ''
    checks.append({'field':label,'xmp_value':val,'pass':val==required})
checks.append({'field':'pdfuaid_part','pass':'pdfuaid:part' in xmp and '>1<' in xmp})
checks.append({'field':'catalog_lang','pass': bool(doc.pdf_catalog())})
result='PASS' if all(c['pass'] for c in checks) else 'FAIL'
print(json.dumps({'pdf':pdf,'result':result,'info':meta,'checks':checks}, indent=2))
sys.exit(0 if result=='PASS' else 1)
