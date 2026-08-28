# Registro de cambios

Historia del proyecto, de lo más reciente a lo más antiguo. Una entrada por
cambio, corta. El estado actual y el porqué de las decisiones están en
[`BITACORA.md`](BITACORA.md); aquí solo se anota **qué pasó y cuándo**.

Cada cambio nuevo se apunta arriba, en el momento de hacerlo.

---

## Sin publicar todavía

Rama `fix/vlc-bundle-and-playlist-refresh`. Cuatro commits hechos y sin empujar.

### 28 de agosto de 2026 — Bitácora y registro
Se crean `BITACORA.md` (qué es el proyecto, cómo está hecho, en qué punto está y
las normas) y este `REGISTRO.md`. Se añade un `CLAUDE.md` de tres líneas que
apunta a la bitácora, que es el fichero que una sesión de Claude Code lee sola
al empezar.

### 28 de agosto de 2026 · `4056cef` — Un BOM ya no borra la cuenta
Si algo reescribía `config.json` dejando un BOM —el Bloc de notas lo hace, y
PowerShell 5.1 también—, `json.load` fallaba, la excepción se perdía y la
aplicación arrancaba pidiendo usuario y contraseña como si no hubiera cuenta.
Ahora se lee con `utf-8-sig`, que acepta el fichero con BOM y sin él.

### 28 de agosto de 2026 · `1155902` — El vídeo va por HLS, no por `.ts`
El fallo gordo, y no estaba donde parecía: la lista nunca falló, lo que no
funcionaba era el vídeo. Pedido en `.ts`, el servidor redirigía a un dominio de
Cloudflare que resuelve a `127.0.0.1`, así que el reproductor se conectaba a sí
mismo. En `.m3u8` va al origen y funciona. HLS pasa a ser el formato por
defecto, y al cargar la lista se sondea un canal una vez por sesión para
confirmar cuál sirve; si ninguno sirve, se dice en la ventana en vez de dejar
una lista llena de canales que no arrancan.

Además, medición del arranque: de los 28 s, 27,8 son una sola categoría.
Corrección al commit anterior: el gzip no quitó ni un segundo.

### 28 de agosto de 2026 · `2ed4c00` — Entender lo que contesta el panel
El servidor responde con códigos HTTP que no se corresponden con lo que manda en
el cuerpo (512 con el JSON bueno, 404 vacío, 200 con un texto de error) y envía
`auth` como cadena, no como número: una autenticación rechazada se daba por
buena y el fallo reaparecía después, disfrazado de lista vacía. Ahora se mira
siempre el cuerpo y cada caso se traduce a una frase que se entiende. El mensaje
de error ya no lleva pegada la URL, que incluía usuario y contraseña.

Las tres peticiones del arranque salen a la vez: de 0,87-1,60 s a 0,30 s.

### 26 de agosto de 2026 · `2c41e1c` — Botón de recarga y lista antes que ventana
La lista se pide en `__init__`, antes de crear un solo widget, para que la
espera de red y la de la interfaz se solapen en vez de sumarse. Botón visible
«⟳ Actualizar» junto al contador de canales, que se agrisa mientras trabaja y
encola una segunda orden en vez de lanzar otro hilo.

También: la salida de emergencia `PYTHON_VLC_LIB_PATH` no llegaba a funcionar;
la configuración cae a `%LOCALAPPDATA%` si no se puede escribir junto al `.exe`;
el `.spec` deja de depender de una ruta fija de VLC y comprueba la arquitectura;
y el workflow de release, roto desde el paso a onedir, se reescribe para bajar
VLC 3.0.23 verificando el SHA256 y publicar el zip.

---

## Publicado

### 22 de agosto de 2026 · `cb46c87` · **v1.3.0** — VLC empaquetado y playlist viva
Tres problemas con el mismo síntoma: `libvlc.dll` no cargaba en otros PC (VLC de
32 bits, rutas no estándar y el hook de `ctypes` de PyInstaller), la lista se
quedaba congelada para siempre en cuanto existía una caché, y un servidor caído
era invisible porque la interfaz seguía enseñando la lista vieja. Se empaqueta
VLC 3.0.23 de 64 bits, la lista se refresca en cada arranque y hay un indicador
permanente con la antigüedad de la lista.

> ⚠ La compilación automática de esta release **falló**: el servidor de GitHub
> no tenía VLC. El zip que cuelga de v1.3.0 se subió a mano y se llama
> `piedrasonic-con-vlc.zip`, no `piedrasonic-windows-x64.zip`, que es el que
> pide el README. Pendiente de arreglar con una etiqueta nueva.

### 22 de agosto de 2026 · **v1.2.0** — VLC empaquetado
Primera versión que no exige tener VLC instalado.

### 20 de agosto de 2026 · `c36acaf` — Controles, catch-up libre y ventana proporcional
Controles nuevos sobre el vídeo, catch-up con salto libre de minutos y una
ventana que respeta la proporción de la imagen al redimensionarla.

### 19 de agosto de 2026 · **v1.1.0**

### 18 de agosto de 2026 · `30d2334` — Banner
Carta de ajuste dentro de la tele del banner y arreglo de textos.

### 18 de agosto de 2026 · `6c43be3` — Empaquetado, CI y README
Primer `.exe`, workflow de compilación y publicación, y README con banner.

### 18 de agosto de 2026 · `a6d516c` — Fuera el servidor por defecto
Se quita del código el servidor que venía puesto: el programa es genérico y el
usuario introduce el suyo.

### 18 de agosto de 2026 · `f385983` · **v1.0.0** — piedrasonic 1.0
Reproductor IPTV Xtream para Windows: lista de canales, vídeo con VLC embebido,
EPG, favoritos con grupos y catch-up.
