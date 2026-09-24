"""Un canal de televisión falso que se cae cuando se le manda.

Sirve un `.ts` en directo por HTTP, igual que el servidor del usuario: el
reloj corre solo, así que si el cliente se reconecta no vuelve al principio,
entra por donde va el directo. Y sabe caerse de las dos maneras en que se caen
los servidores de verdad:

- **colgando la conexión**, que es la que VLC anuncia enseguida;
- **quedándose callado**, mandando solo relleno, que es la mala: el socket
  sigue vivo y hay que darse cuenta por otro lado.

Se usa desde otro programa:

    from canal_falso import CanalFalso
    canal = CanalFalso()          # arranca solo, en un puerto libre
    print(canal.url)
    canal.cortar()                # cuelga la conexión que haya
    canal.seguir()                # vuelve a servir a quien se conecte
    canal.atascar()               # solo relleno: el corte callado
    canal.cerrar()

Y desde la línea de órdenes, para verlo con VLC a mano:

    python canal_falso.py --cortes 12

Lo de meterlo en el mismo proceso que la prueba no es capricho: con el
servidor en un subproceso, VLC daba «connection refused» de forma errática
y se perdió un buen rato en eso.
"""
import argparse
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
FUENTE = os.path.join(AQUI, "fuente.ts")
DURACION = 120.0                  # lo que dura `fuente.ts`
RELLENO = bytes([0x47, 0x1F, 0xFF, 0x10]) + bytes([0xFF]) * 184   # paquete TS nulo


def haz_fuente_otra(path=None, segundos=DURACION):
    """Lo mismo pero con otros códecs (mpeg2video + ac3), para la prueba del
    canal que vuelve cambiado de formato."""
    path = path or os.path.join(AQUI, "fuente-otra.ts")
    if os.path.exists(path):
        return path
    print("fabricando %s con ffmpeg (una sola vez)…" % os.path.basename(path))
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=720x576:rate=25:duration=%d" % segundos,
         "-f", "lavfi", "-i", "sine=frequency=880:duration=%d" % segundos,
         "-c:v", "mpeg2video", "-b:v", "1200k", "-g", "15",
         "-c:a", "ac3", "-b:a", "128k", "-f", "mpegts", path], check=True)
    return path


def haz_fuente(path=FUENTE, segundos=DURACION):
    """Genera el vídeo de pruebas con ffmpeg si no está ya.

    Son 15 MB, así que no se versiona: se fabrica la primera vez y se queda.
    `testsrc` lleva su propio cronómetro pintado, que viene bien para mirar a
    ojo dónde quedó el corte.
    """
    if os.path.exists(path):
        return path
    print("fabricando %s con ffmpeg (una sola vez)…" % os.path.basename(path))
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=%d" % segundos,
         "-f", "lavfi", "-i", "sine=frequency=440:duration=%d" % segundos,
         "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
         "-g", "25", "-b:v", "800k", "-c:a", "mp2", "-b:a", "128k",
         "-f", "mpegts", path], check=True)
    return path


