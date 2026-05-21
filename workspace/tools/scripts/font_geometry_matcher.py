#!/usr/bin/env python3
import sys, json
try:
    from fontTools.ttLib import TTFont
except Exception as e:
    print(json.dumps({'result':'ERROR','error':f'fontTools unavailable: {e}'})); sys.exit(2)
if len(sys.argv)<3:
    print('usage: font_geometry_matcher.py <text> <font1> [font2 ...]', file=sys.stderr); sys.exit(2)
text=sys.argv[1]
rank=[]
for fp in sys.argv[2:]:
    try:
        f=TTFont(fp, fontNumber=0)
        cmap={c for table in f['cmap'].tables for c in table.cmap.keys()}
        coverage=sum(1 for ch in text if ord(ch) in cmap)/max(1,len(text))
        head=f['head']; hhea=f['hhea']
        rank.append({'font':fp,'glyph_coverage_pct':round(coverage*100,2),'units_per_em':head.unitsPerEm,'ascent':hhea.ascent,'descent':hhea.descent})
    except Exception as e:
        rank.append({'font':fp,'error':str(e)})
rank.sort(key=lambda x: x.get('glyph_coverage_pct',0), reverse=True)
print(json.dumps({'text':text,'candidate_rankings':rank}, indent=2))
