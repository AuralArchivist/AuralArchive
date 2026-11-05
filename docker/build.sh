#!/bin/bash
# Build the AuralArchive Docker image (tagged with the version from app/VERSION)
# and export it as a tarball into docker_images/ at the repo root (untracked -
# see .gitignore).
#
# Usage:  bash docker/build.sh       release build (from app/)
#         bash docker/build.sh dev   local test build: auralarchive:dev only, never pushed
#         ./build.sh             (from app/docker/, after: chmod +x build.sh)

set -e

usage() {
    echo "usage: bash docker/build.sh [dev] [--platform linux/amd64[,linux/arm64]] [--tag <name>] [--push]"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGES_DIR="$(cd "$REPO_ROOT/.." && pwd)/docker_images"
REGISTRY_IMAGE="ghcr.io/auralarchivist/auralarchive"

case "$(uname -m)" in
    x86_64)          HOST_ARCH=amd64 ;;
    aarch64|arm64)   HOST_ARCH=arm64 ;;
    *)               HOST_ARCH="" ;;
esac
PLATFORM=$([ -n "$HOST_ARCH" ] && echo "linux/${HOST_ARCH}" || echo "")
PUSH=false
DEV=false
IMAGE_NAME=""

while [ $# -gt 0 ]; do
    case "$1" in
        --platform|-p)
            [ $# -ge 2 ] || { echo "ERROR: $1 needs a value (try: linux/arm64)" >&2; exit 1; }
            PLATFORM="$2"; shift 2 ;;
        --platform=*)  PLATFORM="${1#*=}"; shift ;;
        --push)        PUSH=true; shift ;;
        --tag|-t)
            [ $# -ge 2 ] || { echo "ERROR: $1 needs a value (try: auralarchive:1.0.1)" >&2; exit 1; }
            IMAGE_NAME="$2"; shift 2 ;;
        --tag=*)       IMAGE_NAME="${1#*=}"; shift ;;
        -h|--help)     usage; exit 0 ;;
        -*)            echo "ERROR: unknown option '$1' (see --help)" >&2; exit 1 ;;
        *)             IMAGE_NAME="$1"; shift ;;
    esac
done

APP_VERSION="$(tr -d ' \t\n\r' < "$REPO_ROOT/VERSION")"
if [ -z "$APP_VERSION" ]; then
    echo "ERROR: could not read version from $REPO_ROOT/VERSION" >&2
    exit 1
fi
export APP_VERSION

if [ -z "$HOST_ARCH" ] && [ -z "$PLATFORM" ]; then
    echo "ERROR: cannot tell this machine's architecture from '$(uname -m)'; pass --platform" \
         "(linux/amd64 or linux/arm64)" >&2
    exit 1
fi

PLATFORMS=()
for item in $(echo "$PLATFORM" | tr ',' ' '); do
    case "$item" in
        linux/amd64|amd64)          entry=linux/amd64 ;;
        linux/arm64|arm64|aarch64)  entry=linux/arm64 ;;
        "")                         continue ;;
        *)
            echo "ERROR: unsupported platform '$item'" >&2
            echo "       supported: linux/amd64 and linux/arm64 (32-bit ARM is not)" >&2
            exit 1 ;;
    esac
    case " ${PLATFORMS[*]} " in
        *" $entry "*) ;;
        *) PLATFORMS+=("$entry") ;;
    esac
done

if [ "${#PLATFORMS[@]}" -eq 0 ]; then
    if [ -n "$HOST_ARCH" ]; then
        echo "ERROR: --platform needs a value (this machine is linux/${HOST_ARCH})" >&2
    else
        echo "ERROR: this machine's architecture is not supported; pass --platform" \
             "(linux/amd64 or linux/arm64)" >&2
    fi
    exit 1
fi

PLATFORM="$(IFS=','; echo "${PLATFORMS[*]}")"
MULTI_ARCH=false
if [ "${#PLATFORMS[@]}" -gt 1 ]; then
    MULTI_ARCH=true
fi

# No registry name on dev builds, so they can't be pushed to the package by mistake.
if [ "$IMAGE_NAME" = "dev" ]; then
    DEV=true
    IMAGE_NAME=""
    if [ "$PUSH" = true ]; then
        echo "ERROR: a dev build has no registry name, so it cannot be pushed." >&2
        exit 1
    fi
    if [ "$MULTI_ARCH" = true ]; then
        echo "ERROR: a dev build is a single image; use one --platform." >&2
        exit 1
    fi
fi

if [ "$MULTI_ARCH" = true ] && [ "$PUSH" != true ]; then
    echo "ERROR: more than one platform cannot be exported to a tarball." >&2
    echo "       Add --push to publish to a registry, or use a single --platform." >&2
    exit 1
