# reviewrig build tasks. Run `just` for the list.

root := justfile_directory()
# In development the host runs the engine from the checkout. In a bundle it runs the
# PyInstaller build from the resource directory.
engine_cmd := "uv run --project " + root + "/engine reviewrig-engine"

default:
    @just --list

# Install every dependency.
setup:
    cd {{root}}/engine && uv sync
    cd {{root}}/app && pnpm install

# Run the application against the engine in this checkout.
dev:
    cd {{root}}/app && REVIEWRIG_ENGINE_CMD="{{engine_cmd}}" pnpm tauri dev

# Run the engine on its own. Useful with curl.
engine:
    cd {{root}}/engine && uv run reviewrig-engine

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

# Freeze the engine into app/src-tauri/binaries/engine.
build-sidecar:
    cd {{root}}/engine && uv run pyinstaller --clean --noconfirm \
        --distpath {{root}}/app/src-tauri/binaries \
        --workpath {{root}}/engine/build/pyinstaller \
        reviewrig-engine.spec

# Build the .app and the .dmg.
package: build-sidecar
    cd {{root}}/app && pnpm tauri build

clean:
    rm -rf {{root}}/app/src-tauri/binaries {{root}}/engine/build {{root}}/engine/dist {{root}}/app/dist
