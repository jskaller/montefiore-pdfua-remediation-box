# ─────────────────────────────────────────────────────────────────────────────
# Montefiore PDF/UA Remediation Suite
# Base: Ubuntu 24.04 LTS (Noble)
# ─────────────────────────────────────────────────────────────────────────────

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# ── Enable universe and multiverse repos ─────────────────────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository universe \
    && add-apt-repository multiverse \
    && rm -rf /var/lib/apt/lists/*

# ── System dependencies ───────────────────────────────────────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless \
    qpdf \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-spa \
    ghostscript \
    wget \
    unzip \
    git \
    curl \
    ca-certificates \
    python3 \
    python3-pip \
    python3-dev \
    gcc \
    g++ \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# ── Font packages — Tier 1: metric-compatible and core ───────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-liberation \
    fonts-croscore \
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
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
    fonts-paratype \
    fonts-stix \
    fonts-sil-charis \
    fonts-sil-andika \
    fonts-sil-scheherazade \
    fonts-sil-gentiumplus \
    && rm -rf /var/lib/apt/lists/*

# ── Font packages — Tier 2: extended remediation coverage ────────────────────

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-linuxlibertine \
    fonts-cantarell \
    fonts-droid-fallback \
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
ENV VERAPDF_BIN=/opt/verapdf/arlington-pdf-model-checker

COPY verapdf-install-response.xml /tmp/verapdf-install-response.xml

RUN wget -q \
    "https://software.verapdf.org/releases/arlington/1.30/verapdf-arlington-${VERAPDF_VERSION}-installer.zip" \
    -O /tmp/verapdf-installer.zip \
    && unzip -q /tmp/verapdf-installer.zip -d /tmp/verapdf-installer \
    && java -Djava.awt.headless=true \
            -jar /tmp/verapdf-installer/verapdf-arlington-${VERAPDF_VERSION}/verapdf-izpack-installer-${VERAPDF_VERSION}.jar \
            /tmp/verapdf-install-response.xml \
    && rm -rf /tmp/verapdf-installer.zip \
              /tmp/verapdf-installer \
              /tmp/verapdf-install-response.xml \
    && /opt/verapdf/arlington-pdf-model-checker --version

# ── veraPDF 1.30.1 Greenfield (full PDF/UA-1 + WCAG 2.2 validator) ──────────
# Installs the standard veraPDF Greenfield distribution alongside Arlington.
# Greenfield implements the complete Matterhorn Protocol (31 checkpoints,
# 136 failure conditions) including 7.3 (Figure Alt), 7.4 (Headings),
# 7.5 (Tables), 7.6 (Lists). The Arlington binary only validates the
# PDF object model (ISO 32000-2) and misses these semantic PDF/UA-1 rules.
#
# Both are kept installed. The pipeline uses VERAPDF_GREENFIELD_BIN for
# compliance validation; Arlington remains available for object-model checks.

ENV VERAPDF_GREENFIELD_BIN=/opt/verapdf-greenfield/verapdf

COPY verapdf-greenfield-install-response.xml /tmp/verapdf-greenfield-install-response.xml

RUN wget -q \
    "https://software.verapdf.org/rel/1.30/verapdf-greenfield-${VERAPDF_VERSION}-installer.zip" \
    -O /tmp/verapdf-greenfield-installer.zip \
    && unzip -q /tmp/verapdf-greenfield-installer.zip -d /tmp/verapdf-greenfield-installer \
    && java -Djava.awt.headless=true \
            -jar /tmp/verapdf-greenfield-installer/verapdf-greenfield-${VERAPDF_VERSION}/verapdf-izpack-installer-${VERAPDF_VERSION}.jar \
            /tmp/verapdf-greenfield-install-response.xml \
    && rm -rf /tmp/verapdf-greenfield-installer.zip \
              /tmp/verapdf-greenfield-installer \
              /tmp/verapdf-greenfield-install-response.xml \
    && /opt/verapdf-greenfield/verapdf --version

# ── Node.js 24 + OpenClaw ────────────────────────────────────────────────────
# OpenClaw requires Node.js 22.14+ (24 recommended)

RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && node --version \
    && npm install -g openclaw@latest \
    && openclaw --version

# ── Python dependencies ───────────────────────────────────────────────────────

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────

COPY tools/      ./tools/
COPY skills/     ./skills/
COPY AGENTS.md         ./AGENTS.md
COPY AGENTS_GENERAL.md ./AGENTS_GENERAL.md
COPY SOUL.md           ./SOUL.md
COPY IDENTITY.md       ./IDENTITY.md
COPY TOOLS.md          ./TOOLS.md
COPY smoke_test.py .

RUN find tools/ -name "*.sh" -exec chmod +x {} \;

# ── OpenClaw configuration ───────────────────────────────────────────────────

RUN mkdir -p /root/.openclaw
COPY openclaw.json /root/.openclaw/openclaw.json

# ── Init script ───────────────────────────────────────────────────────────────

COPY docker-init.sh /usr/local/bin/docker-init.sh
RUN chmod +x /usr/local/bin/docker-init.sh

# ── Environment variables ─────────────────────────────────────────────────────

ENV VERAPDF_BIN=/opt/verapdf/arlington-pdf-model-checker
ENV VERAPDF_GREENFIELD_BIN=/opt/verapdf-greenfield/verapdf
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
CMD ["openclaw", "gateway", "run", "--force"]
