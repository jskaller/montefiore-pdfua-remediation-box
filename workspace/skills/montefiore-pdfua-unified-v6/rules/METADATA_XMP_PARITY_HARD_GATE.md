# Metadata and XMP Parity Hard Gate

Metadata is a hard local gate. It is not sufficient for `pdfinfo` or the PDF Info dictionary to show corrected values. The embedded XMP metadata packet must also match.

Required fixed values in both PDF Info and XMP where applicable:

- Author: `Montefiore Einstein`
- Creator: `Montefiore Einstein`
- Producer: `Montefiore Einstein`

Required descriptive metadata:

- Title, derived from the visible document title when possible.
- Subject, derived from the document content.
- Description, derived from the document content.
- Keywords, content-specific; do not use generic-only `PDF/UA`.
- Language, usually `en-US` unless another primary language is clear.

PDF/UA identifier:

- PDF/UA-1 outputs must include a valid XMP PDF/UA identifier, typically `pdfuaid:part=1` with the correct namespace.
- Do not mix PDF/UA-2/PDF 2.0 identifiers into PDF/UA-1 outputs unless explicitly targeting PDF/UA-2.

The metadata audit must fail if it checks only the PDF Info dictionary. XMP parity is mandatory.

Minimum parity checks:

- Info `/Author` equals XMP author/creator value.
- Info `/Creator` equals XMP `xmp:CreatorTool`.
- Info `/Producer` equals XMP `pdf:Producer`.
- Info `/Title` equals XMP title.
- Info `/Subject` equals XMP description/subject where present.
- Info `/Keywords` equals XMP keywords/subject where present.
- Catalog `/Lang` is present.
- XMP `pdfuaid:part` is present for PDF/UA output.

Audit metadata after the final PDF save and after any tool that may rewrite metadata. If metadata is modified by a later step, rerun the metadata audit before packaging.
