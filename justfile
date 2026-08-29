# auger build tasks. Run `just` for the list.

root := justfile_directory()
# In development the host runs the engine from the checkout. In a bundle it runs the
# PyInstaller build from the resource directory.
engine_cmd := "uv run --project " + root + "/engine auger"
runtime := `command -v container || command -v podman || command -v docker || echo docker`
image := "auger/analysis:0.1"

default:
    @just --list

# Install every dependency.
setup:
    cd {{root}}/engine && uv sync
    cd {{root}}/app && pnpm install

# Run the application against the engine in this checkout.
dev:
    cd {{root}}/app && AUGER_ENGINE_CMD="{{engine_cmd}}" pnpm tauri dev

# Run the engine on its own. Useful with curl.
engine:
    cd {{root}}/engine && uv run auger

typecheck:
    cd {{root}}/engine && uv run mypy
    cd {{root}}/app && pnpm typecheck
    cd {{root}}/app/src-tauri && cargo clippy --all-targets -- -D warnings

test:
    cd {{root}}/engine && uv run pytest -q
    cd {{root}}/app && pnpm test
    cd {{root}}/app/src-tauri && cargo test

lint:
    cd {{root}}/engine && uv run ruff check .
    cd {{root}}/engine && uv run ruff format --check .
    cd {{root}}/app && pnpm lint
    cd {{root}}/app/src-tauri && cargo fmt --check

# Fix what can be fixed automatically.
fix:
    cd {{root}}/engine && uv run ruff check --fix .
    cd {{root}}/engine && uv run ruff format .
    cd {{root}}/app/src-tauri && cargo fmt

# The resolver the builder uses. Apple's `container` starts its builder with the
# gateway as its nameserver, and that gateway resolves nothing, so `apt-get update`
# inside a build fails with "Temporary failure resolving". The setting belongs to the
# builder, not to the build command. Podman and Docker inherit the host's resolver and
# ignore all of this.
build_dns := "1.1.1.1"

# Build the analysis image that every review step runs in. Needs network.
build-image:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ "$(basename {{runtime}})" = "container" ]; then
        if ! {{runtime}} run --rm docker.io/library/debian:bookworm-slim \
                getent hosts deb.debian.org >/dev/null 2>&1; then
            echo "the builder cannot resolve a name; restarting it with {{build_dns}}"
            {{runtime}} builder stop >/dev/null 2>&1 || true
            {{runtime}} builder start --dns {{build_dns}}
        fi
    fi
    cd {{root}}/sandbox && {{runtime}} build --tag {{image}} .

# Freeze the engine into app/src-tauri/binaries/engine.
build-sidecar:
    cd {{root}}/engine && uv run pyinstaller --clean --noconfirm \
        --distpath {{root}}/app/src-tauri/binaries \
        --workpath {{root}}/engine/build/pyinstaller \
        auger.spec

# Build the .app. It runs where it was built.
package: build-sidecar
    cd {{root}}/app && pnpm tauri build

# Build a signed and notarised .dmg, and the artefacts the updater needs.
#
# Needs an Apple Developer certificate:
#   APPLE_SIGNING_IDENTITY, APPLE_ID, APPLE_PASSWORD, APPLE_TEAM_ID
# and the updater key:
#   TAURI_SIGNING_PRIVATE_KEY, TAURI_SIGNING_PRIVATE_KEY_PASSWORD
release: build-sidecar
    #!/usr/bin/env bash
    set -euo pipefail
    for name in APPLE_SIGNING_IDENTITY APPLE_ID APPLE_PASSWORD APPLE_TEAM_ID \
                TAURI_SIGNING_PRIVATE_KEY; do
        if [ -z "${!name:-}" ]; then
            echo "${name} is not set. See docs/install.md." >&2
            exit 1
        fi
    done
    # The bundler signs the .app, but not the Mach-O files inside a resource. It calls
    # codesign without --deep, so the frozen engine is signed here first.
    {{root}}/scripts/sign-engine.sh
    cd {{root}}/app && pnpm tauri build --bundles app,dmg \
        --config src-tauri/tauri.release.conf.json

# Check that the built bundle is signed all the way down, and that Gatekeeper takes it.
verify:
    #!/usr/bin/env bash
    set -euo pipefail
    bundle={{root}}/app/src-tauri/target/release/bundle
    codesign --verify --deep --strict --verbose=2 "${bundle}/macos/Auger.app"
    # --deep verifies what --deep signing would have covered, so an unsigned library
    # under Resources/engine fails here rather than at notarisation.
    spctl --assess --type execute --verbose=4 "${bundle}/macos/Auger.app"
    codesign --display --entitlements - \
        "${bundle}/macos/Auger.app/Contents/Resources/engine/auger"

clean:
    rm -rf {{root}}/app/src-tauri/binaries {{root}}/engine/build {{root}}/engine/dist {{root}}/app/dist
