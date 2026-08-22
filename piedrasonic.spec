# -*- mode: python ; coding: utf-8 -*-
"""Empaqueta piedrasonic para Windows CON VLC DENTRO.

    pyinstaller piedrasonic.spec

Por que se empaqueta VLC en vez de depender del instalado:

  El .exe funcionaba en la maquina de desarrollo y fallaba en otros PCs con
  "Failed to load dynlib/dll libvlc.dll". La causa habitual no es que falte VLC,
  es que el VLC instalado alli es de 32 bits (videolan.org ha servido el
  instalador x86 por defecto durante anos) y un ejecutable de 64 bits no puede
  cargar esa DLL. Tambien falla si VLC esta en una ruta no estandar o es
  portable, porque entonces no hay entrada en el registro.

  Llevando VLC dentro, el reproductor deja de depender por completo de lo que
  haya en el PC de destino. player._prepare_vlc() mira primero en el bundle.

Por que ONEDIR y no ONEFILE:

  Los plugins pesan ~110 MB. Un onefile los descomprimiria en un temporal EN
  CADA ARRANQUE (no solo el primero), lo que anade 10-20 s de espera cada vez
  que se abre el programa. Onedir arranca al instante. Se reparte comprimido en
  un .zip.
"""
import os

from PyInstaller.utils.hooks import collect_all

VLC_DIR = r"C:\Program Files\VideoLAN\VLC"

# Plugins que no aportan nada a este reproductor y solo abultan. El video se
# incrusta en nuestra propia ventana de Tk, asi que la interfaz de VLC sobra.
SKIP_PLUGINS = {
    "gui",                 # 18,9 MB de interfaz de VLC que nunca se muestra
    "visualization",       # visualizaciones de audio
    "services_discovery",  # descubrimiento de UPnP/SAP/etc
    "meta_engine",         # busqueda de caratulas y metadatos
    "lua",                 # scripts de lua
    "control",             # mandos a distancia, interfaz de red
    "keystore",            # almacen de credenciales de VLC
}

datas, binaries, hiddenimports = collect_all('customtkinter')
datas += [('icon.ico', '.'), ('star_on.png', '.'), ('star_off.png', '.')]

# --- VLC ---------------------------------------------------------------------
# Se anaden como DATAS y no como binaries a proposito: asi PyInstaller los copia
# tal cual, respetando la estructura de carpetas que libvlc espera, sin analizar
# sus dependencias ni reubicarlos. Los plugins DEBEN quedar en <vlc>/plugins.
if not os.path.isfile(os.path.join(VLC_DIR, "libvlc.dll")):
    raise SystemExit(
        f"No se encuentra libvlc.dll en {VLC_DIR}.\n"
        f"Instala VLC de 64 bits o corrige VLC_DIR en este .spec.")

for name in ("libvlc.dll", "libvlccore.dll"):
    datas.append((os.path.join(VLC_DIR, name), "vlc"))

_n = _bytes = 0
for root, _dirs, files in os.walk(os.path.join(VLC_DIR, "plugins")):
    rel = os.path.relpath(root, VLC_DIR)                 # p.ej. plugins\codec
    parts = rel.split(os.sep)
    if len(parts) > 1 and parts[1] in SKIP_PLUGINS:
        continue
    for f in files:
        if not f.lower().endswith(".dll"):
            continue
        src = os.path.join(root, f)
        datas.append((src, os.path.join("vlc", rel)))
        _n += 1
        _bytes += os.path.getsize(src)
print(f"[piedrasonic] VLC empaquetado: {_n} plugins, {_bytes / 1048576:.1f} MB")

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

# UPX desactivado: comprime mal las DLL de plugins de VLC (algunas dejan de
# cargar) y con 110 MB el empaquetado tardaria una eternidad.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='piedrasonic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='piedrasonic',
)