fi

if [ "$DEV" = true ]; then
    IMAGE_NAME="auralarchive:dev"
elif [ -z "$IMAGE_NAME" ]; then
    if [ "$PUSH" = true ]; then
        IMAGE_NAME="$REGISTRY_IMAGE:${APP_VERSION}"
    else
        IMAGE_NAME="auralarchive:${APP_VERSION}"
    fi
fi

# The tag is the colon after the last slash, so `host:5000/x` is not misread.
LAST_ELEMENT="${IMAGE_NAME##*/}"
case "$LAST_ELEMENT" in
    *:*) REPO="${IMAGE_NAME%:*}"; NAME_TAG="${LAST_ELEMENT#*:}" ;;
    *)   REPO="$IMAGE_NAME";     NAME_TAG="$APP_VERSION" ;;
esac

# Add :<minor> and :latest alongside a full version tag, so `:latest` and the
# short version tag keep working as they did before.
TAG_ARGS=(--tag "${REPO}:${NAME_TAG}")
if [ "$NAME_TAG" = "$APP_VERSION" ]; then
    TAG_ARGS+=(--tag "${REPO}:${APP_VERSION%.*}" --tag "${REPO}:latest")
fi

# A release build tags the registry name locally as well, for pushing by hand.
if [ "$DEV" != true ] && [ "$PUSH" != true ] && [ "$REPO" = "auralarchive" ]; then
    for tag in "$APP_VERSION" "${APP_VERSION%.*}" latest; do
        TAG_ARGS+=(--tag "${REGISTRY_IMAGE}:${tag}")
    done
fi

BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

docker buildx version >/dev/null 2>&1 || {
    echo "ERROR: docker buildx is required (multi-architecture builds)." >&2
    exit 1
}

# A docker-container builder is needed for multi-arch; the default docker
# driver can only build the host architecture.
BUILDER_ARGS=()
if [ "$MULTI_ARCH" = true ]; then
    BUILDER_ARGS=(--builder auralarchive-multiarch)
    if ! docker buildx inspect auralarchive-multiarch >/dev/null 2>&1; then
        echo "Creating multi-architecture builder 'auralarchive-multiarch'..."
        # No --use: that would leave this builder as the person's default.
        docker buildx create --name auralarchive-multiarch --driver docker-container >/dev/null
    fi
fi

if [ "$DEV" = true ]; then
    echo "Building local test image auralarchive:dev..."
else
    echo "Building image ${REPO}:${NAME_TAG} for ${PLATFORM}..."
    for t in "${TAG_ARGS[@]}"; do [ "$t" = "--tag" ] || echo "  tag: $t"; done
fi

if [ "$PUSH" = true ]; then
    docker buildx build "${BUILDER_ARGS[@]}" \
        --platform "$PLATFORM" \
        --build-arg "APP_VERSION=${APP_VERSION}" \
        --build-arg "BUILD_DATE=${BUILD_DATE}" \
        "${TAG_ARGS[@]}" \
        --provenance=mode=min,inline-only=true \
        --sbom=false \
        --push -f "$SCRIPT_DIR/Dockerfile" "$REPO_ROOT"
    echo "Done. Pushed the tags above for: ${PLATFORM}"
    exit 0
fi

docker buildx build "${BUILDER_ARGS[@]}" \
    --platform "$PLATFORM" \
    --build-arg "APP_VERSION=${APP_VERSION}" \
    --build-arg "BUILD_DATE=${BUILD_DATE}" \
    "${TAG_ARGS[@]}" \
    --load -f "$SCRIPT_DIR/Dockerfile" "$REPO_ROOT"

if [ "$DEV" = true ]; then
    echo "Done. auralarchive:dev is for local testing only."
    exit 0
fi

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SAFE_NAME="${REPO}:${NAME_TAG}"
SAFE_NAME="${SAFE_NAME//[:\/]/_}"
OUTPUT_FILE="$IMAGES_DIR/${SAFE_NAME}-$(echo "$PLATFORM" | tr '/' '-')-${TIMESTAMP}.tar.gz"

# Gzipped: docker load reads a .gz without being told.
mkdir -p "$IMAGES_DIR"
echo "Saving image '${REPO}:${NAME_TAG}' to ${OUTPUT_FILE}..."
docker save "${REPO}:${NAME_TAG}" | gzip > "$OUTPUT_FILE"

echo "Done. Image saved to: $OUTPUT_FILE"
echo "Load on the target machine with: docker load -i $(basename "$OUTPUT_FILE")"
echo "(the tarball carries ${REPO}:${NAME_TAG}; the other tags are local only)"
