# -*- mode: python ; coding: utf-8 -*-
"""Empaqueta piedrasonic para Windows CON VLC DENTRO.

    pyinstaller piedrasonic.spec        ->  dist\\piedrasonic\\

Por que se empaqueta VLC en vez de depender del instalado:

  El .exe funcionaba en la maquina de desarrollo y fallaba en otros PCs con
  "Failed to load dynlib/dll libvlc.dll". La causa habitual no es que falte VLC,
  es que el VLC instalado alli es de 32 bits (videolan.org ha servido el
  instalador x86 por defecto durante anos) y un ejecutable de 64 bits no puede
  cargar esa DLL. Tambien falla si VLC esta en una ruta no estandar o es
  portable, porque entonces no hay entrada en el registro.

  Llevando VLC dentro, el reproductor deja de depender por completo de lo que
  haya en el PC de destino. player._prepare_vlc() mira primero en el bundle.

De donde sale el VLC que se empaqueta, por orden:

  1. PIEDRASONIC_VLC_DIR, si apunta a una carpeta con libvlc.dll.
  2. vendor\\vlc dentro del repo. Es lo que usan la build local y la de GitHub
     Actions (que lo descomprime ahi desde el zip oficial). No se versiona.
  3. El VLC instalado en la maquina, SI es de 64 bits.

  Si no aparece ninguno se para con instrucciones, en vez de generar un .exe
  que compila bien y luego no reproduce nada.

Por que ONEDIR y no ONEFILE:

  Los plugins pesan ~110 MB. Un onefile los descomprimiria en un temporal EN
  CADA ARRANQUE (no solo el primero), lo que anade 10-20 s de espera cada vez
  que se abre el programa. Onedir arranca al instante. Se reparte comprimido en
  un .zip.
"""
import os
import struct

from PyInstaller.utils.hooks import collect_all

BITS = 8 * struct.calcsize("P")

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


def _dll_bits(path):
    """32 o 64 segun la cabecera PE, o None si no se puede leer.

    Se comprueba al empaquetar y no al ejecutar porque meter un VLC de 32 bits
    en un .exe de 64 no da ningun error aqui: lo da en el PC del usuario.
    """
    try:
        with open(path, "rb") as fh:
            fh.seek(0x3C)
            pe = struct.unpack("<I", fh.read(4))[0]
            fh.seek(pe + 4)
            machine = struct.unpack("<H", fh.read(2))[0]
    except (OSError, struct.error):
        return None
    return {0x014C: 32, 0x8664: 64, 0xAA64: 64}.get(machine)


def _vlc_candidates():
    yield os.environ.get("PIEDRASONIC_VLC_DIR")
    yield os.path.join(SPECPATH, "vendor", "vlc")
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (r"SOFTWARE\VideoLAN\VLC",
                        r"SOFTWARE\WOW6432Node\VideoLAN\VLC"):
                try:
                    with winreg.OpenKey(root, sub) as k:
                        yield winreg.QueryValueEx(k, "InstallDir")[0]
                except OSError:
                    pass
    except ImportError:
        pass
    for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            yield os.path.join(base, "VideoLAN", "VLC")


def _find_vlc():
    seen, wrong = set(), []
    for d in _vlc_candidates():
        if not d or d in seen:
            continue
        seen.add(d)
        if not os.path.isfile(os.path.join(d, "libvlc.dll")):
            continue
        bits = _dll_bits(os.path.join(d, "libvlc.dll"))
        if bits and bits != BITS:
            wrong.append((d, bits))
            continue
        if not os.path.isdir(os.path.join(d, "plugins")):
            continue                      # sin plugins no decodifica nada
        return d
    detalle = "".join(f"\n  - {d} es de {b} bits" for d, b in wrong)
    raise SystemExit(
        f"No se encuentra un VLC de {BITS} bits del que copiar.{detalle}\n\n"
        f"Baja el zip oficial de 64 bits y descomprimelo en vendor\\vlc:\n\n"
        f"  https://get.videolan.org/vlc/last/win64/\n\n"
        f"de forma que quede vendor\\vlc\\libvlc.dll y vendor\\vlc\\plugins\\.\n"
        f"O apunta PIEDRASONIC_VLC_DIR a una instalacion de {BITS} bits.")


VLC_DIR = _find_vlc()

datas, binaries, hiddenimports = collect_all('customtkinter')
datas += [('icon.ico', '.'), ('star_on.png', '.'), ('star_off.png', '.')]

# --- VLC ---------------------------------------------------------------------
# Se anaden como DATAS y no como binaries a proposito: asi PyInstaller los copia
# tal cual, respetando la estructura de carpetas que libvlc espera, sin analizar
# sus dependencias ni reubicarlos. Los plugins DEBEN quedar en <vlc>/plugins.
for name in ("libvlc.dll", "libvlccore.dll"):
    datas.append((os.path.join(VLC_DIR, name), "vlc"))

# VLC es software libre de otra gente: se reparte con su licencia al lado.
for name in ("COPYING.txt", "AUTHORS.txt"):
    src = os.path.join(VLC_DIR, name)
    if os.path.isfile(src):
        datas.append((src, "vlc"))

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
if not _n:
    raise SystemExit(f"{VLC_DIR}\\plugins no tiene ni una DLL: copia incompleta.")
print(f"[piedrasonic] VLC de {VLC_DIR}: {_n} plugins, {_bytes / 1048576:.1f} MB")

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