class CanalFalso:
    """El servidor. Arranca al crearlo y se apaga con `cerrar()`."""

    def __init__(self, fuente=None, duracion=DURACION, puerto=0, di=print,
                 lento=0.0):
        self.datos = open(fuente or haz_fuente(), "rb").read()
        self.rate = len(self.datos) / duracion       # bytes por segundo
        # `lento`: segundos que el canal tarda en coger velocidad. Emula al que
        # conecta enseguida pero no da imagen hasta un buen rato despues, que
        # es lo que hace La 1 y lo que el vigilante confundia con una caida.
        self.lento = lento
        self.di = di
        self.t0 = time.monotonic()
        self._cortar = False
        self._atascar = False
        self._lock = threading.Lock()
        self.conexiones = 0
        self.intentos = 0            # peticiones recibidas, den vídeo o un 404
        self.abiertas = 0
        self.max_abiertas = 0        # con estas cuentas, más de 1 es un fallo
        self.solapes = []            # y dónde pasó, que si no no hay quien lo mire
        canal = self

        class Manejador(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                with canal._lock:
                    canal.intentos += 1
                if self.path != "/live.ts":
                    # el canal que no existe: es lo que contesta un panel de
                    # verdad cuando el canal se ha caido de la lista
                    canal._log("404 a %s (intento %d)" % (self.path, canal.intentos))
                    self.send_error(404, "Not Found")
                    return
                canal._sirve(self)

        self._srv = ThreadingHTTPServer(("127.0.0.1", puerto), Manejador)
        self._srv.daemon_threads = True
        self.puerto = self._srv.server_address[1]
        self.url = "http://127.0.0.1:%d/live.ts" % self.puerto
        # el mismo servidor, con una direccion que no sirve nada: para probar
        # el canal roto sin tener que levantar otro
        self.url_rota = "http://127.0.0.1:%d/nada.ts" % self.puerto
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    # ---- lo que se le manda desde la prueba ----------------------------
    def cortar(self):
        """Cuelga la conexión que haya y no admite otra hasta `seguir()`."""
        self._cortar = True

    def seguir(self):
        self._cortar = False
        self._atascar = False

    def atascar(self):
        """El corte callado: la conexión sigue viva pero solo llega relleno."""
        self._atascar = True

    def cambiar_formato(self, fuente, duracion=DURACION):
        """A partir de la siguiente conexión, el canal viaja con otros códecs.

        Pasa de verdad —los paneles cambian de perfil— y es el único caso que
        obliga a partir la grabación en dos ficheros."""
        self.datos = open(fuente, "rb").read()
        self.rate = len(self.datos) / duracion

    def cerrar(self):
        self._srv.shutdown()
        self._srv.server_close()

    # ---- las tripas ----------------------------------------------------
    def _sirve(self, h):
        with self._lock:
            self.conexiones += 1
            self.abiertas += 1
            self.max_abiertas = max(self.max_abiertas, self.abiertas)
            idx = self.conexiones
            if self.abiertas > 1:
                self.solapes.append(idx)
        self._log("conexión %d abierta (%d a la vez)%s"
                  % (idx, self.abiertas, "  <-- ¡SOLAPE!" if self.abiertas > 1 else ""))
        h.send_response(200)
        h.send_header("Content-Type", "video/mp2t")
        h.send_header("Connection", "close")   # sin Content-Length, se lee hasta EOF
        h.close_connection = True
        h.end_headers()
        base, env = time.monotonic() - self.t0, 0   # por dónde va el directo
        # el arranque se alinea a paquete TS: un servidor de verdad no te
        # entrega media cabecera, y VLC se lía si empieza a mitad
        desde = int(base * self.rate) // 188 * 188
        try:
            while True:
                if self._cortar:
                    self._log("CORTE: se cuelga la conexión %d tras %.1f s"
                              % (idx, time.monotonic() - self.t0 - base))
                    return
                if self._atascar:
                    # relleno de verdad, no silencio: así el servidor se entera
                    # de si el cliente cuelga, que callado no habría forma
                    h.wfile.write(RELLENO * 20)
                    time.sleep(0.2)
                    continue
                t = time.monotonic() - self.t0 - base
                marcha = 0.12 if (self.lento and t < self.lento) else 1.0
                quiere = int(t * self.rate * marcha)
                while env < quiere:
                    pos = (desde + env) % len(self.datos)
                    n = min(188 * 70, len(self.datos) - pos, quiere - env)
                    h.wfile.write(self.datos[pos:pos + n])
                    env += n
                time.sleep(0.02)
        except OSError:
            self._log("conexión %d: el cliente se fue tras %.1f s"
                      % (idx, time.monotonic() - self.t0 - base))
        finally:
            with self._lock:
                self.abiertas -= 1

    def _log(self, msg):
        if self.di:
            self.di("  [servidor] %s" % msg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cortes", type=float, default=0,
                    help="segundos que dura cada conexión antes de que se le cuelgue")
    ap.add_argument("--vida", type=float, default=300)
    a = ap.parse_args()
    canal = CanalFalso()
    print("sirviendo %s   (Ctrl+C para parar)" % canal.url)
    fin = time.monotonic() + a.vida
    try:
        while time.monotonic() < fin:
            time.sleep(a.cortes if a.cortes else 1.0)
            if a.cortes:
                canal.cortar()
                time.sleep(1.0)
                canal.seguir()
    except KeyboardInterrupt:
        pass
    canal.cerrar()
