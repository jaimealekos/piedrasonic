"""Las diez últimas listas, y poder volver a una de antes.

    python pruebas\\listas_guardadas.py

Se guarda más de una lista porque la última puede no servir: el panel se cae a
media descarga, o devuelve una lista rara, o —lo que pasó el 5 de septiembre de
2026— deja de contestar del todo. Con diez guardadas se vuelve a la de ayer sin
esperar a que se arregle nada.

Lo que se comprueba:

1. **Que rotan bien**, y se comprueba **llamando a `_guarda_cache()`**, que es
   la que rota de verdad. La primera versión de esta prueba rotaba ella misma
   los ficheros y luego miraba el resultado: pasaba en verde **con la rotación
   sin escribir siquiera en el programa**, y el fallo lo encontró el usuario al
   pulsar Actualizar y ver que su lista anterior había desaparecido. Una prueba
   que reimplementa lo que prueba no prueba nada.
2. **Que se leen de la más nueva a la más vieja**, y con la fecha de DENTRO del
   fichero: rotar cambia la fecha del fichero y diría que todas son de hace un
   momento.
3. **Que el rótulo distingue**: «Lista guardada … · última» cuando es la última
   descargada, «Lista recuperada …» cuando el usuario ha ido a buscar una vieja.
   Esa distinción no es cosmética: mirar una lista de hace tres días creyendo
   que es la de hoy es el fallo que costó caro en agosto.
4. **Que recuperar una vieja pone SUS canales**, no los de la última. Sin esto
   lo demás es un rótulo bonito y nada más.
"""
import json
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


def escribe_lista(app, n, cuando, cuantos, marca):
    """Fabrica una lista guardada, con su fecha por dentro y sus canales.

    Se usa solo para preparar el escenario de las comprobaciones 2, 3 y 4: la
    rotación de la 1 la tiene que hacer el programa."""
    d = {"fetched_at": cuando,
         "categories": [{"category_id": "1", "category_name": "Uno",
                         "stream_count": cuantos}],
         "streams": [{"stream_id": 1000 + i, "category_id": "1",
                      "name": "%s %d" % (marca, i)} for i in range(cuantos)]}
    with open(app._ruta_lista(n), "w", encoding="utf-8") as fh:
        json.dump(d, fh)


def corre(a, seg):
    fin = time.time() + seg
    while time.time() < fin:
        a.update()
        time.sleep(0.05)


