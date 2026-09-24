"""Prueba de la grabación con cortes, contra el reproductor de verdad.

Levanta un canal falso que se cae a voluntad, mete el `VlcPlayer` de
`player.py` en una ventana fuera de pantalla, le da a grabar y comprueba lo
que queda en el disco. Es el código real, no una imitación.

    python pruebas\\graba_con_cortes.py            # todos los escenarios
    python pruebas\\graba_con_cortes.py cortes     # solo uno

Los escenarios:

| nombre      | qué pasa                                    | qué tiene que salir |
|-------------|---------------------------------------------|---------------------|
| `cortes`    | el servidor cuelga dos veces                | **un** fichero, con dos huecos |
| `atasco`    | el flujo se muere callado (solo relleno)    | **un** fichero, con un hueco |
| `formato`   | vuelve con otros códecs                     | **dos** ficheros, los dos sanos |
| `singrabar` | igual pero sin grabar                       | ningún fichero, y reconecta |

Y en todos: **nunca dos conexiones abiertas a la vez**, porque estas cuentas
suelen permitir una sola y la segunda te tiraría la imagen.

Hace falta `ffmpeg`/`ffprobe` en el PATH: para fabricar el vídeo de pruebas la
primera vez, y para mirar el resultado por dentro. Tarda un par de minutos:
son cortes de verdad, con sus esperas de verdad.
"""
import json
import os
import subprocess
import sys
import time

try:                               # la consola de Windows no siempre es UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

import customtkinter as ctk                                    # noqa: E402
from canal_falso import CanalFalso, haz_fuente, haz_fuente_otra  # noqa: E402
from player import VlcPlayer, VLC_OK, VLC_ERR                  # noqa: E402

SALIDA = os.path.join(AQUI, "grabado")


def di(t0, msg):
    print("  [%5.1f s] %s" % (time.monotonic() - t0, msg), flush=True)


# ---- mirar el resultado por dentro ------------------------------------

def mide(path):
    """Duración, saltos de los sellos de tiempo y quejas del decodificador."""
    fmt = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", path],
        capture_output=True, text=True).stdout or "{}").get("format", {})
    ts = [float(x) for x in subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "packet=pts_time", "-of", "csv=p=0", path],
        capture_output=True, text=True).stdout.replace(",", " ").split()]
    saltos = [(ts[i - 1], ts[i]) for i in range(1, len(ts))
              if ts[i] - ts[i - 1] > 0.5 or ts[i] - ts[i - 1] < -0.1]
    quejas = len([l for l in subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"],
        capture_output=True, text=True).stderr.splitlines() if l.strip()])
    return {
        "bytes": int(fmt.get("size", 0) or 0),
        "dura": float(fmt.get("duration", 0) or 0),
        "pts": (ts[0], ts[-1]) if ts else (0, 0),
        "saltos": saltos,
        "quejas": quejas,
    }


def cuenta(m, mal, tope_quejas=50):
    """Escribe lo medido, y va apuntando en `mal` lo que no cuadre.

    `tope_quejas` sube solo en un sitio: el primer fichero del escenario
    `formato`, que acaba con unos segundos de destrozo mientras el reproductor
    se da cuenta de que el canal ha cambiado de códecs. Lo que se comprueba
    ahí es que ese daño quede ACOTADO —sin cortar el fichero serían miles— y
    que el segundo fichero salga limpio."""
    print("      %d bytes · dura %.1f s · sellos de tiempo de %.1f a %.1f · %d quejas"
          % (m["bytes"], m["dura"], m["pts"][0], m["pts"][1], m["quejas"]))
    for a, b in m["saltos"]:
        print("      hueco: de %.1f a %.1f s  (%+.1f)" % (a, b, b - a))
    if m["pts"][1] < m["pts"][0]:
        mal.append("los sellos de tiempo van hacia atrás: el fichero está roto")
    if any(b < a for a, b in m["saltos"]):
        mal.append("hay un salto hacia atrás en los sellos de tiempo")
    if m["quejas"] > tope_quejas:
        mal.append("%d quejas del decodificador (el tope era %d): el vídeo va corrupto"
                   % (m["quejas"], tope_quejas))


# ---- los escenarios ----------------------------------------------------

def guion(esc, canal, pl, otra):
    """Qué pasa y cuándo, en segundos desde que empieza la prueba."""
    g = [(1.0, lambda: pl.play(canal.url, "Canal de pruebas", True), "ve el canal")]
    if esc != "singrabar":
        g.append((6.0, pl.toggle_record, "le da a GRABAR"))
    if esc == "cortes":
        g += [(16.0, canal.cortar, "el servidor CORTA"),
              (18.0, canal.seguir, "el servidor vuelve"),
              (40.0, canal.cortar, "el servidor CORTA otra vez"),
              (42.0, canal.seguir, "el servidor vuelve"),
              (62.0, pl.toggle_record, "PARA de grabar"),
              (68.0, None, "fin")]
    elif esc == "atasco":
        g += [(16.0, canal.atascar, "el flujo se muere CALLADO (solo relleno)"),
              (30.0, canal.seguir, "vuelve a mandar de verdad"),
              (55.0, pl.toggle_record, "PARA de grabar"),
              (61.0, None, "fin")]
    elif esc == "formato":
        g += [(16.0, lambda: (canal.cambiar_formato(otra), canal.cortar()),
               "CORTA y volverá con OTROS códecs"),
              (18.0, canal.seguir, "el servidor vuelve"),
              (45.0, pl.toggle_record, "PARA de grabar"),
              (51.0, None, "fin")]
    elif esc == "singrabar":
        g += [(16.0, canal.cortar, "el servidor CORTA"),
              (18.0, canal.seguir, "el servidor vuelve"),
              (40.0, None, "fin")]
    return g


