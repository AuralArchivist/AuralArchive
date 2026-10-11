# AuralArchive container image
#
# Build context is app/ (see docker-compose.yml's `context: ..`), so every
# source path below is relative to app/.
#
# Storage (see core/paths.py):
#   /config  app data - settings, database, secrets, cache, logs, backups
#   /data    built-in media folders: library/, downloads/, import/, work/
# Bind-mount /data (recommended) or any of its subfolders. Anything left
# unmounted lives inside the container and is lost when it is recreated.
#
# Python is the free-threaded build (no GIL). There is no official image for
# it, so the standalone build is installed with uv and copied into the image.

# CSS build stage
FROM node:22-slim AS css-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --include=dev
COPY frontend/tailwind.input.css ./
COPY frontend/templates/ ./templates/
COPY frontend/static/ ./static/
RUN npx @tailwindcss/cli -i tailwind.input.css -o static/css/tailwind.css --minify

# FFmpeg build stage: audio-only, LGPL-2.1-or-later build.
# No --enable-gpl / --enable-nonfree. Only FFmpeg's own components plus
# libopus (BSD-3-Clause) and zlib (zlib license) are linked in, statically.
# The final image ships the FFmpeg source tarball and all license texts.
FROM debian:trixie-slim AS ffmpeg-builder
ARG FFMPEG_VERSION=8.1.3
ARG FFMPEG_SHA256=7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3
ARG OPUS_VERSION=1.6.1
ARG OPUS_SHA256=6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential ca-certificates curl nasm pkg-config xz-utils zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSLO "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && curl -fsSLO "https://downloads.xiph.org/releases/opus/opus-${OPUS_VERSION}.tar.gz" \
    && echo "${FFMPEG_SHA256}  ffmpeg-${FFMPEG_VERSION}.tar.xz" | sha256sum -c - \
    && echo "${OPUS_SHA256}  opus-${OPUS_VERSION}.tar.gz" | sha256sum -c - \
    && tar xf "ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && tar xf "opus-${OPUS_VERSION}.tar.gz"
RUN cd "opus-${OPUS_VERSION}" \
    && ./configure --prefix=/opt/ffdeps --disable-shared --enable-static \
        --disable-doc --disable-extra-programs \
    && make -j"$(nproc)" \
    && make install
RUN cd "ffmpeg-${FFMPEG_VERSION}" \
    && PKG_CONFIG_PATH=/opt/ffdeps/lib/pkgconfig ./configure \
        --prefix=/opt/ffmpeg \
        --pkg-config-flags=--static \
        --disable-autodetect --disable-everything \
        --disable-doc --disable-debug --disable-network \
        --disable-ffplay --disable-avdevice \
        --enable-ffmpeg --enable-ffprobe \
        --enable-libopus --enable-zlib \
        --enable-decoder=aac,aac_fixed,aac_latm,mp3,mp3float,mp2,mp2float,flac,alac,opus,vorbis,ac3,eac3,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s32le,pcm_f32le,mjpeg,png \
        --enable-encoder=aac,libopus \
        --enable-demuxer=mov,mp3,aac,flac,ogg,wav,concat,ffmetadata,image2,image_jpeg_pipe,image_png_pipe,matroska,ac3,eac3 \
        --enable-muxer=mp4,ipod,mov,mp3,adts,flac,ogg,opus,ffmetadata,null \
        --enable-parser=aac,aac_latm,mpegaudio,flac,opus,vorbis,ac3,mjpeg,png \
        --enable-bsf=aac_adtstoasc,null \
        --enable-filter=aresample,aformat,anull,null,format,scale \
        --enable-protocol=file,pipe \
    && make -j"$(nproc)" \
    && make install \
    && strip /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe
RUN mkdir -p /opt/licenses/ffmpeg /opt/licenses/opus /opt/licenses/zlib \
    && cp "ffmpeg-${FFMPEG_VERSION}/COPYING.LGPLv2.1" "ffmpeg-${FFMPEG_VERSION}/LICENSE.md" /opt/licenses/ffmpeg/ \
    && cp "ffmpeg-${FFMPEG_VERSION}.tar.xz" /opt/licenses/ffmpeg/ \
    && /opt/ffmpeg/bin/ffmpeg -hide_banner -buildconf > /opt/licenses/ffmpeg/BUILD_CONFIGURATION.txt \
    && cp "opus-${OPUS_VERSION}/COPYING" /opt/licenses/opus/ \
    && cp /usr/share/doc/zlib1g/copyright /opt/licenses/zlib/ \
    && printf '%s\n' \
        "This image includes FFmpeg ${FFMPEG_VERSION} (https://ffmpeg.org), built from" \
        "unmodified source and licensed under the GNU LGPL version 2.1 or later." \
        "The complete source is included: ffmpeg/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
        "Build options: ffmpeg/BUILD_CONFIGURATION.txt" \
        "" \
        "FFmpeg is linked with:" \
        "  libopus ${OPUS_VERSION} (BSD-3-Clause, static), https://opus-codec.org, see opus/COPYING" \
        "  zlib (zlib license, Debian package), https://zlib.net, see zlib/copyright" \
        > /opt/licenses/README.txt

# Python stage: free-threaded interpreter plus dependencies (compilers never
# reach the final image)
FROM debian:trixie-slim AS py-builder
ARG PYTHON_VERSION=3.14.8
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_NO_CACHE=1
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential ca-certificates libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN uv python install "${PYTHON_VERSION}t" \
    && uv venv --python "${PYTHON_VERSION}t" /opt/venv
COPY requirements.txt ./
RUN VIRTUAL_ENV=/opt/venv uv pip install -r requirements.txt

# Application stage
FROM debian:trixie-slim

ARG APP_VERSION=dev
LABEL org.opencontainers.image.title="AuralArchive" \
      org.opencontainers.image.description="Self-hosted audiobook library manager" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.source="https://github.com/AuralArchivist/AuralArchive" \
      org.opencontainers.image.licenses="GPL-3.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    DOCKER_CONTAINER=true \
    PUID=1000 \
    PGID=1000 \
    UMASK=002

# tini as PID 1 (forwards stop signals), gosu to drop root in the entrypoint,
# tzdata for TZ. ffmpeg and Python come from the build stages.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates tini gosu tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 auralarchive \
    && useradd --uid 1000 --gid auralarchive --home-dir /config --no-create-home \
        --shell /usr/sbin/nologin auralarchive

COPY --from=py-builder /opt/python /opt/python
COPY --from=py-builder /opt/venv /opt/venv
COPY --from=ffmpeg-builder /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/
COPY --from=ffmpeg-builder /opt/licenses /usr/share/licenses/auralarchive-thirdparty

WORKDIR /app/auralarchive

# Application code stays root-owned: the app user can read it, never modify it.
COPY . .
COPY --from=css-builder /build/static/css/tailwind.css ./frontend/static/css/tailwind.css

# Built-in storage folders, owned by the default app user so the image also
# works when started with `user: 1000:1000` (no root entrypoint work needed).
RUN mkdir -p /config /library /downloads /import /work \
    && chown auralarchive:auralarchive /config /library /downloads /import /work

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('AURALARCHIVE_PORT', '8765'), timeout=4)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["python", "app.py"]
