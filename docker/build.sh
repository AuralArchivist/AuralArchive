#!/bin/bash
# Build the AuralArchive Docker image (tagged with the version from app/VERSION)
# and export it as a tarball into docker_images/ at the repo root (untracked -
# see .gitignore).
#
# Usage:  bash docker/build.sh       release build (from app/)
#         bash docker/build.sh dev   local test build: auralarchive:dev only, never pushed
#         ./build.sh             (from app/docker/, after: chmod +x build.sh)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$APP_DIR/.." && pwd)"
IMAGES_DIR="$REPO_ROOT/docker_images"

build_image() {
    docker build -f "$SCRIPT_DIR/Dockerfile" --build-arg APP_VERSION="$1" -t "auralarchive:$1" "$APP_DIR"
}

# No registry name on dev builds, so they can't be pushed to the package by mistake.
if [ "${1:-}" = "dev" ]; then
    echo "Building local test image auralarchive:dev..."
    build_image dev
    echo "Done. auralarchive:dev is for local testing only."
    exit 0
fi

APP_VERSION="$(tr -d ' \t\n\r' < "$APP_DIR/VERSION")"
if [ -z "$APP_VERSION" ]; then
    echo "ERROR: could not read version from $APP_DIR/VERSION" >&2
    exit 1
fi

mkdir -p "$IMAGES_DIR"

echo "Building image auralarchive:${APP_VERSION}..."
build_image "$APP_VERSION"

IMAGE_NAME="auralarchive:${APP_VERSION}"
REGISTRY_IMAGE="ghcr.io/auralarchivist/auralarchive"
MINOR_VERSION="${APP_VERSION%.*}"
for tag in "$APP_VERSION" "$MINOR_VERSION" latest; do
    docker tag "$IMAGE_NAME" "${REGISTRY_IMAGE}:${tag}"
done
echo "Tagged ${REGISTRY_IMAGE}:${APP_VERSION}, :${MINOR_VERSION} and :latest"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SAFE_NAME="${IMAGE_NAME//[:\/]/_}"
OUTPUT_FILE="$IMAGES_DIR/${SAFE_NAME}-${TIMESTAMP}.tar"

echo "Saving image '${IMAGE_NAME}' to ${OUTPUT_FILE}..."
docker save -o "$OUTPUT_FILE" "$IMAGE_NAME"

echo "Done. Image saved to: $OUTPUT_FILE"
