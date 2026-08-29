#!/usr/bin/env bash
#
# Put the Developer ID certificate into a keychain that this job alone can read.
#
# The runner is thrown away after the job, but the keychain is still made temporary and
# given no idle timeout, because a signature that waits behind a locked keychain fails
# with an error that names nothing.
#
# Reads APPLE_CERTIFICATE (a base64 .p12) and APPLE_CERTIFICATE_PASSWORD.

set -euo pipefail

: "${APPLE_CERTIFICATE:?APPLE_CERTIFICATE is not set}"
: "${APPLE_CERTIFICATE_PASSWORD:?APPLE_CERTIFICATE_PASSWORD is not set}"

keychain="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/auger-signing.keychain-db"
password="$(uuidgen)"
certificate="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/certificate.p12"

# The certificate never touches the log, and it leaves the disk before the job ends.
trap 'rm -f "${certificate}"' EXIT
printf '%s' "${APPLE_CERTIFICATE}" | base64 --decode > "${certificate}"

security create-keychain -p "${password}" "${keychain}"
security set-keychain-settings -lut 21600 "${keychain}"
security unlock-keychain -p "${password}" "${keychain}"
security import "${certificate}" -k "${keychain}" \
    -P "${APPLE_CERTIFICATE_PASSWORD}" \
    -T /usr/bin/codesign -T /usr/bin/security
# Without this, codesign stops for a dialogue that no one is there to answer.
security set-key-partition-list -S apple-tool:,apple:,codesign: \
    -s -k "${password}" "${keychain}" > /dev/null
security list-keychain -d user -s "${keychain}" login.keychain

echo "imported the signing certificate into ${keychain}"
security find-identity -v -p codesigning "${keychain}"
