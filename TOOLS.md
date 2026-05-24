# Tools and Conventions

## Binary paths (inside Docker container)

| Tool | Path |
|------|------|
| veraPDF | `/opt/verapdf/arlington-pdf-model-checker` |
| qpdf | `/usr/bin/qpdf` |
| python3 | `/usr/bin/python3` |
| java | `/usr/bin/java` |
| bash | `/usr/bin/bash` |

## Script paths (relative to workspace root)

All scripts are under `tools/` and take `<input> <output>` arguments.
All scripts output JSON to stdout and exit 0 on pass, 1 on fail, 2 on error.

### Audit (read-only)
```
tools/audit/run_verapdf_profiles.sh    <verapdf-bin> <profiles-root> <pdf> <out-dir>
tools/audit/run_qpdf_check.sh          <pdf> <out-dir> [--linearize]
tools/audit/parse_verapdf_summary.py   <xml> [<xml2> ...]
tools/audit/metadata_xmp_parity_audit.py  <pdf> [--org "Org Name"]
tools/audit/font_inventory.py          <pdf>
tools/audit/font_geometry_matcher.py   <text-sample> <font1.ttf> [font2.ttf ...]
tools/audit/table_semantics_audit.py   <pdf>
tools/audit/contrast_audit.py          <pdf>
tools/audit/detect_image_only_pages.py <pdf> [--min-chars N]
tools/audit/metadata_xmp_parity_audit.py  <pdf> [--out results.json]
```

### Repair (modifies PDFs — always write to new output path)
```
tools/repair/fix_pdfua_identifier.py              <in.pdf> <out.pdf>
tools/repair/fix_metadata_xmp_parity.py           <in.pdf> <out.pdf> [--title X] [--subject X] [--description X] [--keywords X]
tools/repair/fix_figure_alt_text.py               <in.pdf> <out.pdf> [--alt-map map.json]
tools/repair/fix_table_headers.py                 <in.pdf> <out.pdf>
tools/repair/fix_link_annotation_descriptions.py  <in.pdf> <out.pdf>
tools/repair/fix_list_numbering.py                <in.pdf> <out.pdf>
tools/repair/fix_notdef_glyphs.py                 <pdf>          (audit only, no output)
tools/repair/fix_parent_tree_mcids.py             <in.pdf> <out.pdf>
tools/repair/fix_contrast_color_runs.py           <pdf>          (audit only, no output)
tools/repair/font_replacement_report.py           <pdf>          (audit only, no output)
tools/repair/fix_cidset.py                        <in.pdf> <out.pdf> [--out results.json]
tools/repair/generate_alt_text_drafts.py          <pdf> --fix-output <json> --out <alt_map_draft.json>
tools/repair/generate_alt_text_review_report.py   <pdf> --draft <json> --out <html> --map-out <json>
```

### QA
```
tools/qa/preservation_audit.py  <source.pdf> <output.pdf>
tools/qa/render_compare.py      <source.pdf> <output.pdf> <out-dir> [--dpi 150] [--threshold 0.01]
tools/qa/visual_qa.py           <pdf> <out-dir> [--dpi 96]
```

### Packaging
```
tools/packaging/package_scaffold.py     <output-base-dir> <job-name>
tools/packaging/package_deliverables.py <job-dir> <remediated-pdf> [--source-pdf original.pdf]
tools/packaging/status_json_writer.py   <job-dir> [--pdf original.pdf] [--out STATUS.json]
tools/packaging/checksums.py            generate <dir> [--out SHA256SUMS.txt]
tools/packaging/checksums.py            verify   <dir> <SHA256SUMS.txt>
tools/packaging/cleanup_job.py          <job_name> [--confirm] [--ticket TICKET]
```

## veraPDF profiles root

`/app/workspace/assets/validation_profiles/veraPDF-validation-profiles-integration`

Pinned WCAG profile: `PDF_UA/WCAG-2-2-Machine.xml` relative to profiles root.

## Naming conventions

- Job names: `<basename>_pdfua_<YYYY-MM-DD>` e.g. `annual_report_pdfua_2026-05-22`
- Intermediate repair passes: `<basename>_pass1.pdf`, `_pass2.pdf`, etc.
- Final output: `<basename>_pdfua_final.pdf`
- Never overwrite source PDFs — always write to a new path
