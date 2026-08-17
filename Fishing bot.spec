# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

datas = [
    ('src/data', 'data'),
    ('src/locale', 'locale'),
    ('src/icon.ico', '.'),
    ('src/preferences.json', '.'),
    ('src/statistics.json', '.'),
]
datas += collect_data_files('customtkinter')

a = Analysis(
    ['src/Fishing bot.py'],
    pathex=['src'],
    binaries=[],
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
        'projectile_probe',
        'pymem',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'numpy',
        'pandas',
        'scipy',
        'matplotlib',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

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
