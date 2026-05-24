#!/usr/bin/env python3
"""
smoke_test.py
Verifies the container is correctly assembled and all required tools,
assets, and Python dependencies are in place.

Run after every build:
  docker compose exec remediation python3 smoke_test.py

Exit codes:
  0  all checks pass
  1  one or more checks failed
"""
import sys, json, shutil
from pathlib import Path

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/app')
checks = []

def chk(name, cond, fix=''):
    checks.append({'check': name, 'pass': bool(cond), 'fix': fix})

# ── Directory structure ───────────────────────────────────────────────────────

chk('tools/audit exists',
    (root / 'tools/audit').exists())
chk('tools/repair exists',
    (root / 'tools/repair').exists())
chk('tools/packaging exists',
    (root / 'tools/packaging').exists())
chk('tools/qa exists',
    (root / 'tools/qa').exists())
chk('skills/ exists',
    (root / 'skills').exists(),
    'skills/ directory missing — check Dockerfile COPY step')
chk('workspace/assets/validation_profiles exists',
    (root / 'workspace/assets/validation_profiles').exists(),
    'Created by docker-init.sh on first run')

# ── Critical files ────────────────────────────────────────────────────────────

chk('SKILL.md present',
    any((root / 'skills').rglob('SKILL.md')),
    'SKILL.md missing from skills/ tree')
chk('Dockerfile present',
    (root.parent / 'Dockerfile').exists() or (root / 'Dockerfile').exists(),
    'Dockerfile missing from repo root')
chk('docker-compose.yml present',
    (root.parent / 'docker-compose.yml').exists() or (root / 'docker-compose.yml').exists(),
    'docker-compose.yml missing from repo root')
chk('.env.example present',
    (root.parent / '.env.example').exists() or (root / '.env.example').exists(),
    'Copy .env.example to repo root')
chk('requirements.txt present',
    (root / 'requirements.txt').exists())

# ── Audit scripts ─────────────────────────────────────────────────────────────

audit_scripts = [
    'contrast_audit.py',
    'detect_image_only_pages.py',
    'font_geometry_matcher.py',
    'font_inventory.py',
    'metadata_xmp_parity_audit.py',
    'parse_verapdf_summary.py',
    'run_qpdf_check.sh',
    'run_verapdf_profiles.sh',
    'table_semantics_audit.py',
]
for s in audit_scripts:
    chk(f'tools/audit/{s}', (root / 'tools/audit' / s).exists())

# ── Repair scripts ────────────────────────────────────────────────────────────

repair_scripts = [
    'fix_contrast_color_runs.py',
    'fix_figure_alt_text.py',
    'fix_link_annotation_descriptions.py',
    'fix_list_numbering.py',
    'fix_metadata_xmp_parity.py',
    'fix_notdef_glyphs.py',
    'fix_parent_tree_mcids.py',
    'fix_pdfua_identifier.py',
    'fix_table_headers.py',
    'font_replacement_report.py',
    'generate_alt_text_drafts.py',
    'generate_alt_text_review_report.py',
]
for s in repair_scripts:
    chk(f'tools/repair/{s}', (root / 'tools/repair' / s).exists())

# ── QA scripts ────────────────────────────────────────────────────────────────

qa_scripts = ['preservation_audit.py', 'render_compare.py', 'visual_qa.py']
for s in qa_scripts:
    chk(f'tools/qa/{s}', (root / 'tools/qa' / s).exists())

# ── Packaging scripts ─────────────────────────────────────────────────────────

packaging_scripts = [
    'checksums.py',
    'cleanup_job.py',
    'package_deliverables.py',
    'package_scaffold.py',
    'status_json_writer.py',
]
for s in packaging_scripts:
    chk(f'tools/packaging/{s}', (root / 'tools/packaging' / s).exists())

# ── Pinned WCAG profile ───────────────────────────────────────────────────────

wcag_profiles = list(root.rglob('WCAG-2-2-Machine.xml'))
chk('WCAG-2-2-Machine.xml present',
    len(wcag_profiles) > 0,
    'Run docker-init.sh or: git clone --branch integration '
    'https://github.com/veraPDF/veraPDF-validation-profiles.git '
    'workspace/assets/validation_profiles/veraPDF-validation-profiles-integration')

# ── System tools ──────────────────────────────────────────────────────────────

chk('qpdf available',
    shutil.which('qpdf') is not None,
    'apt install qpdf')
chk('java available',
    shutil.which('java') is not None,
    'apt install openjdk-17-jre-headless')
chk('python3 available',
    shutil.which('python3') is not None)
chk('tesseract available',
    shutil.which('tesseract') is not None,
    'apt install tesseract-ocr tesseract-ocr-eng')
chk('ocrmypdf available',
    shutil.which('ocrmypdf') is not None,
    'pip install ocrmypdf (also requires ghostscript)')
chk('ghostscript available',
    shutil.which('gs') is not None,
    'apt install ghostscript')
chk('git available',
    shutil.which('git') is not None,
    'apt install git')

# ── veraPDF ───────────────────────────────────────────────────────────────────

import os
verapdf_bin = os.environ.get('VERAPDF_BIN', '/opt/verapdf/verapdf')
chk('veraPDF binary present',
    Path(verapdf_bin).exists(),
    f'veraPDF not found at {verapdf_bin} — check Dockerfile install step')

# ── Python dependencies ───────────────────────────────────────────────────────

py_deps = [
    ('fitz',           'pymupdf',        'pip install pymupdf'),
    ('fontTools',      'fonttools',       'pip install fonttools'),
    ('PIL',            'Pillow',          'pip install Pillow'),
    ('ocrmypdf',       'ocrmypdf',        'pip install ocrmypdf'),
    ('pikepdf',        'pikepdf',         'pip install pikepdf'),
    ('pdfplumber',     'pdfplumber',      'pip install pdfplumber'),
    ('color_contrast', 'color-contrast',  'pip install color-contrast'),
    ('pypdf',          'pypdf',           'pip install pypdf'),
]

for import_name, pkg_name, fix in py_deps:
    try:
        __import__(import_name)
        chk(f'{pkg_name} importable', True)
    except ImportError:
        chk(f'{pkg_name} importable', False, fix)

# ── Tesseract language packs ──────────────────────────────────────────────────

tessdata = Path(os.environ.get('TESSDATA_PREFIX',
                '/usr/share/tesseract-ocr/5/tessdata'))
chk('tesseract eng language pack',
    (tessdata / 'eng.traineddata').exists(),
    'apt install tesseract-ocr-eng')
chk('tesseract spa language pack',
    (tessdata / 'spa.traineddata').exists(),
    'apt install tesseract-ocr-spa')

# ── Summary ───────────────────────────────────────────────────────────────────

passed = sum(1 for c in checks if c['pass'])
failed = sum(1 for c in checks if not c['pass'])
result = 'PASS' if failed == 0 else 'FAIL'

print(json.dumps({
    'result':   result,
    'passed':   passed,
    'failed':   failed,
    'checks':   checks,
    'failures': [c for c in checks if not c['pass']],
}, indent=2))

sys.exit(0 if result == 'PASS' else 1)
