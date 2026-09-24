"""Las tres cosas de la interfaz que se hacen dentro de la propia ventana.

    python pruebas\\interfaz.py

No se mira cómo queda —para eso hay que mirarlo— sino que haga lo que dice:

- el **panel de ajustes**, que ocupa el sitio de las dos columnas en vez de
  abrir una ventana;
- el **modo edición** de la columna de categorías, que enseña las ocultas y se
  marcan ahí mismo;
- el **arranque**, con y sin la casilla de cargar la lista al iniciar.

Levanta un panel Xtream falso, así que no hace falta ni servidor de verdad ni
ffmpeg. La ventana va fuera de pantalla para no molestar.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CATS = ["Deportes", "Cine", "Noticias", "Infantil"]
PEDIDOS = []


class PanelFalso(BaseHTTPRequestHandler):
    """Lo justo del protocolo Xtream para que la ventana se llene."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        PEDIDOS.append(self.path)
        if "get_live_categories" in self.path:
            c = json.dumps([{"category_id": str(i), "category_name": n,
                             "stream_count": 2}
                            for i, n in enumerate(CATS, 1)]).encode()
        elif "get_live_streams" in self.path:
            cid = self.path.split("category_id=")[-1].split("&")[0] or "1"
            c = json.dumps([{"stream_id": int(cid) * 10 + k, "category_id": cid,
                             "name": "%s %d" % (CATS[int(cid) - 1], k)}
                            for k in (1, 2)]).encode()
        elif "player_api.php" in self.path and "action=" not in self.path:
            c = json.dumps({"user_info": {"auth": "1"}}).encode()
        else:
            c = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(c)))
        self.end_headers()
        self.wfile.write(c)


MAL = []


def comprueba(que, cond):
    print("   %-52s %s" % (que, "bien" if cond else "MAL"))
    if not cond:
        MAL.append(que)


def abre(app, settings, tmp, cfg_extra=None):
    """Una ventana nueva, con su configuración recién hecha."""
    cfg = {"server": URL, "username": "u", "password": "p"}
    cfg.update(cfg_extra or {})
    settings.save_config(cfg)
    a = app.LiveApp()
    a.geometry("1280x760+-4000+-4000")
    return a


def corre(a, seg):
    fin = time.time() + seg
    while time.time() < fin:
        a.update()
        time.sleep(0.05)


def espera_peticion(a, texto, desde, seg=25):
    """Corre hasta que el panel falso reciba una petición de las que se buscan.

    Con un tiempo fijo no vale: una recarga puede tardar lo que quiera, y si se
    sigue adelante antes de tiempo sus peticiones acaban contadas en el
    escenario siguiente y fallan DOS comprobaciones por un solo suceso."""
    fin = time.time() + seg
    while time.time() < fin:
        if any(texto in p for p in PEDIDOS[desde:]):
            return True
        a.update()
        time.sleep(0.05)
    return False


def cierra(a, seg=3.0):
    """Cierra la ventana y espera a que no lleguen más peticiones.

    Al destruirla quedan hilos de descarga en vuelo; sus peticiones llegarían
    tarde y se contarían en la prueba siguiente."""
    a.destroy()
    quieto, ultimo = time.time(), len(PEDIDOS)
    while time.time() - quieto < seg:
        if len(PEDIDOS) != ultimo:
            ultimo, quieto = len(PEDIDOS), time.time()
        time.sleep(0.1)


