#!/usr/bin/env bash
#
# Put the App Store Connect API key on disk for notarytool.
#
# `notarytool` reads the key from a file, not from a variable, so CI has to write one.
# The key is a private key: it goes to a directory the runner throws away, never to the
# workspace, and never to the log.
#
# Reads APPLE_API_KEY (the ten-character key ID) and APPLE_API_KEY_BASE64 (the .p8 file,
# base64 encoded). Writes the path to GITHUB_ENV as APPLE_API_KEY_PATH.

set -euo pipefail

: "${APPLE_API_KEY:?APPLE_API_KEY is not set. It is the key ID, such as 2X9R4HXF34}"
: "${APPLE_API_KEY_BASE64:?APPLE_API_KEY_BASE64 is not set}"

directory="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/private_keys"
key="${directory}/AuthKey_${APPLE_API_KEY}.p8"

mkdir -p "${directory}"
chmod 700 "${directory}"
# The file is created empty and locked down before anything secret reaches it.
: > "${key}"
chmod 600 "${key}"
printf '%s' "${APPLE_API_KEY_BASE64}" | base64 --decode > "${key}"

if ! grep --quiet "BEGIN PRIVATE KEY" "${key}"; then
    echo "the decoded key is not a PEM private key. Is APPLE_API_KEY_BASE64 the .p8?" >&2
    exit 1
fi

echo "wrote the notarisation key to ${key}"
if [ -n "${GITHUB_ENV:-}" ]; then
    echo "APPLE_API_KEY_PATH=${key}" >> "${GITHUB_ENV}"
fi
