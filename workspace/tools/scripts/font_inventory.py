#!/usr/bin/env python3
import json, sys
from pathlib import Path
roots=[Path('/usr/share/fonts'), Path('/usr/local/share/fonts'), Path.home()/'.fonts', Path.home()/'.local/share/fonts']
fonts=[]
for r in roots:
    if r.exists():
        for ext in ('*.ttf','*.otf','*.ttc'):
            fonts += [str(p) for p in r.rglob(ext)]
print(json.dumps({'font_count':len(fonts),'fonts':fonts}, indent=2))
