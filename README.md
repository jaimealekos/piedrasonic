<p align="center">
  <img src="screenshots/banner.png" alt="piedrasonic" width="820">
</p>

<p align="center">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-10%2F11-0a84ff">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776ab">
  <img alt="Release" src="https://img.shields.io/github/v/release/jaimealekos/piedrasonic?color=f2c24c">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-30d158">
</p>

Reproductor de IPTV ligero para Windows. Protocolo **Xtream Codes**, vídeo con
VLC embebido, EPG, favoritos con grupos y catch-up.

![Liga de Campeones](screenshots/liga-campeones.png)
![LaLiga](screenshots/laliga-sat.png)

## Descargar

Baja el `.exe` desde **[Releases](https://github.com/jaimealekos/piedrasonic/releases/latest)**
y ejecútalo. No necesita Python. Requiere **[VLC 64-bit](https://www.videolan.org/vlc/)**
instalado.

## Uso

En el primer arranque introduces **servidor, usuario y contraseña**. Se validan
contra el panel y se guardan cifradas con DPAPI de Windows (`config.json`, que no
se comparte).

- **Un clic** en un canal lo reproduce.
- **★ estrella** = favorito. En *Favoritos* se agrupan y ordenan (clic derecho o
  desplegable *Mover a*; multiselección con Ctrl/Shift).
- **Doble clic** en el vídeo o `F11` = pantalla completa.
- **Catch-up** (`-30m … -24h`) en los canales que lo soportan.
- **⚙ Cuenta** (abajo) para cambiar de servidor/credenciales.
- **⚙** (arriba) para mostrar/ocultar categorías.
- **Exportar M3U** para VLC/Kodi/TiviMate.

## Desde el código

```bash
pip install -r requirements.txt
pythonw iptv_player.pyw      # o doble clic en run.bat
```

Requiere Python 3.12 y VLC 64-bit.

## Compilar el .exe

```bash
pip install pyinstaller
pyinstaller piedrasonic.spec     # -> dist/piedrasonic.exe
```

## Estructura

| Archivo | Función |
|---------|---------|
| `iptv_player.pyw` | Aplicación |
| `xtream.py` | Cliente del protocolo Xtream |
| `player.py` | Reproductor VLC embebido |
| `settings.py` | Cuenta + cifrado (DPAPI) |
| `theme.py` | Tema visual |
| `piedrasonic.spec` | Build de PyInstaller |

## Licencia

MIT.
