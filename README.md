<p align="center">
  <img src="screenshots/banner.png" alt="piedrasonic" width="820">
</p>

<p align="center">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-10%2F11-0a84ff">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776ab">
  <img alt="Release" src="https://img.shields.io/github/v/release/jaimealekos/piedrasonic?color=f2c24c">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-30d158">
</p>

Reproductor de IPTV para Windows. Protocolo **Xtream Codes**, vídeo con VLC
embebido, EPG, favoritos con grupos y catch-up. Lleva VLC dentro: se descomprime
y funciona, sin instalar nada.

![Liga de Campeones](screenshots/liga-campeones.png)
![LaLiga](screenshots/laliga-sat.png)

## Descargar

Baja `piedrasonic-windows-x64.zip` de
**[Releases](https://github.com/jaimealekos/piedrasonic/releases/latest)**,
descomprímelo **entero** y ejecuta `piedrasonic.exe`.

No necesita Python **ni VLC**: el reproductor lleva su propio VLC de 64 bits
dentro, así que da igual lo que haya instalado en el PC (el motivo es que el
instalador que reparte videolan.org es de 32 bits desde hace años, y un
programa de 64 no puede cargar esa versión). Windows 10/11 de 64 bits.

Dos cosas de Windows que conviene saber:

- No saques `piedrasonic.exe` de la carpeta: la carpeta `_internal` que tiene al
  lado es el programa. Si quieres un acceso directo, créalo (clic derecho →
  *Enviar a* → *Escritorio*), no muevas el `.exe`.
- La primera vez SmartScreen avisa de que no está firmado:
  *Más información* → *Ejecutar de todas formas*.

## Uso

En el primer arranque introduces **servidor, usuario y contraseña**. Se validan
contra el panel y se guardan cifradas con DPAPI de Windows (`config.json`, que no
se comparte).

- **Un clic** en un canal lo reproduce.
- **⟳ Actualizar** (o `F5`) vuelve a pedir la lista de canales. La lista ya se
  actualiza sola en cada arranque —de hecho se pide antes incluso de dibujar la
  ventana—; el botón es para cuando el servidor cambia algo a media sesión.
  Debajo del buscador se ve siempre de cuándo es la lista, y en rojo si el
  servidor no ha respondido.
- **★ estrella** = favorito. En *Favoritos* se agrupan y ordenan (clic derecho o
  desplegable *Mover a*; multiselección con Ctrl/Shift).
- **Doble clic** en el vídeo o `F11` = pantalla completa.
- **`L`** (o **◧** en la barra de controles) = ocultar las dos columnas de
  la izquierda y dejar solo el vídeo; la ventana se encoge sola para que la
  imagen no cambie de tamaño ni de sitio. Otra vez y vuelven.
- **`M`** (o **🔊**) = silenciar / recuperar. El volumen y el silencio se
  recuerdan entre sesiones.
- **`A`** (o **📌**) = ventana siempre encima.
- **`S`** (o **📷**) = captura del fotograma (al *Escritorio*).
- **Catch-up** (`-30m … -24h`) en los canales que lo soportan; con la caja
  `min` saltas los minutos que quieras (`◀` atrás, `▶` adelante; llegar a 0
  vuelve al directo).
- **⚙ Cuenta** (abajo) para cambiar de servidor/credenciales.
- **⚙** (arriba) para mostrar/ocultar categorías.
- **Exportar M3U** para VLC/Kodi/TiviMate.

## Desde el código

```bash
pip install -r requirements.txt
pythonw iptv_player.pyw      # o doble clic en run.bat
```

Requiere Python 3.12. Ejecutado desde el código sí usa el VLC de la máquina; si
el tuyo es de 32 bits, apunta `PYTHON_VLC_LIB_PATH` a un `libvlc.dll` de 64.

## Compilar

Hace falta un VLC de 64 bits del que copiar. Baja el zip oficial de
[get.videolan.org](https://get.videolan.org/vlc/last/win64/) y descomprímelo en
`vendor\vlc`, de forma que queden `vendor\vlc\libvlc.dll` y
`vendor\vlc\plugins\`.

```bash
pip install -r requirements.txt pyinstaller
pyinstaller piedrasonic.spec     # -> dist\piedrasonic\
```

Sale una **carpeta**, no un archivo suelto: los 110 MB de plugins de VLC en un
onefile se descomprimirían en un temporal en cada arranque, no solo el primero.
Para repartirlo, comprime `dist\piedrasonic` en un `.zip`.

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

El ejecutable incluye [VLC](https://www.videolan.org/vlc/) (libvlc y sus
plugins), de VideoLAN, bajo LGPL 2.1+ / GPL 2+. Su licencia viaja dentro del
paquete, en `_internal\vlc\COPYING.txt`.
