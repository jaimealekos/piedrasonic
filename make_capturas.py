"""Genera las capturas del README (screenshots/*.png) con canales inventados.

    python make_capturas.py

Levanta un panel Xtream de mentira —categorías, canales, guía y vídeo
sintético fabricado con ffmpeg—, abre la ventana de verdad contra él, la lleva
a cada estado y la fotografía. Así las capturas no enseñan ni la cuenta de
nadie ni canales reales. Mientras dura, la ventana se queda por encima de todo:
si el ratón pasa por encima, saldrá en la foto el resalte de algún botón.

Necesita ffmpeg en el PATH y un VLC de 64 bits allí donde lo busca el programa
(vendor\\vlc, PIEDRASONIC_VLC_DIR o el instalado). Solo Windows: la foto se
saca de la pantalla, que es la única manera de que salga el vídeo, pintado por
VLC en su propia ventana.
"""
import base64
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from ctypes import wintypes
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import customtkinter as ctk
from PIL import ImageGrab

APP = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(APP, "screenshots")
GEOMETRIA = "1400x600+160+120"      # la ventana de las capturas
USUARIO, CLAVE = "demo", "demo"
TROZO = 2                           # segundos de cada trozo HLS
TROZOS = 15                         # trozos de cada vídeo: 30 s que se repiten
DIRECTO_MIN = 10                    # lo que dura la lista de un directo
ESPERA_VIDEO = 25                   # segundos que se le dan a VLC para arrancar
FUENTE = r"C:\Windows\Fonts\segoeuib.ttf"
FUENTE_FINA = r"C:\Windows\Fonts\segoeui.ttf"

# (id, nombre, canales); cada canal es (id, nombre, tiene catch-up).
CATEGORIAS = [
    ("1", "Noticias", [(101, "Actualidad 24", False), (102, "Mundo Hoy", True),
                       (103, "Economía Directo", False),
                       (104, "Tiempo y Tráfico", False),
                       (105, "Noticias Locales", False)]),
    ("2", "Deportes", [(201, "Pista Central", True), (202, "Motor y Ruedas", False),
                       (203, "Fútbol Base", False), (204, "Tenis Directo", False),
                       (205, "Aventura", False)]),
    ("3", "Cine", [(301, "Butaca Uno", False), (302, "Cine Clásico", False),
                   (303, "Sala Estreno", False), (304, "Cine Negro", False)]),
    ("4", "Series", [(401, "Serie Total", False), (402, "Maratón", False),
                     (403, "Comedia", False), (404, "Misterio", False)]),
    ("5", "Documentales", [(501, "Planeta Azul", False),
                           (502, "Naturaleza Viva", False),
                           (503, "Historia Abierta", True),
                           (504, "Ciencia a Fondo", False),
                           (505, "Grandes Viajes", False),
                           (506, "Mar y Montaña", False),
                           (507, "Arte y Museos", False),
                           (508, "Vida Salvaje", False)]),
    ("6", "Música", [(601, "Ritmo 80", False), (602, "Acústico", False),
                     (603, "Clásica", False), (604, "Jazz Club", False)]),
    ("7", "Infantil", [(701, "Dibujos", False), (702, "Pequeñecos", False),
                       (703, "Cuentos", False)]),
]
TOTAL = sum(len(canales) for _, _, canales in CATEGORIAS)

# Lo que echa cada canal ahora y luego, para la guía.
GUIA = {
    501: ("La vida en el arrecife", "Volcanes de Islandia"),
    102: ("Resumen de la mañana", "Informativo de mediodía"),
}
GUIA_OTROS = ("Programa en emisión", "A continuación")

# nombre: (colores del fondo, mosca del canal, rótulo, subrótulo)
VIDEOS = {
    "planeta": ("0x041a2e 0x0b5d73 0x1f9e8e", "PLANETA AZUL",
                "La vida en el arrecife", "Océanos · capítulo 3"),
    "mundo": ("0x1c0a10 0x7a1a22 0xc8662a", "MUNDO HOY",
              "Resumen de la mañana", "Las noticias de las nueve"),
}
VIDEO_DE = {102: "mundo"}           # los demás canales echan el primero

FAVORITOS = ["501", "102", "201", "302"]
GRUPOS = [{"name": "Para cada día", "channels": ["102", "501", "201"]}]

