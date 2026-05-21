#!/usr/bin/env python3
import sys, hashlib, pathlib
for arg in sys.argv[1:]:
    p=pathlib.Path(arg)
    h=hashlib.sha256(p.read_bytes()).hexdigest()
    print(f'{h}  {p}')
