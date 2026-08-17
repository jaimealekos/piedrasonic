# piedrasonic

Reproductor de IPTV ligero para Windows. Protocolo **Xtream Codes**, vídeo con
VLC embebido, EPG, favoritos con grupos y catch-up.

![Liga de Campeones](screenshots/liga-campeones.png)
![LaLiga](screenshots/laliga-sat.png)

## Requisitos

- Windows 10/11
- Python 3.12
- VLC 64-bit
- `pip install -r requirements.txt`

## Uso

Doble clic en **`run.bat`** (o `pythonw iptv_player.pyw`).

En el primer arranque pide **servidor, usuario y contraseña**. Se validan contra
el panel y se guardan cifradas con DPAPI de Windows (`config.json`, que no se
comparte).

- **Un clic** en un canal lo reproduce.
- **★ estrella** = favorito. En *Favoritos* se agrupan y ordenan (clic derecho o
  desplegable *Mover a*; multiselección con Ctrl/Shift).
- **Doble clic** en el vídeo o `F11` = pantalla completa.
- **Catch-up** (`-30m … -24h`) en los canales que lo soportan.
- **⚙ Cuenta** (abajo) para cambiar de servidor/credenciales.
- **⚙** (arriba) para mostrar/ocultar categorías.
- **Exportar M3U** para VLC/Kodi/TiviMate.

## Estructura

| Archivo | Función |
|---------|---------|
| `iptv_player.pyw` | Aplicación |
| `xtream.py` | Cliente del protocolo Xtream |
| `player.py` | Reproductor VLC embebido |
| `settings.py` | Cuenta + cifrado (DPAPI) |
| `theme.py` | Tema visual |
| `config.json` | Se crea al iniciar sesión (privado, no se sube) |

## Licencia

MIT.