def main():
    import settings
    tmp = tempfile.mkdtemp(prefix="piedrasonic-pruebas-")
    settings.CONFIG_PATH = os.path.join(tmp, "config.json")
    import iptv_player as app
    app.CACHE_PATH = os.path.join(tmp, "cache.json")
    app.APP_DIR = tmp

    def cfg_disco():
        return json.load(open(settings.CONFIG_PATH, encoding="utf-8"))

    # ---- el panel de ajustes ----
    print("\n=== el panel de ajustes")
    a = abre(app, settings, tmp)
    corre(a, 7)
    a.open_settings()
    corre(a, 0.6)
    comprueba("se abre y esconde las dos columnas",
              a._opts_on and not a.cat_col.winfo_ismapped()
              and not a.ch_col.winfo_ismapped())
    comprueba("trae puesta la cuenta que hay", a.op_srv.get() == URL)
    a.op_arranque.set(False)
    a._opciones_arranque()
    corre(a, 0.3)
    comprueba("la casilla se guarda al tocarla",
              cfg_disco().get("load_on_start") is False)
    a.op_arranque.set(True)
    a._opciones_arranque()
    corre(a, 0.3)
    a.open_settings()
    corre(a, 0.6)
    comprueba("se cierra y vuelven las dos columnas",
              not a._opts_on and a.cat_col.winfo_ismapped()
              and a.ch_col.winfo_ismapped())

    a.toggle_category_edit()
    corre(a, 0.3)
    a.open_settings()
    corre(a, 0.6)
    comprueba("abrir los ajustes saca del modo edición",
              not a._cat_edit and a._opts_on)
    a.op_usr.delete(0, "end")
    a.op_usr.insert(0, "otro")
    n = len(PEDIDOS)
    a._opciones_conectar()
    llego = espera_peticion(a, "get_live_streams", n)
    comprueba("conectar comprueba, guarda y cierra el panel", not a._opts_on)
    comprueba("la cuenta nueva queda escrita", cfg_disco().get("username") == "otro")
    comprueba("y se vuelve a pedir la lista", llego)
    a.open_settings()
    corre(a, 0.4)
    a.op_usr.delete(0, "end")
    a._opciones_conectar()
    corre(a, 0.5)
    comprueba("con un campo vacío, avisa y no conecta",
              "Rellena" in a.op_estado.cget("text"))
    cierra(a)

    # ---- el modo edición de categorías ----
    print("\n=== el modo edición de la columna de categorías")
    a = abre(app, settings, tmp, {"hidden_categories": ["3"]})
    corre(a, 7)
    comprueba("de normal, la oculta no está en la columna",
              "3" not in a._cat_widgets and "1" in a._cat_widgets)
    a.toggle_category_edit()
    corre(a, 0.4)
    comprueba("editando salen todas, también la oculta",
              all(str(i) in a._cat_widgets for i in (1, 2, 3, 4)))
    comprueba("y ya no están Favoritos ni Todos",
              "fav" not in a._cat_widgets and "all" not in a._cat_widgets)
    comprueba("la oculta sale desmarcada",
              a._cat_widgets["3"]["btn"].cget("text").startswith(app.MARCA_NO))
    a._marca_visible("3")
    a._marca_visible("1")
    corre(a, 0.3)
    comprueba("marcar y desmarcar cambia las marcas",
              a._cat_widgets["3"]["btn"].cget("text").startswith(app.MARCA_SI)
              and a._cat_widgets["1"]["btn"].cget("text").startswith(app.MARCA_NO))
    comprueba("y todavía no se ha guardado nada",
              cfg_disco().get("hidden_categories") == ["3"])
    a.toggle_category_edit()
    corre(a, 0.5)
    comprueba("al salir se guarda", cfg_disco().get("hidden_categories") == ["1"])
    comprueba("y la columna vuelve a la normalidad",
              "1" not in a._cat_widgets and "3" in a._cat_widgets
              and "all" in a._cat_widgets)
    cierra(a)

    # ---- el arranque ----
    print("\n=== el arranque, con la casilla puesta y quitada")
    for quiere, hay_cache in ((True, False), (False, True), (False, False)):
        if not hay_cache and os.path.exists(app.CACHE_PATH):
            os.remove(app.CACHE_PATH)
        PEDIDOS.clear()
        a = abre(app, settings, tmp, {"load_on_start": quiere})
        corre(a, 7)
        pidio = any("get_live_streams" in p for p in PEDIDOS)
        suena = bool(a.current_stream)
        comprueba("casilla=%-5s cache=%-5s → pide la lista: %s"
                  % (quiere, hay_cache, pidio), pidio == quiere)
        if quiere or hay_cache:
            comprueba("   ...y arranca un canal", suena)
        else:
            comprueba("   ...y avisa de que no hay lista",
                      getattr(a, "_cartel_w", None) is not None)
        cierra(a)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n--- %s" % ("BIEN" if not MAL else "MAL: " + "; ".join(MAL)))
    return 0 if not MAL else 1


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 0), PanelFalso)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    URL = "http://127.0.0.1:%d" % srv.server_address[1]
    sys.exit(main())
