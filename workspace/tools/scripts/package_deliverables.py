#!/usr/bin/env python3
import sys, zipfile
from pathlib import Path
if len(sys.argv)<3:
    print('usage: package_deliverables.py <folder> <zip>', file=sys.stderr); sys.exit(2)
root=Path(sys.argv[1]); zp=Path(sys.argv[2])
with zipfile.ZipFile(zp,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file(): z.write(p, p.relative_to(root.parent))
print(zp)
