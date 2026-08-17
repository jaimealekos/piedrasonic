# -*- mode: python ; coding: utf-8 -*-
# Empaqueta piedrasonic en un único .exe (Windows).
#   pyinstaller piedrasonic.spec
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all('customtkinter')
datas += [('icon.ico', '.'), ('star_on.png', '.'), ('star_off.png', '.')]

a = Analysis(
    ['iptv_player.pyw'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='piedrasonic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
