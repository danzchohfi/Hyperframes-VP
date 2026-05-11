# HyperFrames Video Pipeline — Docker image
#
# Single-stage image with:
#   - Python 3.12
#   - ffmpeg + ffprobe
#   - Node.js 22 (for `npx hyperframes render`)
#   - OpenCV (via opencv-python-headless) prebuilt wheels
#
# Listens on $PORT (Railway / Fly inject this) and serves the FastAPI app +
# the static SPA.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_VERSION=22 \
    HFVP_PROJECTS_DIR=/data/projects

# System dependencies. We need ffmpeg, curl + ca-certificates for npm bootstrap,
# fontconfig (for ffmpeg drawtext / ASS rendering), and the Node 22 LTS runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        curl \
        gnupg \
        fontconfig \
        fonts-dejavu \
        libglib2.0-0 \
        libgl1 \
    && curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first so the layer caches when only app code changes.
COPY server/requirements.txt /app/server/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/server/requirements.txt

# Pre-cache the Hyperframes CLI so the first render doesn't pay the npx fetch.
RUN npx --yes hyperframes@0.5.5 --help > /dev/null 2>&1 || true

# App source.
COPY . /app

# Persistent storage path used by storage.PROJECTS_DIR.
RUN mkdir -p /data/projects

# Health probe friendly; Railway expects $PORT.
ENV PORT=8765
EXPOSE 8765

CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-8765}"]
