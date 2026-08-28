# PyInstaller build of the engine sidecar.
#
# One directory, not one file. A one-file build unpacks to a temporary directory at every
# start, and the unpacked files are unsigned, which fails notarisation under the hardened
# runtime on macOS.

a = Analysis(
    ["src/reviewrig/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=["uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.on"],
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
    name="reviewrig-engine",
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
