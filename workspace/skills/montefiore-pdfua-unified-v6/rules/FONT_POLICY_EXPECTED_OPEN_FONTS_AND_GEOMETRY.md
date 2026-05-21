# Font Policy, Expected Open Font Inventory, and Geometry Matching

Font replacement is a last-resort repair path. Preserve source/embedded fonts whenever possible.

Do not bundle font files in the quickstart package. Use locally installed fonts, source-embedded fonts, or a separately approved external font package. Never redistribute proprietary fonts extracted from a source PDF.

Before replacing any font, run font inventory and geometry matching. The replacement must have 100% glyph coverage for affected text and must preserve line breaks, bounding boxes, native text extraction, tags, reading order, links, annotations, and page geometry.

Do not claim a 1:1 font replacement unless font geometry metrics and before/after visual QA support that claim.

## Expected freely available candidate families

This list is a candidate search inventory, not a set of bundled files and not a guarantee that fonts are installed locally. The runtime environment may use locally installed fonts or a separately approved font package.

### Metric-compatible / Office-style fallbacks

Arial / Helvetica-like:

- Arimo
- Liberation Sans
- Nimbus Sans
- TeX Gyre Heros
- Roboto
- Noto Sans
- Open Sans
- Source Sans 3

Calibri / Aptos-like:

- Carlito
- Noto Sans
- Source Sans 3
- Open Sans
- Atkinson Hyperlegible
- Public Sans
- Inter

Times / Times New Roman-like:

- Tinos
- Liberation Serif
- Nimbus Roman
- TeX Gyre Termes
- Noto Serif
- Source Serif 4

Courier / Courier New-like:

- Cousine
- Liberation Mono
- Nimbus Mono
- TeX Gyre Cursor
- Noto Sans Mono
- Source Code Pro

Cambria / Georgia-like:

- Caladea
- Noto Serif
- Source Serif 4
- Merriweather
- Lora
- Libre Baskerville
- PT Serif

Century Gothic / geometric sans-like:

- Montserrat
- Poppins
- Raleway
- Nunito Sans
- Questrial
- Jost
- League Spartan

### Sans-serif families

- Noto Sans
- Noto Sans Display
- Noto Sans Text
- Noto Sans Condensed
- Noto Sans SemiCondensed
- Noto Sans ExtraCondensed
- Source Sans 3
- Open Sans
- Roboto
- Roboto Flex
- Roboto Condensed
- Lato
- Inter
- Public Sans
- Work Sans
- IBM Plex Sans
- IBM Plex Sans Condensed
- Fira Sans
- Fira Sans Condensed
- PT Sans
- PT Sans Narrow
- Ubuntu
- Barlow
- Barlow Condensed
- Barlow Semi Condensed
- Archivo
- Archivo Narrow
- Oswald
- Montserrat
- Poppins
- Raleway
- Nunito Sans
- Merriweather Sans
- Karla
- Cabin
- Mulish
- Manrope
- DM Sans
- Rubik
- Heebo
- Assistant
- Hind
- Hind Siliguri
- Hind Madurai
- Hind Vadodara
- Hind Guntur
- Titillium Web
- Exo 2
- Overpass
- Red Hat Display
- Red Hat Text
- Alegreya Sans
- Asap
- Asap Condensed
- Chivo
- Encode Sans
- Encode Sans Condensed
- Saira
- Saira Condensed
- Yantramanav
- Questrial
- Jost
- Urbanist
- Lexend
- Lexend Deca
- Atkinson Hyperlegible

### Serif families

- Noto Serif
- Noto Serif Display
- Source Serif 4
- Merriweather
- Lora
- Libre Baskerville
- PT Serif
- IBM Plex Serif
- Crimson Pro
- Crimson Text
- Cormorant
- Cormorant Garamond
- EB Garamond
- Libre Caslon Text
- Libre Caslon Display
- Alegreya
- Bitter
- Arvo
- Roboto Serif
- Roboto Slab
- Zilla Slab
- Sanchez
- Slabo 13px
- Slabo 27px
- Spectral
- Vollkorn
- Domine
- Faustina
- Cardo
- Gentium Plus
- Charis SIL
- Andika
- Scheherazade New
- Tinos
- Liberation Serif
- TeX Gyre Termes
- TeX Gyre Pagella
- TeX Gyre Schola

