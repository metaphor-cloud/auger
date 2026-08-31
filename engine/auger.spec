# PyInstaller build of the engine sidecar.
#
# One directory, not one file. A one-file build unpacks to a temporary directory at every
# start, and the unpacked files are unsigned, which fails notarisation under the hardened
# runtime on macOS.

from PyInstaller.utils.hooks import collect_dynamic_libs

# `sqlite-vec` is a loadable SQLite extension, so the package is a shim around a dylib
# that nothing imports and PyInstaller therefore never sees. Left out, the frozen engine
# starts and indexes and only says "search by meaning unavailable" once it is too late
# to notice. `sign-engine.sh` signs every Mach-O file under the engine, so this one is
# covered by the release as it stands.

a = Analysis(
    ["src/auger/__main__.py"],
    pathex=["src"],
    binaries=collect_dynamic_libs("sqlite_vec"),
    datas=[],
    hiddenimports=[
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
        # Imported where it is used, which is inside a function.
        "sqlite_vec",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="auger",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="engine",
)
