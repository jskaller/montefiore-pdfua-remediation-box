# veraPDF Profile Selection Rule

For PDF/UA-1 remediation, the required custom WCAG profile is pinned exactly:

`PDF_UA/WCAG-2-2-Machine.xml`

Wildcard discovery such as `*WCAG*Machine*.xml` is prohibited unless the selected profile is logged and confirmed to match the active PDF conformance target.

`PDF_UA/WCAG-2-2-Machine-PDF20.xml` must not be used for PDF/UA-1 output because it may introduce PDF 2.0 / PDF/UA-2 namespace requirements that are not applicable to PDF/UA-1 deliverables.

Required PDF/UA-1 command pattern:

```bash
verapdf --format xml --verbose --maxfailuresdisplayed -1 --flavour ua1 <pdf>
```

Required WCAG profile command pattern:

```bash
verapdf --format xml --verbose --maxfailuresdisplayed -1 --profile <assets>/validation_profiles/veraPDF-validation-profiles-integration/PDF_UA/WCAG-2-2-Machine.xml <pdf>
```

Record the exact commands, profile path, profile name, failedRules, failedChecks, passedRules, and passedChecks in the package.