### Monospace families

- Noto Sans Mono
- Source Code Pro
- Roboto Mono
- IBM Plex Mono
- Fira Code
- Fira Mono
- JetBrains Mono
- Inconsolata
- Cousine
- Liberation Mono
- Ubuntu Mono
- PT Mono
- Space Mono
- Anonymous Pro
- Overpass Mono
- Red Hat Mono
- DejaVu Sans Mono
- TeX Gyre Cursor
- Hack

### Symbol, math, emoji, and broad Unicode

- Noto Sans Symbols
- Noto Sans Symbols 2
- Noto Sans Math
- Noto Emoji
- Noto Color Emoji
- STIX Two Text
- STIX Two Math
- DejaVu Sans
- DejaVu Serif
- FreeSerif
- FreeSans
- FreeMono
- Symbola, only if locally available and license-approved

### CJK and pan-Unicode candidates

- Noto Sans CJK
- Noto Serif CJK
- Noto Sans JP
- Noto Serif JP
- Noto Sans KR
- Noto Serif KR
- Noto Sans SC
- Noto Serif SC
- Noto Sans TC
- Noto Serif TC
- Noto Sans HK
- Source Han Sans
- Source Han Serif

### Arabic, Hebrew, Indic, and global script candidates

Arabic:

- Noto Naskh Arabic
- Noto Kufi Arabic
- Noto Sans Arabic
- Amiri
- Cairo
- Tajawal
- IBM Plex Sans Arabic
- Scheherazade New

Hebrew:

- Noto Sans Hebrew
- Noto Serif Hebrew
- Heebo
- Assistant
- Frank Ruhl Libre
- David Libre

Devanagari / Indic:

- Noto Sans Devanagari
- Noto Serif Devanagari
- Hind
- Mukta
- Tiro Devanagari Hindi
- Noto Sans Bengali
- Noto Serif Bengali
- Noto Sans Tamil
- Noto Serif Tamil
- Noto Sans Telugu
- Noto Serif Telugu
- Noto Sans Kannada
- Noto Sans Malayalam
- Noto Sans Gujarati
- Noto Sans Gurmukhi
- Noto Sans Sinhala

Thai / Southeast Asian:

- Noto Sans Thai
- Noto Serif Thai
- Sarabun
- Kanit
- Prompt
- Bai Jamjuree
- Noto Sans Khmer
- Noto Serif Khmer
- Noto Sans Lao
- Noto Sans Myanmar

### Presentation and display candidates

- Montserrat
- Poppins
- Raleway
- Oswald
- Bebas Neue
- League Spartan
- Anton
- Archivo Black
- Abril Fatface
- Playfair Display
- DM Serif Display
- Cormorant Garamond
- Libre Bodoni
- Nunito
- Quicksand
- Rubik
- Varela Round
- M PLUS Rounded 1c
- Comfortaa
- Dosis

## Geometry matching requirements

Use candidate fonts only after metric comparison. Minimum criteria:

- Required glyph coverage: 100%.
- Style class match: sans, serif, mono, display, symbol, or script-specific.
- Weight/slant/stretch availability.
- Ascent/descent/cap-height/x-height similarity.
- Per-character advance-width similarity.
- Text-run width delta within threshold.
- Line-break changes: 0.
- Clipping/overlap introduced: 0.
- Rendered crop visual QA passes or has documented acceptable delta.

Default thresholds:

- Text-run width delta: <= 2%.
- Average advance-width delta: <= 3%.
- Line-break changes: 0.
- Clipping/overlap introduced: 0.

Strict or brand-sensitive thresholds:

- Text-run width delta: <= 1%.
- Average advance-width delta: <= 1.5%.
- Line-break changes: 0.
- Visual QA: pass.
