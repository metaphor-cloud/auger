#!/usr/bin/env bash
#
# Put the Developer ID certificate into a keychain that this job alone can read.
#
# The runner is thrown away after the job, but the keychain is still made temporary and
# given no idle timeout, because a signature that waits behind a locked keychain fails
# with an error that names nothing.
#
# Reads APPLE_CERTIFICATE (a base64 .p12) and APPLE_CERTIFICATE_PASSWORD, and writes
# APPLE_SIGNING_IDENTITY and APPLE_TEAM_ID to GITHUB_ENV.
#
# Those two are read out of the certificate rather than configured. They are not secret,
# they are printed in every signature, and a second copy in a settings page is one more
# thing to get wrong: the release that found this had the certificate and neither name.

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

# `find-identity` prints:  1) <hash> "Developer ID Application: Name (TEAMID)"
# Only a Developer ID Application certificate can sign for distribution, so a .p12 that
# holds a development one as well cannot pick the wrong half.
identity="$(security find-identity -v -p codesigning "${keychain}" \
    | sed -n 's/.*"\(Developer ID Application: .*\)".*/\1/p' | head -1)"

if [ -z "${identity}" ]; then
    echo "the certificate holds no Developer ID Application identity." >&2
    echo "Export it from Keychain Access under My Certificates. See docs/install.md." >&2
    exit 1
fi

# The team is the parenthesised code at the end of the identity, and nowhere else.
team="$(printf '%s' "${identity}" | sed -n 's/.*(\([A-Z0-9]\{10\}\))$/\1/p')"
if [ -z "${team}" ]; then
    echo "no team ID in '${identity}'." >&2
    exit 1
fi

echo "signing as ${identity}"
if [ -n "${GITHUB_ENV:-}" ]; then
    {
        echo "APPLE_SIGNING_IDENTITY=${identity}"
        echo "APPLE_TEAM_ID=${team}"
    } >> "${GITHUB_ENV}"
fi
