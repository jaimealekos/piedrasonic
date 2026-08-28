# Bitácora de piedrasonic

Este fichero es el punto de entrada al proyecto. Si eres una persona que vuelve
después de un tiempo, o una sesión nueva de Claude Code que empieza de cero,
léelo entero antes de tocar nada: cuenta qué es esto, cómo está hecho, qué
trampas tiene y en qué punto estamos.

Última actualización: **28 de agosto de 2026**.

---

## 1. Qué es piedrasonic

Un reproductor de televisión por internet para Windows. El usuario mete la
dirección de su servidor, su usuario y su contraseña, y ve la lista de canales a
la izquierda y el vídeo a la derecha. Un clic en un canal y se ve.

Habla el protocolo **Xtream Codes**, que es el que usan casi todos los
servidores de IPTV: una API en `/player_api.php` que devuelve JSON con las
categorías y los canales, y unas URL de vídeo del estilo
`/live/USUARIO/CLAVE/12345.m3u8`.

Lo que lo diferencia de otros: **lleva VLC dentro**. El usuario descomprime una
carpeta y funciona, sin instalar Python ni VLC ni nada.

Repositorio: https://github.com/jaimealekos/piedrasonic · Licencia MIT.

## 2. Cómo se ejecuta

Desde el código, con Python 3.12:

```bash
pip install -r requirements.txt
pythonw iptv_player.pyw
```

Empaquetado, el usuario final descomprime el `.zip` y ejecuta
`piedrasonic.exe`. Sale una **carpeta**, no un ejecutable suelto: los 110 MB de
plugins de VLC en un «onefile» se descomprimirían en un temporal en cada
arranque, no solo el primero.

## 3. Cómo está hecho por dentro

| Fichero | Qué hace |
|---|---|
| `iptv_player.pyw` | La aplicación entera: ventana, listas, favoritos, catch-up, atajos de teclado. Es el fichero grande (~1300 líneas). La clase es `LiveApp`. |
| `xtream.py` | El cliente del protocolo. Habla con el servidor y devuelve datos limpios. Clase `XtreamClient`. |
| `player.py` | El reproductor de vídeo: encuentra VLC, lo carga y lo mete dentro de la ventana. Los controles translúcidos que aparecen al pasar el ratón también están aquí. |
| `settings.py` | La cuenta del usuario, cifrada, y dónde se guardan los datos. |
| `theme.py` | Colores y tipografías. Un diccionario `C` y poco más. |
| `piedrasonic.spec` | La receta de PyInstaller para construir el `.exe`. |
| `.github/workflows/build.yml` | Construye y publica la release cuando se empuja una etiqueta `v*`. |

Los `make_*.py` (icono, banner, estrellas) son utilidades que se ejecutaron una
vez para generar imágenes. No forman parte del programa.

### Dónde viven los datos del usuario

Junto al ejecutable si se puede escribir ahí; si no —caso típico: descomprimido
en *Archivos de programa*— en `%LOCALAPPDATA%\piedrasonic`. Se comprueba
**escribiendo un fichero de verdad**, porque en Windows `os.access` miente
cuando está de por medio la virtualización de UAC.

- `config.json` — la cuenta, los favoritos, el volumen, la geometría de la
  ventana. **Nunca se sube al repositorio.**
- `cache.json` — la última lista de canales descargada, para que el arranque sea
  instantáneo. Tampoco se sube.

La contraseña se cifra con **DPAPI de Windows**, que la ata a esa máquina y a
ese usuario: copiar el `config.json` a otro PC no sirve de nada.

## 4. Las cuatro cosas que hay que saber antes de tocar nada

Son cuatro fallos que costaron mucho encontrar porque los cuatro daban el mismo
síntoma: *«no se ve nada»*. Están resueltos, pero si algo se rompe otra vez, es
casi seguro que será por aquí.

**1. VLC tiene que ser de 64 bits.** La página de VideoLAN sirvió el instalador
de 32 bits por defecto durante años, y un programa de 64 no puede cargar esa
biblioteca. Por eso el `.exe` lleva su propio VLC dentro y comprueba la
arquitectura leyendo la cabecera del fichero, para poder decir «tu VLC es de 32
bits» en vez de soltar un error de `ctypes` que no explica nada.

**2. El servidor no habla HTTP correcto.** Comprobado petición a petición contra
el panel real: responde **512** llevando el JSON bueno en el cuerpo, **404 con
el cuerpo vacío** si el usuario no existe, **200 con texto plano** si la
contraseña falla, y manda `auth` como la cadena `"0"`, no como el número `0`.
Por eso `xtream.py` mira siempre el cuerpo de la respuesta, venga con el código
que venga, y nunca compara `auth` con un entero.

**3. El vídeo va por HLS (`.m3u8`), no por `.ts`.** Pedir un canal en `.ts`
devolvía una redirección a `cloudflare-terms-of-service-abuse.com`, que resuelve
a `127.0.0.1`: el reproductor terminaba conectándose a sí mismo. Cloudflare no
permite repartir televisión por su CDN y corta el flujo. El `.m3u8` va al
servidor de origen directamente y funciona. Al cargar la lista se **sondea** un
canal una vez por sesión para confirmarlo, y si el formato configurado no da
vídeo pero el otro sí, se cambia solo y se recuerda. Las redirecciones se siguen
a mano en esa sonda **a propósito**: siguiéndolas sin mirar, el corte del CDN
llega disfrazado de error de red y parece cosa del PC del usuario.

**4. La lista tiene que refrescarse en cada arranque.** Hubo una versión en la
que, si existía `cache.json`, el programa no volvía a contactar con el servidor
**nunca**. La interfaz enseñaba la lista vieja y todo parecía normal hasta
pulsar un canal. Ahora la caché se pinta al instante y la descarga ocurre en
segundo plano siempre, con un aviso permanente bajo el buscador que dice de
cuándo es la lista.