def main():
    import settings
    tmp = tempfile.mkdtemp(prefix="piedrasonic-listas-")
    settings.CONFIG_PATH = os.path.join(tmp, "config.json")
    import iptv_player as app
    app.APP_DIR = tmp
    app.CACHE_PATH = os.path.join(tmp, "cache.json")
    N = app.LISTAS_GUARDADAS

    srv = ThreadingHTTPServer(("127.0.0.1", 0), PanelFalso)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    settings.save_config({"server": "http://127.0.0.1:%d" % srv.server_address[1],
                          "username": "u", "password": "p",
                          "load_on_start": False})

    a = app.LiveApp()
    a.geometry("1280x760+-4000+-4000")
    corre(a, 0.5)

    print()
    print("=== 1. la rotación la hace el PROGRAMA, no la prueba")
    for i in range(N + 2):
        a.fallidas = []
        a.categories = [{"category_id": "1", "category_name": "Uno",
                         "stream_count": 10 + i}]
        a.all_streams = [{"stream_id": 1000 + k, "category_id": "1",
                          "name": "tanda%d %d" % (i, k)} for k in range(10 + i)]
        a.by_id = {str(s["stream_id"]): s for s in a.all_streams}
        a._guarda_cache()                          # <-- el código de verdad
    hay = [n for n in range(N + 4) if os.path.exists(app._ruta_lista(n))]
    comprueba("se guardan las %d, ni una más" % N, hay == list(range(N)))
    comprueba("no se ha creado la %d" % N, not os.path.exists(app._ruta_lista(N)))
    try:
        ult = json.load(open(app._ruta_lista(0), encoding="utf-8-sig"))
        ant = json.load(open(app._ruta_lista(1), encoding="utf-8-sig"))
        comprueba("cache.json es la última guardada",
                  ult["streams"][0]["name"].startswith("tanda%d " % (N + 1)))
        comprueba("LA ANTERIOR NO SE PIERDE: está en cache.1.json",
                  ant["streams"][0]["name"].startswith("tanda%d " % N))
    except Exception as e:
        comprueba("se pueden leer las dos primeras (%s)" % e, False)

    print()
    print("=== 2. se leen de la más nueva a la más vieja")
    # con fechas separadas: N guardados seguidos caen en el mismo segundo y no
    # se podría ver el orden
    ahora = time.time()
    for n in range(N):
        escribe_lista(app, n, ahora - n * 3600, 40 - n, "vuelta%d" % n)
    listas = a._listas()
    print("      %s …" % [(n, a._edad(t), c) for n, _, t, c in listas][:3])
    comprueba("salen las %d" % N, len(listas) == N)
    comprueba("en orden, de la más nueva a la más vieja",
              [t for _, _, t, _ in listas] == sorted(
                  [t for _, _, t, _ in listas], reverse=True))
    comprueba("la más nueva es la primera", bool(listas) and listas[0][3] == 40)
    comprueba("la fecha sale de DENTRO del fichero, no del disco",
              bool(listas) and a._edad(listas[-1][2]) != a._edad(listas[0][2]))

    print()
    print("=== 3. el rótulo distingue guardada de recuperada")
    a._usa_cache(cual=0)
    corre(a, 0.3)
    t0 = a.sync_lbl.cget("text")
    print("      con la última:   %r" % t0)
    comprueba("con la última dice «guardada» y la marca como última",
              "guardada" in t0 and "última" in t0)
    a._usa_cache(cual=3)
    corre(a, 0.3)
    t3 = a.sync_lbl.cget("text")
    print("      con una vieja:   %r" % t3)
    comprueba("con una vieja dice «recuperada»",
              "recuperada" in t3.lower() and "última" not in t3)
    comprueba("y dice de cuándo es", "hace" in t3)

    print()
    print("=== 4. recuperar una vieja pone SUS canales")
    a._usa_cache(cual=0)
    corre(a, 0.3)
    ultimos = sorted(s.get("name", "") for s in a.by_id.values())
    a._usa_cache(cual=3)
    corre(a, 0.3)
    viejos = sorted(s.get("name", "") for s in a.by_id.values())
    print("      última: %d canales (%s…)  ·  recuperada: %d canales (%s…)"
          % (len(ultimos), ultimos[0][:10] if ultimos else "",
             len(viejos), viejos[0][:10] if viejos else ""))
    comprueba("los canales cambian al recuperar", ultimos != viejos)
    comprueba("son los de la lista que se pidió",
              bool(viejos) and all(n.startswith("vuelta3 ") for n in viejos))
    comprueba("y son los que decía el inventario",
              len(viejos) == [c for n, _, _, c in listas if n == 3][0])

    print()
    print("=== 5. el menú")
    a._lista_puesta = 3
    entradas = []
    import tkinter as tk
    hueco = {}
    tk.Menu.tk_popup = lambda self, x, y, e="": hueco.setdefault("m", self)
    a._menu_listas()
    m = hueco.get("m")
    if m is None:
        comprueba("el menú se construye", False)
    else:
        for i in range(m.index("end") + 1):
            try:
                entradas.append(m.entrycget(i, "label"))
            except Exception:
                entradas.append(None)          # separador
        print("      %d entradas; la primera: %r" % (len(entradas), entradas[0]))
        comprueba("no lleva título: la primera entrada ya es una lista",
                  bool(entradas[0]) and entradas[0].startswith(("hace", "• hace")))
        comprueba("ninguna entrada está deshabilitada (nada que iluminar en vano)",
                  all(m.entrycget(i, "state") != "disabled"
                      for i in range(m.index("end") + 1)
                      if m.type(i) != "separator"))
        comprueba("ofrece las %d listas" % N,
                  sum(1 for e in entradas if e and "hace" in e) == N)
        comprueba("marca con • la que se está viendo",
                  any(e and e.startswith("• ") for e in entradas))
        comprueba("y deja actualizar desde el servidor",
                  any(e and "Actualizar" in e for e in entradas))

    a.destroy()
    time.sleep(0.5)
    srv.shutdown()
    srv.server_close()
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("--- %s" % ("BIEN" if not MAL else "MAL: " + "; ".join(MAL)))
    return 0 if not MAL else 1


if __name__ == "__main__":
    sys.exit(main())
