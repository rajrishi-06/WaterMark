# PyInstaller build spec.
#
#     pip install pyinstaller && pyinstaller watermark.spec
#
# Produces a self-contained desktop build.  The bundled fonts are copied in
# under the package path that watermark.core.fonts already looks for inside
# sys._MEIPASS, so a frozen build finds them exactly as a wheel install does.

from PyInstaller.utils.hooks import collect_dynamic_libs

block_cipher = None

datas = [("watermark/assets/fonts", "watermark/assets/fonts")]

# tkinterdnd2 ships a Tcl extension that must travel with the binary.  It is
# optional: the app detects its absence and falls back to the file dialogs.
try:
    import tkinterdnd2
    import os

    datas.append((os.path.join(os.path.dirname(tkinterdnd2.__file__), "tkdnd"), "tkdnd"))
except ImportError:
    pass

analysis = Analysis(
    ["watermark_app.py"],
    pathex=["."],
    binaries=collect_dynamic_libs("PIL"),
    datas=datas,
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "scipy", "matplotlib", "pytest"],
    cipher=block_cipher,
)

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher=block_cipher)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="WaterMark",
    debug=False,
    strip=False,
    upx=True,
    console=False,          # no terminal window behind the GUI
)

collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.zipfiles,
    analysis.datas,
    strip=False,
    upx=True,
    name="WaterMark",
)