ESPERADO = {                       # (ficheros que tienen que quedar, huecos)
    "cortes": (1, 2),
    "atasco": (1, 1),
    "formato": (2, None),
    "singrabar": (0, None),
}


def corre(esc):
    print("\n=== %s ===" % esc, flush=True)
    dest = os.path.join(SALIDA, esc)
    os.makedirs(dest, exist_ok=True)
    for f in os.listdir(dest):
        os.remove(os.path.join(dest, f))

    t0 = time.monotonic()
    canal = CanalFalso(di=lambda m: di(t0, m))
    otra = haz_fuente_otra() if esc == "formato" else None

    root = ctk.CTk()
    root.geometry("640x400+-4000+-4000")        # fuera de pantalla, que no moleste
    pl = VlcPlayer(root, record_dir=dest, network_caching=1500)
    pl.pack(fill="both", expand=True)
    root.update()

    pasos = guion(esc, canal, pl, otra)
    hechos = [0]
    ini = time.monotonic()
    proximo_aviso = [0.0]

    # UN paso por latido, y el latido cada 250 ms. Si se ejecutan dos seguidos,
    # «corta» y «vuelve» se anulan entre sí y el corte no llega a ocurrir: la
    # prueba salía en verde con un corte de menos. Pasa en cuanto la máquina va
    # cargada y el bucle se queda atrás, así que no basta con acelerar el
    # latido: hay que negarse a hacer dos cosas a la vez.
    def reloj():
        t = time.monotonic() - ini
        if hechos[0] < len(pasos) and pasos[hechos[0]][0] <= t:
            _, f, texto = pasos[hechos[0]]
            hechos[0] += 1
            di(t0, ">>> %s" % texto)
            if f is None:
                root.quit()
                return
            try:
                f()
            except Exception as e:
                di(t0, "    fallo al hacerlo: %r" % e)
        if t >= proximo_aviso[0] and pl._rec_on and pl._rec_file:
            proximo_aviso[0] = t + 4
            tam = os.path.getsize(pl._rec_file) if os.path.exists(pl._rec_file) else -1
            di(t0, "    grabando %.0f s en %s (%d bytes)"
               % (pl._rec_seg(), os.path.basename(pl._rec_file), tam))
        root.after(250, reloj)

    root.after(500, reloj)
    root.mainloop()
    pl.release()
    root.destroy()
    time.sleep(2)
    canal.cerrar()

    # ---- el veredicto ----
    mal = []
    ficheros = sorted(f for f in os.listdir(dest) if f.endswith(".ts"))
    quiere_f, quiere_h = ESPERADO[esc]
    print("   ha quedado: %s" % (", ".join(ficheros) or "nada"))
    if len(ficheros) != quiere_f:
        mal.append("esperaba %d fichero(s) y hay %d" % (quiere_f, len(ficheros)))
    for i, f in enumerate(ficheros):
        m = mide(os.path.join(dest, f))
        # solo el primero del cambio de códecs tiene derecho a acabar sucio
        cuenta(m, mal, 1000 if (esc == "formato" and i == 0) else 50)
        if quiere_h is not None and len(ficheros) == 1 and len(m["saltos"]) != quiere_h:
            mal.append("esperaba %d hueco(s) y hay %d" % (quiere_h, len(m["saltos"])))
    if canal.max_abiertas > 1:
        mal.append("hubo %d conexiones abiertas a la vez (la cuenta permite una); "
                   "se solapó al abrir la(s) conexión(es) %s"
                   % (canal.max_abiertas,
                      ", ".join(str(x) for x in canal.solapes)))
    else:
        print("      conexiones: %d, nunca más de una a la vez" % canal.conexiones)

    if mal:
        print("   MAL:")
        for m in mal:
            print("      - %s" % m)
    else:
        print("   BIEN")
    return not mal


if __name__ == "__main__":
    if not VLC_OK:
        sys.exit("no se puede cargar VLC: %s" % VLC_ERR)
    haz_fuente()
    cuales = sys.argv[1:] or list(ESPERADO)
    resultados = {c: corre(c) for c in cuales}
    print("\n--- resumen")
    for c, ok in resultados.items():
        print("   %-10s %s" % (c, "bien" if ok else "MAL"))
    sys.exit(0 if all(resultados.values()) else 1)
