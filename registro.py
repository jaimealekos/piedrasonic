"""El cuaderno de bitácora de las conexiones. Siempre encendido.

Por qué existe
--------------
El filtro del operador va y viene, cambia de un día para otro y no avisa. Lo
que se aprendió el 4 y el 5 de septiembre de 2026 se aprendió porque hubo un
fallo delante **mientras alguien miraba**, y eso no se puede organizar: cuando
el canal se cae de verdad, el usuario quiere ver la tele, no lanzar sondas.

Así que se apunta solo, siempre, y sin que nadie lo pida: cada intento de
conexión, los que salen bien y los que salen mal. Luego se mira con calma.

Esto **no es para el usuario**. No sale en la interfaz, no avisa de nada y no
cambia el comportamiento del programa. Es el cuaderno de quien mantiene esto.

Qué se apunta
-------------
Un CSV en la carpeta de datos, al lado de `config.json`, con una fila por
suceso: cuándo, qué pasó, en qué canal, contra qué origen, cómo acabó y cuánto
tardó. Con eso se puede responder después a las preguntas que hoy hay que medir
a mano:

* ¿a qué horas falla? ¿coincide con algo?
* ¿falla el 443 mientras el 22 aguanta? (si sí, mover de puerto bastaría)
* ¿qué orígenes fallan y cuáles no? ¿cambian de un día para otro?

Lo que NUNCA se apunta
----------------------
La URL de Xtream lleva usuario y contraseña dentro, así que **no se escribe una
URL entera jamás**: sólo el nombre del canal, el nombre del servidor de origen y
la parte de la ruta que no identifica a nadie. Es la misma norma de siempre, y
aquí importa el doble porque esto es un fichero que se queda en el disco.

Está en `.gitignore`, como `config.json` y las grabaciones.
"""
import csv
import io
import os
import threading
import time
from datetime import datetime

MAX_BYTES = 4 * 1024 * 1024      # al pasarse, se guarda una vuelta y se empieza
CABECERA = ["hora", "suceso", "canal", "origen", "ip", "puerto", "via",
            "resultado", "ms", "detalle"]

_lock = threading.Lock()
_ruta = None
_apagado = False


def arranca(carpeta, nombre="conexiones.csv"):
    """Abre el cuaderno. Si no se puede escribir, se calla y se apaga.

    Que no se pueda apuntar no es motivo para que el programa falle: esto es
    una ayuda para nosotros, no una función del reproductor."""
    global _ruta, _apagado
    with _lock:
        try:
            _ruta = os.path.join(carpeta, nombre)
            if not os.path.exists(_ruta):
                with io.open(_ruta, "w", encoding="utf-8", newline="") as f:
                    csv.writer(f).writerow(CABECERA)
            _apagado = False
        except OSError:
            _ruta, _apagado = None, True
    return _ruta


def apunta(suceso, canal="", origen="", ip="", puerto="", via="directo",
           resultado="", ms=None, detalle=""):
    """Una fila. No lanza nunca: se llama desde sitios donde fallar sería peor
    que no apuntar."""
    if _apagado or not _ruta:
        return
    fila = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            suceso, _limpia(canal), _limpia(origen), ip, puerto, via,
            resultado, ("%.0f" % ms) if ms is not None else "",
            _limpia(detalle)]
    try:
        with _lock:
            _rueda()
            with io.open(_ruta, "a", encoding="utf-8", newline="") as f:
                csv.writer(f).writerow(fila)
    except Exception:
        pass          # apuntar no puede tumbar a nadie


def _limpia(t):
    """Nada de URLs enteras: llevan usuario y contraseña dentro."""
    t = str(t or "")
    if "://" in t:
        t = t.split("://", 1)[1].split("/", 1)[0]     # sólo el nombre del host
    return t.replace("\n", " ").replace("\r", " ")[:200]


def _rueda():
    """Una vuelta guardada y a empezar. Sin esto, un fallo que dure toda la
    noche deja un fichero de cientos de megas."""
    try:
        if os.path.getsize(_ruta) < MAX_BYTES:
            return
    except OSError:
        return
    viejo = _ruta + ".1"
    try:
        if os.path.exists(viejo):
            os.remove(viejo)
        os.replace(_ruta, viejo)
        with io.open(_ruta, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(CABECERA)
    except OSError:
        pass


def resumen(desde_horas=24):
    """Lo apuntado en las últimas horas, en cuatro números. Para mirarlo a mano
    o desde `pruebas/donde_muere.py`."""
    if not _ruta or not os.path.exists(_ruta):
        return {}
    corte = time.time() - desde_horas * 3600
    tot = {"intentos": 0, "bien": 0, "mal": 0, "origenes": {}}
    try:
        with io.open(_ruta, encoding="utf-8", newline="") as f:
            for fila in csv.DictReader(f):
                try:
                    t = datetime.strptime(fila["hora"], "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception:
                    continue
                if t < corte or fila["suceso"] != "conexion":
                    continue
                tot["intentos"] += 1
                bien = fila["resultado"] == "abre"
                tot["bien"] += 1 if bien else 0
                tot["mal"] += 0 if bien else 1
                o = tot["origenes"].setdefault(fila["origen"], [0, 0])
                o[0 if bien else 1] += 1
    except Exception:
        return tot
    return tot