_U = ctypes.windll.user32
_DWM = ctypes.windll.dwmapi
_GA_ROOT = 2
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_WINDOW_CORNER_PREFERENCE = 33     # Windows 11
_DWMWCP_DONOTROUND = 1


# ---- el vídeo -------------------------------------------------------------
def _ruta_filtro(ruta):
    """Una ruta de Windows escrita como la quiere una opción de un filtro de
    ffmpeg: entre comillas y con los dos puntos de la unidad escapados. Hacen
    falta las dos cosas: el grafo y la opción se leen cada uno con su escape."""
    return "'" + ruta.replace("\\", "/").replace(":", "\\:") + "'"


def haz_videos(carpeta):
    """Cada entrada de VIDEOS, troceada en HLS: fondo que se mueve despacio,
    mosca del canal y rótulo del programa. La pista de audio es silencio: VLC
    la abre como la de un canal, pero no suena nada mientras se hacen fotos."""
    for nombre, (colores, mosca, rotulo, sub) in VIDEOS.items():
        d = os.path.join(carpeta, nombre)
        os.makedirs(d)
        textos = {}
        for clave, texto in (("mosca", mosca), ("rotulo", rotulo), ("sub", sub)):
            ruta = os.path.join(d, clave + ".txt")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(texto)
            textos[clave] = _ruta_filtro(ruta)
        c = colores.split()
        negrita, fina = _ruta_filtro(FUENTE), _ruta_filtro(FUENTE_FINA)
        filtro = (
            f"gradients=s=1280x720:r=25:n={len(c)}:"
            + ":".join(f"c{i}={x}" for i, x in enumerate(c))
            + ":speed=0.004:seed=3,"
            f"drawtext=fontfile={negrita}:textfile={textos['mosca']}:"
            "fontsize=30:fontcolor=white@0.85:x=w-tw-60:y=110,"
            "drawbox=x=0:y=ih*0.60:w=iw*0.58:h=118:color=black@0.42:t=fill,"
            f"drawtext=fontfile={negrita}:textfile={textos['rotulo']}:"
            "fontsize=46:fontcolor=white:x=60:y=h*0.60+16,"
            f"drawtext=fontfile={fina}:textfile={textos['sub']}:"
            "fontsize=26:fontcolor=white@0.8:x=60:y=h*0.60+74")
        fps_trozo = str(25 * TROZO)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", filtro,
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", str(TROZO * TROZOS),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-g", fps_trozo, "-keyint_min", fps_trozo,
             "-sc_threshold", "0", "-c:a", "aac", "-b:a", "64k",
             "-f", "hls", "-hls_time", str(TROZO), "-hls_list_size", "0",
             "-hls_segment_filename", os.path.join(d, "%d.ts"),
             os.path.join(d, "ffmpeg.m3u8")],
            check=True)


def lista_hls(video, minutos):
    """Lista HLS de `minutos` que repite en bucle los trozos de `video`.

    Se da como grabación cerrada (#EXT-X-ENDLIST) también para los directos:
    VLC la pone desde el principio y no se queda esperando trozos nuevos, que
    este panel no fabrica."""
    lineas = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{TROZO}",
              "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD"]
    for i in range(int(minutos * 60 / TROZO)):
        if i and i % TROZOS == 0:
            lineas.append("#EXT-X-DISCONTINUITY")
        lineas += [f"#EXTINF:{TROZO:.3f},", f"/hls/{video}/{i % TROZOS}.ts"]
    lineas.append("#EXT-X-ENDLIST")
    return "\n".join(lineas) + "\n"


