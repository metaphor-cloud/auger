#!/usr/bin/env bash
#
# Sign the frozen engine before Tauri builds the bundle.
#
# The engine reaches the bundle through `resources`, not `externalBin`, so the bundler
# treats it as data and does not sign what is inside it. macOS does. A PyInstaller build
# holds more than eighty Mach-O files, and notarisation refuses the bundle if one of them
# carries no signature.
#
# The engine is also its own process. Entitlements belong to a process, not to a bundle,
# so the `auger` executable carries the entitlements that let it load those libraries.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
engine="${root}/app/src-tauri/binaries/engine"
entitlements="${root}/app/src-tauri/entitlements.plist"

if [ -z "${APPLE_SIGNING_IDENTITY:-}" ]; then
    echo "APPLE_SIGNING_IDENTITY is not set. See docs/install.md." >&2
    exit 1
fi
# The directory is in git, so it exists even when nothing has been built into it. The
# executable is the thing that proves a build happened.
if [ ! -x "${engine}/auger" ]; then
    echo "no engine at ${engine}/auger. Run 'just build-sidecar' first." >&2
    exit 1
fi

# Deepest path first. A signature over a directory seals what it holds, so a file signed
# after its parent breaks the parent's seal.
signed=0
while IFS= read -r -d '' file; do
    if file --brief "${file}" | grep --quiet "Mach-O"; then
        codesign --force --timestamp --options runtime \
            --sign "${APPLE_SIGNING_IDENTITY}" "${file}"
        signed=$((signed + 1))
    fi
done < <(find "${engine}" -depth -type f -print0)

# The engine process loads those libraries, so it is the process that needs the
# entitlement. Sign it last, over the libraries it holds.
codesign --force --timestamp --options runtime \
    --entitlements "${entitlements}" \
    --sign "${APPLE_SIGNING_IDENTITY}" "${engine}/auger"

echo "signed ${signed} Mach-O files under ${engine}"
