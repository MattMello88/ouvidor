# Uso: pyinstaller ouvidor.spec
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

a = Analysis(
    ["ouvidor/__main__.py"],
    pathex=["."],
    datas=collect_data_files("faster_whisper"),
    binaries=collect_dynamic_libs("ctranslate2"),
    excludes=["tkinter", "pytest", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtNetwork"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="Ouvidor", console=False, upx=False)
