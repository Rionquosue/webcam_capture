# Build with: python -m PyInstaller --noconfirm --clean webcam_capture.spec
from pathlib import Path

project = Path(SPECPATH)
analysis = Analysis(
    [str(project / "webcam_capture.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[(str(project / "assets" / "webcam_capture.ico"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="WebcamCapture",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(project / "assets" / "webcam_capture.ico"),
)
bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="WebcamCapture",
)