## 5. Rendimiento: lo que ya está medido

No repitas estas pruebas, ya están hechas. Actualizar la lista tarda unos **28
segundos** y **27,8 de esos 28 son una sola llamada**, `get_live_streams`, y
dentro de ella una sola categoría, «Latinos», que trae 1382 de los 1737 canales.
Las otras nueve responden en 0,33-0,38 s cada una.

Descartado por medición, no por intuición:

- **Comprimir con gzip** baja la lista de 522 kB a 48 kB y no quita ni un
  segundo (27,78 s con gzip, 27,91 s sin él). Se ha dejado puesto porque en una
  conexión lenta sí ayudaría, pero aquí el cuello de botella es lo que tarda el
  panel en generar la respuesta, no la transferencia.
- **Partir la petición por categorías** y lanzarlas a la vez no arregla nada:
  esa categoría sigue tardando lo mismo.
- **Pintar la interfaz** no es el problema: meter los 1737 canales en la tabla
  cuesta 12 ms e indexarlos 1 ms.
- **Lanzar login, categorías y canales a la vez** sí ayudó, pero poco: 0,6 s de
  28,4.

Sigue abierto. Cualquier arreglo tiene que atacar esa categoría concreta.

## 6. Cómo se compila y se publica

Para compilar hace falta un VLC de 64 bits del que copiar, descomprimido en
`vendor\vlc` (esa carpeta no se versiona, son 110 MB):

```bash
pyinstaller piedrasonic.spec
```

El `.spec` busca VLC en `PIEDRASONIC_VLC_DIR`, en `vendor\vlc` y en la
instalación del sistema, por ese orden, y **se para con instrucciones** si no
encuentra uno de 64 bits, en vez de generar un `.exe` que compila bien y luego
no reproduce nada.

Para publicar una versión basta con empujar una etiqueta:

```bash
git tag v1.4.0 && git push origin v1.4.0
```

El workflow baja VLC 3.0.23 verificando su SHA256, compila, comprime y sube
`piedrasonic-windows-x64.zip` a la release. **Ese nombre importa**: es el que el
README le dice al usuario que descargue.

## 7. Dónde estamos ahora mismo

Rama de trabajo: **`fix/vlc-bundle-and-playlist-refresh`**, con **4 commits sin
empujar**. La PR #1 está abierta pero solo enseña el primero de los cinco.

Tres cosas pendientes, por orden de urgencia:

1. **Empujar los cuatro commits.** La PR muestra el trabajo de hace seis días.
2. **La release v1.3.0 está coja.** Su compilación automática falló (el servidor
   de GitHub no tenía VLC y el `.spec` abortó, que es justo lo que debía hacer).
   Lo que cuelga de esa release es un zip subido a mano y con otro nombre,
   mientras que el README manda descargar `piedrasonic-windows-x64.zip`, que no
   existe en ninguna release: **quien entre a descargar hoy no encuentra lo que
   el README le promete**. El workflow ya está arreglado, pero como no se ha
   empujado ni etiquetado nada, no ha vuelto a ejecutarse nunca.
3. **Los 28 segundos de la actualización**, con lo dicho en el punto 5.

## 8. Proyecto hermano

En `D:\CODE\github\piedrasonic-andrdtv` hay un port a **Android TV** empezado el
26 de agosto de 2026: proyecto Gradle, con APK compilado y su almacén de firmas.
Es una carpeta local, **todavía sin repositorio git**, y no comparte código con
esta. Sí comparte los hallazgos del punto 4, que le valen igual.

## 9. Normas del proyecto

**La primera, y la que sostiene a las demás: este fichero se mantiene al día.**
Cuando cambie algo que contradiga lo que aquí se cuenta —el estado, una
decisión, una trampa nueva descubierta—, se corrige aquí en el mismo momento, no
«luego». Una bitácora desactualizada es peor que no tenerla, porque alguien se
la cree.

**La segunda: cada cambio se anota en [`REGISTRO.md`](REGISTRO.md)**, en una
entrada corta, en castellano y con la fecha. Ahí se lee la historia; aquí se lee
el estado.

Y las que se siguen del código tal como está escrito:

- **Los mensajes de commit se escriben en castellano, sin tildes ni eñes**
  (ASCII puro), explicando *por qué* se ha hecho el cambio y *qué se ha
  comprobado*, no solo qué se ha tocado. La documentación y los comentarios del
  código sí llevan tildes.
- **Medir antes de optimizar, y dejar la medición escrita.** Varias veces lo que
  parecía lento no lo era. Si algo se descarta, se dice con el número al lado
  para que nadie lo vuelva a intentar.
- **Nada de fallar en silencio.** Una excepción que se traga y sigue como si
  nada es el peor fallo posible en este programa, porque el síntoma aparece
  mucho después y disfrazado. Cada error se traduce a una frase que se entienda:
  «usuario o contraseña incorrectos», no un `repr` en crudo.
- **La contraseña no se enseña nunca.** La URL de Xtream lleva usuario y
  contraseña en la propia dirección, así que **no se pinta en ningún mensaje de
  error, en ningún registro y en ninguna captura**. `config.json` y `cache.json`
  están en `.gitignore` y ahí se quedan.
- **Las dependencias van fijadas a una versión exacta.** CustomTkinter cambia el
  aspecto de la ventana entre versiones menores y el `.exe` de mañana debe verse
  como el de hoy.
- **Se prueba contra un servidor de verdad o contra respuestas grabadas del
  servidor de verdad**, no contra lo que suponemos que contesta. Ver el punto 4.
- **`vendor/`, `dist/` y `build/` no se versionan.**
