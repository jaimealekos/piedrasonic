"""El cartel «Pidiendo los canales…» se quita al terminar la descarga.

    python pruebas\\cartel_carga.py

El cartel de la lista vacía («Pidiendo los canales… / Aparecerán aquí conforme
vayan llegando…») se quitaba en `_mete_canales`, que solo lo hace cuando entra
un lote de la categoría que se está MIRANDO. Así que si al recargar estabas en
«Favoritos» —o en cualquier categoría que no fuera la primera en llegar—, el
cartel no se limpiaba nunca y se quedaba flotando sobre la lista ya cargada.

Aquí se reproduce: se recarga con la vista puesta en «Favoritos», y al terminar
el cartel tiene que estar quitado. Con un panel Xtream falso: ni servidor de
verdad ni ffmpeg.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from interfaz import PanelFalso                     # noqa: E402

MAL = []


def comprueba(que, cond):
    print("   %-56s %s" % (que, "bien" if cond else "MAL"))
    if not cond:
        MAL.append(que)


def corre_hasta(a, cond, seg):
    fin = time.time() + seg
    while time.time() < fin:
        a.update()
        time.sleep(0.05)
        if cond():
            return True
    return False


def main():
    import settings
    tmp = tempfile.mkdtemp(prefix="piedrasonic-cartel-")
    settings.CONFIG_PATH = os.path.join(tmp, "config.json")
    import iptv_player as app
    app.APP_DIR = tmp
    app.CACHE_PATH = os.path.join(tmp, "cache.json")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), PanelFalso)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    settings.save_config({"server": url, "username": "u", "password": "p",
                          "load_on_start": False})

    a = app.LiveApp()
    a.geometry("1280x760+-4000+-4000")
    corre_hasta(a, lambda: True, 0.5)

    print("\n=== recarga con la vista en «Favoritos» ===")
    a.select_key("fav")                    # la vista que NO limpiaba el cartel
    a.load()
    # el cartel aparece al empezar; con el panel falso (localhost) la descarga
    # es tan rápida que puede aparecer y quitarse entre dos sondeos, así que
    # esto es informativo, no una comprobación: lo que importa es el estado
    # FINAL, que es donde estaba el fallo.
    visto = corre_hasta(a, lambda: getattr(a, "_cartel_w", None) is not None, 3)
    print("   (el cartel se llegó a ver: %s)" % ("sí" if visto else "no, demasiado rápido"))
    # esperar a que la descarga termine (deja de estar _loading)
    ok = corre_hasta(a, lambda: not a._loading, 25)
    comprueba("la descarga termina", ok)
    corre_hasta(a, lambda: False, 0.5)     # un respiro para el repintado final
    comprueba("hay canales cargados", len(a.by_id) > 0)
    comprueba("el cartel se ha QUITADO al terminar",
              getattr(a, "_cartel_w", None) is None)

    a.destroy()
    time.sleep(0.3)
    srv.shutdown()
    srv.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n--- %s" % ("BIEN" if not MAL else "MAL: " + "; ".join(MAL)))
    return 0 if not MAL else 1


if __name__ == "__main__":
    sys.exit(main())
