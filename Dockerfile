# ─────────────────────────────────────────────────────────────────────────────
# Montefiore PDF/UA Remediation Suite
# Base: Ubuntu 24.04 LTS (Noble)
# ─────────────────────────────────────────────────────────────────────────────

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# ── System dependencies ───────────────────────────────────────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    # Java runtime for veraPDF
    openjdk-17-jre-headless \
    # PDF structural validation and repair
    qpdf \
    # OCR engine
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-spa \
    # Required by ocrmypdf for PDF/A output and image processing
    ghostscript \
    # Build/fetch utilities
    wget \
    unzip \
    git \
    ca-certificates \
    # Python
    python3 \
    python3-pip \
    python3-dev \
    # Required by some Python wheel builds
    gcc \
    g++ \
    # Font rendering
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# ── Font packages — Tier 1: metric-compatible and core ───────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation \
    fonts-crosextra-arimo \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-crosextra-tinos \
    fonts-urw-base35 \
    fonts-texgyre \
    fonts-noto-core \
    fonts-noto-mono \
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    fonts-noto \
    fonts-open-sans \
    fonts-roboto \
    fonts-ubuntu \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    fonts-lato \
    fonts-hack \
    fonts-inconsolata \
    fonts-anonymous-pro \
    fonts-firacode \
    fonts-jetbrains-mono \
    fonts-ibm-plex \
    fonts-inter \
    fonts-atkinson-hyperlegible-ttf \
    fonts-pt-sans \
    fonts-pt-serif \
    fonts-pt-mono \
    fonts-stix \
    fonts-sil-charis \
    fonts-sil-andika \
    fonts-sil-scheherazade \
    fonts-gentium-plus \
    && rm -rf /var/lib/apt/lists/*

# ── Font packages — Tier 2: extended remediation coverage ────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-linux-libertine \
    fonts-cantarell \
    fonts-droid \
    fonts-noto-extra \
    fonts-symbola \
    fonts-opensymbol \
    fonts-hosny-amiri \
    fonts-wqy-zenhei \
    fonts-ipafont \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# ── veraPDF 1.30.1 Arlington ──────────────────────────────────────────────────

ENV VERAPDF_VERSION=1.30.1
ENV VERAPDF_BIN=/opt/verapdf/verapdf

COPY verapdf-install-response.xml /tmp/verapdf-install-response.xml

RUN wget -q \
    "https://software.verapdf.org/releases/arlington/1.30/verapdf-arlington-${VERAPDF_VERSION}-installer.zip" \
    -O /tmp/verapdf-installer.zip \
    && unzip -q /tmp/verapdf-installer.zip -d /tmp/verapdf-installer \
    && java -Djava.awt.headless=true \
            -jar /tmp/verapdf-installer/verapdf-arlington-${VERAPDF_VERSION}-installer.jar \
            /tmp/verapdf-install-response.xml \
    && rm -rf /tmp/verapdf-installer.zip \
              /tmp/verapdf-installer \
              /tmp/verapdf-install-response.xml \
    && /opt/verapdf/verapdf --version

# ── Python dependencies ───────────────────────────────────────────────────────

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────

COPY tools/  ./tools/
COPY skills/ ./skills/
COPY smoke_test.py .

RUN find tools/ -name "*.sh" -exec chmod +x {} \;

# ── Init script ───────────────────────────────────────────────────────────────

COPY docker-init.sh /usr/local/bin/docker-init.sh
RUN chmod +x /usr/local/bin/docker-init.sh

# ── Environment variables ─────────────────────────────────────────────────────

ENV VERAPDF_BIN=/opt/verapdf/verapdf
ENV QPDF_BIN=/usr/bin/qpdf
ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# ── Workspace volume mount point ──────────────────────────────────────────────

RUN mkdir -p /app/workspace/input \
             /app/workspace/jobs \
             /app/workspace/output \
             /app/workspace/archive \
             /app/workspace/assets/validation_profiles \
             /app/workspace/templates

VOLUME /app/workspace

# ── Entrypoint ────────────────────────────────────────────────────────────────

ENTRYPOINT ["/usr/local/bin/docker-init.sh"]
CMD ["python3", "smoke_test.py"]
