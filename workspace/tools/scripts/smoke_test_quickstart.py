#!/usr/bin/env python3
import sys, json
from pathlib import Path
root=Path(sys.argv[1]) if len(sys.argv)>1 else Path.cwd()
checks=[]
def chk(name, cond): checks.append({'check':name,'pass':bool(cond)})
chk('rules exist', (root/'montefiore_pdfua_quickstart_v6_01_rules').exists())
chk('scripts exist', (root/'montefiore_pdfua_quickstart_v6_02_scripts').exists())
chk('skills exist', (root/'montefiore_pdfua_quickstart_v6_03_skills').exists())
chk('assets exist', (root/'montefiore_pdfua_quickstart_v6_05_assets').exists())
chk('no qpdf binary obvious', not any(p.name=='qpdf' for p in root.rglob('*') if p.is_file()))
chk('no verapdf binary obvious', not any(p.name=='verapdf' for p in root.rglob('*') if p.is_file()))
profile=list(root.rglob('PDF_UA/WCAG-2-2-Machine.xml'))
chk('pinned WCAG profile exists', len(profile)>0)
print(json.dumps({'result':'PASS' if all(c['pass'] for c in checks) else 'FAIL','checks':checks}, indent=2))
sys.exit(0 if all(c['pass'] for c in checks) else 1)
