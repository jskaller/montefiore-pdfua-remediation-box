# Dependency Resolution Rule

When a script fails due to a missing dependency, the agent attempts to resolve
it before escalating. Resolution is tiered by risk and reversibility. Any
dependency installed at runtime is logged in STATUS.json and treated as a
signal that the Dockerfile needs updating.

Runtime resolution is a fallback, not a substitute for a correct Dockerfile.
Every dependency resolved at runtime must be reported so it can be added to
the permanent build.

---

## Tier 1 — Missing Python package (ModuleNotFoundError)

**Trigger:** A script raises `ModuleNotFoundError` or `ImportError`.

**Action:**
1. Identify the missing package from the error message
2. Verify the package is in `requirements.txt` — if not, do not install
   (the package is unknown and may be unsafe or unnecessary)
3. If it is in `requirements.txt`, install it:
   ```bash
   pip install --break-system-packages <package>==<pinned_version>
   ```
   Use the pinned version from `requirements.txt`, not latest.
4. Retry the failed script once
5. If it still fails, escalate to REVIEW_REQUIRED

**Permitted packages (must be in requirements.txt):**
- pymupdf, fonttools, Pillow, ocrmypdf, pikepdf, pdfplumber,
  color-contrast, pypdf

**Never install packages not in requirements.txt autonomously.**

---

## Tier 2 — Missing system binary (apt package)

**Trigger:** A script or tool call fails with `command not found`,
`No such file or directory` on a binary path, or a known error message
indicating a missing system tool.

**Action:**
1. Identify the missing binary from the error
2. Verify it maps to a known apt package in the table below
3. If it does, install it:
   ```bash
   apt-get install -y --no-install-recommends <package>
   ```
4. Retry the failed operation once
5. If it still fails, escalate to REVIEW_REQUIRED

**Permitted apt packages (known safe, no conflict risk):**

| Binary | apt package | When needed |
|--------|-------------|-------------|
| `gs` | `ghostscript` | ocrmypdf PDF/A output |
| `tesseract` | `tesseract-ocr` | OCR remediation |
| `tesseract` (eng) | `tesseract-ocr-eng` | English OCR |
| `tesseract` (spa) | `tesseract-ocr-spa` | Spanish OCR |
| `tesseract` (fra) | `tesseract-ocr-fra` | French OCR |
| `tesseract` (deu) | `tesseract-ocr-deu` | German OCR |
| `tesseract` (por) | `tesseract-ocr-por` | Portuguese OCR |
| `tesseract` (zho) | `tesseract-ocr-chi-sim` | Simplified Chinese OCR |
| `tesseract` (jpn) | `tesseract-ocr-jpn` | Japanese OCR |
| `tesseract` (kor) | `tesseract-ocr-kor` | Korean OCR |
| `tesseract` (ara) | `tesseract-ocr-ara` | Arabic OCR |
| `img2pdf` | `img2pdf` | ocrmypdf image input fallback |
| `fc-cache` | `fontconfig` | Font cache rebuild |

**Never install:**
- veraPDF (version-pinned, complex installer — rebuild Docker)
- qpdf (structural changes — rebuild Docker)
- Java (version-sensitive — rebuild Docker)
- Any package not in the table above

---

## Tier 3 — Unknown failure, run smoke_test.py

**Trigger:** A script fails and the cause is not a clear import error or
missing binary — or multiple scripts are failing.

**Action:**
1. Run `python3 smoke_test.py`
2. Read the failures list from the JSON output
3. For each failure, attempt the fix listed in the `fix` field
4. Re-run `smoke_test.py` to confirm fixes applied
5. If smoke_test.py still fails after fixes, escalate to REVIEW_REQUIRED
   with the full smoke_test.py output attached

---

## What the agent must never do autonomously

- Install veraPDF, qpdf, or Java — these require a Docker rebuild
- Modify `Dockerfile` or `requirements.txt` (document the need instead)
- Install packages not listed in Tier 1 or Tier 2 tables
- Run `apt-get upgrade` or `pip install --upgrade` — version drift
  can break pinned dependencies
- Suppress or ignore a dependency error without resolving it

---

## Logging requirement

Every runtime dependency installation must be recorded in STATUS.json:

```json
"runtime_dependencies_added": [
  {
    "type":    "pip",
    "package": "ocrmypdf",
    "version": "17.4.2",
    "reason":  "ModuleNotFoundError on import",
    "script":  "tools/audit/detect_image_only_pages.py",
    "action":  "ADD_TO_DOCKERFILE"
  },
  {
    "type":    "apt",
    "package": "ghostscript",
    "reason":  "ocrmypdf PDF/A output failed: gs not found",
    "action":  "ADD_TO_DOCKERFILE"
  }
]
```

The `"action": "ADD_TO_DOCKERFILE"` flag is a standing instruction.
Any entry in this list means the Dockerfile is incomplete and must be
updated before the next team member runs a build.

---

## Escalation

If any tier fails to resolve the dependency, mark the job as
REVIEW_REQUIRED with:

```json
"review_reason": "unresolved_dependency",
"unresolved_dependency": {
  "error":   "<original error message>",
  "script":  "<script that failed>",
  "tier_attempted": 1
}
```

Do not attempt to continue remediation on a document when a required
tool is missing. A partial remediation with a missing tool produces
output that cannot be trusted.
