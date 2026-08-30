"""Write the manifest the updater reads.

The updater asks GitHub for `latest.json`, compares its `version` with the running one,
and checks the archive against the signature with the public key in `tauri.conf.json`.
The manifest is therefore the release: an archive with no entry here reaches nobody.

Usage: write-latest-json.py <version> <bundle directory> <output file>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The engine is a PyInstaller build for Apple silicon, so the bundle is arm64 only. An
# Intel Mac gets no update from here, and must not be offered one.
TARGET = "darwin-aarch64"
REPOSITORY = "metaphor-cloud/auger"


def main() -> int:
    version, bundle, output = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])

    archives = sorted(bundle.glob("macos/*.app.tar.gz"))
    if len(archives) != 1:
        print(f"expected one update archive under {bundle}, found {archives}", file=sys.stderr)
        return 1
    archive = archives[0]

    signature = archive.with_suffix(archive.suffix + ".sig")
    if not signature.is_file():
        print(f"no signature beside {archive}. Is TAURI_SIGNING_PRIVATE_KEY set?", file=sys.stderr)
        return 1

    manifest = {
        "version": version,
        "notes": f"https://github.com/{REPOSITORY}/releases/tag/v{version}",
        "pub_date": None,
        "platforms": {
            TARGET: {
                "signature": signature.read_text().strip(),
                "url": (
                    f"https://github.com/{REPOSITORY}/releases/download/"
                    f"v{version}/{archive.name}"
                ),
            }
        },
    }
    # `pub_date` is optional, and an empty one is worse than none.
    del manifest["pub_date"]

    output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {output} for {archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
