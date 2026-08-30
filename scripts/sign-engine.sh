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

sign() {
    codesign --force --timestamp --options runtime \
        --sign "${APPLE_SIGNING_IDENTITY}" "$@"
}

# Loose Mach-O files first, and nothing inside a framework. Deepest path first, because a
# signature over a directory seals what it holds, and a file signed after its parent
# breaks the parent's seal.
#
# A file here may be a hard link to the binary inside a framework, which is why the
# frameworks are sealed after this pass and not before it.
signed=0
while IFS= read -r -d '' file; do
    if file --brief "${file}" | grep --quiet "Mach-O"; then
        sign "${file}"
        signed=$((signed + 1))
    fi
done < <(find "${engine}" -depth -type f -not -path '*.framework/*' -print0)

# A framework is a bundle, not a file. codesign has to be given the directory so that it
# writes the _CodeSignature the loader reads; a signature on the bare Mach-O inside is a
# different thing, and Apple's notary service rejects it with "the signature of the binary
# is invalid". Which Python a build gets decides whether this matters: a framework build
# ships Python.framework here, and a plain one ships libpython as an ordinary dylib.
frameworks=0
while IFS= read -r -d '' framework; do
    sign "${framework}"
    frameworks=$((frameworks + 1))
done < <(find "${engine}" -depth -type d -name '*.framework' -print0)

# The engine process loads those libraries, so it is the process that needs the
# entitlement. Sign it last, over the libraries it holds.
codesign --force --timestamp --options runtime \
    --entitlements "${entitlements}" \
    --sign "${APPLE_SIGNING_IDENTITY}" "${engine}/auger"

echo "signed ${signed} Mach-O files and ${frameworks} frameworks under ${engine}"
