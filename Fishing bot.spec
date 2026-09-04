# -*- mode: python ; coding: utf-8 -*-

import os
import PySide6

block_cipher = None

# Qt6Gui is linked against the newer MSVC helper DLLs.  PyInstaller detects
# the primary VCRUNTIME files but can omit these delay-loaded companions.
# Keep them beside the PySide modules so Windows always loads a matching set.
_pyside_dir = os.path.dirname(PySide6.__file__)
_msvc_runtime_dlls = (
    "concrt140.dll",
    "msvcp140_codecvt_ids.dll",
    "vcamp140.dll",
    "vccorlib140.dll",
    "vcomp140.dll",
)
_pyside_runtime_binaries = [
    (os.path.join(_pyside_dir, name), "PySide6")
    for name in _msvc_runtime_dlls
    if os.path.isfile(os.path.join(_pyside_dir, name))
]

datas = [
    ('src/data', 'data'),
    ('src/locale', 'locale'),
    ('src/gui/styles', 'gui/styles'),
    ('src/icon.ico', '.'),
    ('src/preferences.json', '.'),
    ('src/statistics.json', '.'),
]

a = Analysis(
    ['src/Fishing bot.py'],
    pathex=['src'],
    binaries=_pyside_runtime_binaries,
    datas=datas,
            hiddenimports=[
        'win32api',
        'win32con',
        'win32gui',
        'win32process',
        'multiprocessing',
        'i18n',
        'catches_data',
        'memory_bot',
        'player_buffs',
        'projectile_probe',
        'pymem',
        'gui',
        'gui.paths',
        'gui.prefs',
        'gui.theme',
        'gui.widgets',
        'gui.bridge',
        'gui.auto_drink',
        'gui.catch_tab',
        'gui.stats_tab',
        'gui.settings_tab',
        'gui.window',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'shiboken6',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['src/pyi_rth_qt_dll_dirs.py'],
    excludes=[
        'numpy',
        'pandas',
        'scipy',
        'matplotlib',
        'customtkinter',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.QtPositioning',
        'PySide6.QtBluetooth',
        'PySide6.QtSensors',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtRemoteObjects',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# The Python runtime may pull ICU copies into the bundle root.  Qt6Core then
# loads them before the system ICU and fails with WinError 127 (a procedure is
# missing).  This app does not use a package that requires those bundled ICU
# files, so let Qt use the Windows copy just as it does from source.
_conflicting_icu_dlls = {"icuuc.dll", "icudt78.dll"}
a.binaries = [
    entry for entry in a.binaries
    if os.path.basename(entry[0]).lower() not in _conflicting_icu_dlls
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Fishing bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='src/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Fishing bot',
)
