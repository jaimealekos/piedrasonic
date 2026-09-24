# Arquitectura — piedrasonic

Reproductor de IPTV para Windows sobre el protocolo **Xtream Codes**, con VLC
embebido de 64 bits: canales en directo, guía (EPG), favoritos, catch-up,
grabación y exportación M3U.

## 1. Visión general

La aplicación es una única ventana (CustomTkinter) con tres zonas: la columna de
categorías, la lista de canales y, a la derecha, el vídeo. Un clic en un canal lo
reproduce. Todo el estado de la cuenta (servidor, usuario, contraseña, favoritos,
preferencias) vive en `config.json`, fuera del código.

El flujo de arranque, en orden:

1. `settings.py` carga `config.json` (o pide la cuenta si es el primer arranque).
2. `xtream.py` se autentica contra el panel y pide la lista de canales por
   categorías, que se van pintando según llegan.
3. Al elegir un canal, `player.py` lo abre en el VLC embebido por HLS.

## 2. Módulos

| Módulo | Nivel | Responsabilidad |
|---|---|---|
| `iptv_player.pyw` | normal | Aplicación completa: ventana, listas, favoritos, catch-up, atajos (~1300 líneas). |
| `xtream.py` | normal | Cliente del protocolo Xtream Codes (autenticación, listas, EPG, catch-up). |
| `player.py` | normal | Reproductor VLC embebido y grabación. |
| `settings.py` | normal | Cuenta del usuario y rutas de datos; carga y guarda `config.json`. |
| `theme.py` | trivial | Colores y tipografías. |
| `registro.py` | trivial | Cuaderno CSV de conexiones para diagnóstico. |

## 3. Decisiones y trampas

Las razones detrás del código, que no se deducen leyéndolo:

- **VLC de 64 bits dentro del `.exe`**, que lo usa antes que el instalado: muchos
  PCs tienen el VLC de 32 bits y un proceso de 64 no puede cargar esa `libvlc.dll`.
  Ejecutando desde el código se busca en `PIEDRASONIC_VLC_DIR`, luego en
  `vendor\vlc` y por último el instalado, siempre que sea de 64 bits.
- **Vídeo siempre por `.m3u8` (HLS), nunca `.ts`**: en algunos paneles la variante
  `.ts` redirige a un dominio de abuso que resuelve a `127.0.0.1`; el `.m3u8` va al
  origen. `xtream.py` compara a dónde acaba cada variante y se queda con HLS.
- **La lista se pide por categorías y se pinta según llega** (goteo): el catálogo
  entero de una vez puede tardar minutos o no responder; por categorías responde en
  segundos. Se guardan las últimas listas y rotan.
- **El panel no habla HTTP correcto**: devuelve códigos atípicos (512 con JSON
  bueno, 404 vacío, 200 con texto en vez de JSON) y campos numéricos como cadenas
  (`auth` puede llegar como `"0"`). Por eso `xtream.py` **lee siempre el cuerpo** y
  el código de estado solo decide cuando el cuerpo viene vacío.
- **Regla de hilos Tk/VLC** (crítica): ningún hilo que no sea el de Tk toca los
  widgets (todo va por `after`), y el hilo de Tk nunca llama a nada de VLC que pueda
  bloquear. Las dos cosas causaron cuelgues totales de la aplicación.
- **Grabación**: al relanzar se usa `--sout-keep` + `set_media(None)`, nunca
  `stop()`, que trunca el fichero.

## 4. Configuración

Todo en `config.json`, junto al ejecutable (o en `%LOCALAPPDATA%\piedrasonic`
si ahí no se puede escribir), con valores por defecto documentados en
`settings.py`:

- **Cuenta Xtream**: servidor, usuario y contraseña. Se guardan **en texto claro**
  a propósito: así la configuración se lleva a otro ordenador copiando la
  carpeta. `config.json` no se versiona.
- **Preferencias**: favoritos y sus grupos, última lista, geometría de ventana,
  volumen y silencio, categorías ocultas.

## 5. Build

`pyinstaller piedrasonic.spec` produce una **carpeta** `dist\piedrasonic\` (no un
archivo suelto: los plugins de VLC en onefile se descomprimirían en cada arranque).
Necesita el VLC de 64 bits en `vendor\vlc`. El workflow de GitHub compila y sube el
zip al etiquetar una versión (`git tag vX.Y.Z && git push origin vX.Y.Z`).
