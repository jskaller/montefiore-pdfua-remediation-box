#!/usr/bin/env python3
from pathlib import Path
import sys
if len(sys.argv)<2:
    print('usage: package_scaffold.py <out-dir>', file=sys.stderr); sys.exit(2)
root=Path(sys.argv[1])
for d in ['output','reports','logs','visual','rules','checksums']:
    (root/d).mkdir(parents=True, exist_ok=True)
print(root)
