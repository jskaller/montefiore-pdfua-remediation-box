#!/usr/bin/env python3
import sys, json
try:
    import fitz
except Exception as e:
    print(json.dumps({'result':'ERROR','error':str(e)})); sys.exit(2)
if len(sys.argv)<3:
    print('usage: preservation_audit.py <source.pdf> <output.pdf>', file=sys.stderr); sys.exit(2)
def words(p):
    doc=fitz.open(p); return [w[4] for pg in doc for w in pg.get_text('words')]
a=words(sys.argv[1]); b=words(sys.argv[2])
print(json.dumps({'source_words':len(a),'output_words':len(b),'exact_order_preserved':a==b,'result':'PASS' if a==b else 'REVIEW'}, indent=2))
