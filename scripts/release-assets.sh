#!/usr/bin/env bash
# The files a spotdown GitHub Release carries for an image-only install (docs/runbooks/docker-image.md):
# the one list the release workflow and the tests read. A server needs these, the image and its own
# secrets/ (the key files), never a clone.
#
#   scripts/release-assets.sh list          one "<source path> <asset name>" line per asset
#   scripts/release-assets.sh build DIR     copy the assets into a new, empty DIR and write DIR/SHA256SUMS
#
# Assets are repository files copied byte for byte from the tagged checkout: nothing is generated,
# stamped or edited, so the file on the server is the file reviewed at that tag (the compose file
# pins the image through the required SPOTDOWN_VERSION). None can hold a secret: all are committed,
# reviewed files, and .env and secrets/ are git-ignored. SHA256SUMS lists the others by name.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

assets=(
  "deploy/compose.yaml compose.yaml"
  "deploy/compose.no-ui.yaml compose.no-ui.yaml"
  "deploy/compose.spotify.yaml compose.spotify.yaml"
  "deploy/spotdown.env.example spotdown.env.example"
  "deploy/spotdown-ui.env.example spotdown-ui.env.example"
)

case "${1:-}" in
  list)
    printf '%s\n' "${assets[@]}"
    ;;
  build)
    dir="${2:?usage: release-assets.sh build DIR}"
    mkdir -p "$dir"
    [ -z "$(ls -A "$dir")" ] || { echo "release-assets: $dir is not empty" >&2; exit 1; }
    for a in "${assets[@]}"; do
      src="${a% *}"; name="${a#* }"
      # `sha256sum -- *` and the release's `$assets/*` glob skip dotfiles: a hidden asset (a .env,
      # say) would be uploaded by neither or checksummed by neither, so its name may not start
      # with a dot.
      [[ "$name" != .* && "$name" != */* ]] || { echo "release-assets: asset name '$name' must not start with a dot or hold a slash" >&2; exit 1; }
      [ -f "$root/$src" ] && [ ! -L "$root/$src" ] || { echo "release-assets: $src is not a regular file" >&2; exit 1; }
      cp "$root/$src" "$dir/$name"
    done
    ( cd "$dir" && sha256sum -- * > SHA256SUMS )
    ;;
  *)
    echo "usage: release-assets.sh list | build DIR" >&2
    exit 2
    ;;
esac