# ---- el panel -------------------------------------------------------------
def guia(sid):
    ahora, luego = GUIA.get(sid, GUIA_OTROS)
    inicio = datetime.now().replace(minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%d %H:%M:%S"
    franjas = [(ahora, inicio, inicio + timedelta(hours=1)),
               (luego, inicio + timedelta(hours=1), inicio + timedelta(hours=2))]
    return [{"title": base64.b64encode(t.encode()).decode(), "description": "",
             "start": a.strftime(fmt), "end": b.strftime(fmt)}
            for t, a, b in franjas]


def api(q):
    """La respuesta de player_api.php a la acción que se pida."""
    accion = q.get("action")
    if not accion:
        return {"user_info": {"username": USUARIO, "auth": 1, "status": "Active"},
                "server_info": {}}
    if accion == "get_live_categories":
        return [{"category_id": cid, "category_name": nombre, "parent_id": 0}
                for cid, nombre, _ in CATEGORIAS]
    if accion == "get_live_streams":
        pedida = q.get("category_id")
        return [{"num": sid, "name": nombre, "stream_type": "live",
                 "stream_id": sid, "stream_icon": "", "epg_channel_id": "",
                 "category_id": cid, "tv_archive": int(archivo),
                 "tv_archive_duration": 7 if archivo else 0}
                for cid, _, canales in CATEGORIAS if pedida in (None, "", cid)
                for sid, nombre, archivo in canales]
    if accion == "get_short_epg":
        return {"epg_listings": guia(int(q.get("stream_id", 0)))}
    return []


class Panel(BaseHTTPRequestHandler):
    """Lo justo de un panel Xtream: cuenta, categorías, canales, guía y vídeo."""

    protocol_version = "HTTP/1.1"
    videos = None                   # carpeta de los trozos; la pone main()

    def log_message(self, *a):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        partes = url.path.strip("/").split("/")
        if partes[-1] == "player_api.php":
            q = dict(urllib.parse.parse_qsl(url.query))
            return self._manda(json.dumps(api(q)).encode(), "application/json")
        if partes[0] in ("live", "timeshift"):
            # /live/u/p/ID.m3u8 y /timeshift/u/p/MINUTOS/INICIO/ID.m3u8
            sid = int(partes[-1].split(".")[0])
            minutos = DIRECTO_MIN if partes[0] == "live" else int(partes[3])
            cuerpo = lista_hls(VIDEO_DE.get(sid, "planeta"), minutos)
            return self._manda(cuerpo.encode(), "application/vnd.apple.mpegurl")
        if partes[0] == "hls" and len(partes) == 3:
            ruta = os.path.join(self.videos, partes[1], partes[2])
            if os.path.isfile(ruta):
                with open(ruta, "rb") as f:
                    return self._manda(f.read(), "video/mp2t")
        self._manda(b"", "text/plain", 404)

    def _manda(self, cuerpo, tipo, codigo=200):
        try:
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            # VLC cuelga a medio trozo al cambiar de canal: es lo normal.
            pass


# ---- la ventana -----------------------------------------------------------
def prepara_app(datos):
    """Importa el programa con todos sus ficheros de datos en `datos`."""
    import settings
    settings.DATA_DIR = settings.APP_DIR = datos
    settings.CONFIG_PATH = os.path.join(datos, "config.json")
    import iptv_player as app
    app.APP_DIR = datos
    app.CACHE_PATH = os.path.join(datos, "cache.json")
    return settings, app


def corre(w, seg):
    fin = time.time() + seg
    while time.time() < fin:
        w.update()
        time.sleep(0.02)


def espera(w, cond, seg, que):
    fin = time.time() + seg
    while time.time() < fin:
        w.update()
        if cond():
            return
        time.sleep(0.05)
    raise RuntimeError(f"no ha llegado {que} en {seg} s")


def espera_video(a):
    """Hasta que VLC lleve un par de segundos poniendo lo último que se pidió."""
    corre(a, 1.0)
    espera(a, lambda: a.player.mp.is_playing() and a.player.mp.get_time() > 2000,
           ESPERA_VIDEO, "el vídeo")


def _hwnd(w):
    return _U.GetAncestor(w.winfo_id(), _GA_ROOT)


def encima(w, *mas):
    """Por encima de todo, y con esquinas rectas para que en la foto no se
    cuele por ellas lo que haya detrás. `mas`: ventanas que van con ella."""
    w.update()
    for v in (w,) + mas:
        v.attributes("-topmost", True)
    recta = ctypes.c_int(_DWMWCP_DONOTROUND)
    _DWM.DwmSetWindowAttribute(_hwnd(w), _DWMWA_WINDOW_CORNER_PREFERENCE,
                               ctypes.byref(recta), ctypes.sizeof(recta))


def foto(w, nombre):
    """Fotografía la ventana tal como se ve en pantalla, barra de título incluida."""
    w.update()
    r = wintypes.RECT()
    _DWM.DwmGetWindowAttribute(_hwnd(w), _DWMWA_EXTENDED_FRAME_BOUNDS,
                               ctypes.byref(r), ctypes.sizeof(r))
    # Sin el borde de 1 px: es translúcido y deja ver lo que hay detrás.
    img = ImageGrab.grab(bbox=(r.left + 1, r.top + 1, r.right - 1, r.bottom - 1),
                         all_screens=True)
    ruta = os.path.join(SALIDA, nombre)
    img.save(ruta, optimize=True)
    print("   %-26s %dx%d" % (os.path.relpath(ruta, APP), img.width, img.height))


def foto_cuenta(a, settings, app):
    """La ventana que pide la cuenta en el primer arranque, rellena a mano.

    Se abre la misma que abre el programa sin cuenta (`first=True`) encima de
    la ventana ya en marcha: con una segunda ventana raíz, los temporizadores
    de la primera seguían saltando en el bucle de la segunda."""
    settings.settings_dialog(a, {}, lambda *_: None, first=True, icon=app.ICON)
    hallado = []

    def dialogo():
        hallado[:] = [w for w in a.winfo_children()
                      if isinstance(w, ctk.CTkToplevel)
                      and w.title().startswith("Cuenta")]
        return bool(hallado)

    espera(a, dialogo, 10, "la ventana de la cuenta")
    d = hallado[0]
    corre(a, 1.0)
    campos = [w for w in d.winfo_children() if isinstance(w, ctk.CTkEntry)]
    for campo, valor in zip(campos, ("http://mi-proveedor.example:8080",
                                     "usuario", "contraseña")):
        campo.delete(0, "end")
        campo.insert(0, valor)
    d.geometry("+%d+%d" % (a.winfo_rootx() + 200, a.winfo_rooty() + 120))
    encima(d)
    corre(a, 1.5)
    foto(d, "cuenta.png")
    d.destroy()


def fotos(settings, app, url):
    """La ventana con la lista cargada: un canal en directo; los favoritos con
    un canal en catch-up, y la ventana de la cuenta."""
    settings.save_config({"server": url, "username": USUARIO, "password": CLAVE,
                          "window_geometry": GEOMETRIA, "volume": 70,
                          "favorites": FAVORITOS, "favorite_groups": GRUPOS})
    a = app.LiveApp()
    encima(a, a.player.top, a.player.bottom)
    espera(a, lambda: len(a.all_streams) >= TOTAL, 30, "la lista de canales")
    # Las barras del vídeo solo salen con el ratón encima: se le dice que lo está.
    a.player._pointer_over = lambda: True

    a.select_key("5")
    a.tree.selection_set("501")
    a.tree.see("501")
    espera_video(a)
    corre(a, 2.0)
    foto(a, "principal.png")

    a.select_key("fav")
    a.tree.selection_set("102")
    espera_video(a)
    a.play_catchup(60)
    espera_video(a)
    corre(a, 2.0)
    foto(a, "favoritos.png")

    foto_cuenta(a, settings, app)
    a._on_close()


def main():
    if not sys.platform.startswith("win"):
        sys.exit("Las capturas se sacan de la pantalla de Windows: solo va en Windows.")
    if not shutil.which("ffmpeg"):
        sys.exit("Hace falta ffmpeg en el PATH para fabricar el vídeo de las capturas.")
    tmp = tempfile.mkdtemp(prefix="piedrasonic-capturas-")
    srv = None
    try:
        datos = os.path.join(tmp, "datos")
        os.makedirs(datos)
        os.chdir(datos)             # lo que se escriba sin ruta, que caiga aquí
        settings, app = prepara_app(datos)
        import player
        if not player.VLC_OK:
            sys.exit("Sin VLC de 64 bits no hay vídeo que fotografiar.\n"
                     + (player.VLC_HINT or str(player.VLC_ERR)))
        print("Fabricando el vídeo…")
        Panel.videos = os.path.join(tmp, "videos")
        haz_videos(Panel.videos)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Panel)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print("Capturas:")
        fotos(settings, app, "http://127.0.0.1:%d" % srv.server_address[1])
    finally:
        if srv is not None:
            srv.shutdown()
        os.chdir(APP)
        try:
            shutil.rmtree(tmp)
        except OSError as e:
            print(f"No se ha podido borrar la carpeta temporal {tmp}: {e}")


if __name__ == "__main__":
    main()
