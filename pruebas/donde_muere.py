"""¿En cuál de los tres saltos muere? Y, si se le pide, vigilando en el tiempo.

    python pruebas\\donde_muere.py               una foto, ahora
    python pruebas\\donde_muere.py --vigila 120  vigila 120 minutos y deja un CSV

Pedir un canal son **tres saltos**, no uno (ver «El panel no sirve el vídeo» en
la bitácora):

    panel (player_api.php)  ->  302 a un origen  ->  el origen sirve el vídeo

Y el fallo casi siempre está en el tercero, que es el que nadie mira. Sin esta
herramienta, «no se ve nada» es indistinguible entre cuenta caducada, canal que
ya no existe, servidor caído y filtro del operador. Con ella se sabe en un
minuto.

Lo que la hace concluyente es la fila de los **vecinos**: se sondea también una
IP contigua a la del origen, del mismo centro de datos y el mismo puerto. Si el
vecino va y el origen no, el camino hasta esa red está bien y lo que falla
apunta a esas direcciones concretas. Sin esa comparación, un filtro dirigido y
un corte de internet se leen igual.

El modo `--vigila` existe porque **el filtro va y viene**: medido el 4 de
septiembre de 2026, los orígenes pasaban de 4/18 a 18/18 en cuestión de horas.
Una foto puede caer en un hueco bueno y mentir. Dejándolo corriendo se ve la
forma de la cosa, y sobre todo se ve **qué puertos sobreviven** cuando el
filtro está activo, que es lo que decide si basta con mover el servicio de
puerto.

NUNCA imprime usuario ni contraseña: la URL de Xtream los lleva dentro y todo lo
que sale por pantalla pasa antes por `tapa()`.
"""
import argparse
import csv
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

import settings                                    # noqa: E402
from xtream import XtreamClient                    # noqa: E402

_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

CONECTA, SILENCIO, RECHAZO, RARO = "+", ".", "R", "?"
LEYENDA = ("+ conecta   . silencio (el paquete se pierde)   "
           "R rechazo, RST (la IP se alcanza)   ? otro")


class _NoSigue(urllib.request.HTTPRedirectHandler):
    """Para poder LEER el 302 en vez de seguirlo: el destino es el dato."""
    def redirect_request(self, *a, **k):
        return None


def toca(ip, puerto, tmo=4.0):
    """Un toque TCP. Distinguir el silencio del rechazo es la mitad del
    diagnóstico: un RST demuestra que la IP se alcanza y que solo está cerrado
    ese puerto; el silencio es la firma de que alguien se come el paquete."""
    s = socket.socket()
    s.settimeout(tmo)
    ini = time.monotonic()
    try:
        s.connect((ip, puerto))
        return CONECTA, time.monotonic() - ini
    except socket.timeout:
        return SILENCIO, time.monotonic() - ini
    except ConnectionRefusedError:
        return RECHAZO, time.monotonic() - ini
    except OSError as e:
        if getattr(e, "winerror", 0) == 10061:
            return RECHAZO, time.monotonic() - ini
        return RARO, time.monotonic() - ini
    finally:
        s.close()


def resuelve(host):
    try:
        return sorted({x[4][0] for x in socket.getaddrinfo(host, None,
                                                           socket.AF_INET)})
    except Exception:
        return []


def vecina(ip):
    """Una IP contigua, para comparar. Es la fila que decide."""
    p = ip.split(".")
    ult = int(p[3])
    p[3] = str(ult - 1 if ult > 1 else ult + 1)
    return ".".join(p)


def descubre(cl, cache, cuantos=12, di=print):
    """A qué orígenes manda el panel. Se lee el 302 sin seguirlo.

    Se descubren en vez de escribirlos a mano porque cambian: hay al menos seis
    y los canales se reparten entre ellos sin patrón."""
    op = urllib.request.build_opener(_NoSigue,
                                     urllib.request.HTTPSHandler(context=_SSL))
    origenes, mirados = {}, 0
    for s in cache:
        if mirados >= cuantos:
            break
        sid = s.get("stream_id")
        if not sid:
            continue
        mirados += 1
        req = urllib.request.Request(cl.live_url(sid, "m3u8"),
                                     headers={"User-Agent": cl.user_agent})
        try:
            op.open(req, timeout=10)
        except urllib.error.HTTPError as e:
            h = urllib.parse.urlsplit(e.headers.get("Location", "")).hostname
            if h:
                origenes.setdefault(h, []).append(s.get("name", ""))
        except Exception:
            pass
    return origenes


def _crudo(cl, cfg):
    """La respuesta del panel tal cual, sin interpretarla. Devuelve
    (codigo, cuerpo)."""
    op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL))
    req = urllib.request.Request(cl._url(), headers={"User-Agent": cl.user_agent})
    try:
        r = op.open(req, timeout=15)
        return r.status, r.read(400)
    except urllib.error.HTTPError as e:
        return e.code, e.read(400)
    except Exception as e:
        return None, str(type(e).__name__).encode()


def _que_es(cod):
    if cod in (521, 522, 523, 524):
        return ("Cloudflare llega, pero NO alcanza al panel de detrás: "
                "el panel está caído o no le contesta")
    if cod in (525, 526):
        return "Cloudflare no puede cifrar contra el panel: certificado del origen"
    if cod in (401, 403):
        return "la cuenta no vale o está en uso en otro sitio"
    if cod == 404:
        return "el panel no reconoce esta cuenta"
    return ""


def foto(cl, cfg, cache, di=print):
    """Los tres saltos, una vez, en orden."""
    tapa = lambda s: (s or "").replace(cfg["username"], "USUARIO") \
                              .replace(cfg["password"], "CLAVE")
    panel = urllib.parse.urlsplit(cfg["server"]).hostname
    puerto = urllib.parse.urlsplit(cfg["server"]).port or 443

    di("\n=== salto 1: el panel ===")
    ips = resuelve(panel)
    di("   resuelve a: %s" % (ips or "NO RESUELVE  <- es el DNS"))
    for ip in ips[:3]:
        r, dt = toca(ip, puerto)
        di("   %-16s :%-5d %s (%.2f s)" % (ip, puerto, r, dt))
    # Antes del login, la peticion en crudo: el CODIGO es la pista. Un 52x dice
    # que Cloudflare responde pero no alcanza al panel de detras, y eso no se ve
    # a traves de `login()`, que solo dice "no ha devuelto datos de la cuenta" y
    # manda a mirar la cuenta. Pasó el 5 de septiembre de 2026.
    cod, cuerpo = _crudo(cl, cfg)
    if cod:
        di("   respuesta en crudo: HTTP %s, %d bytes%s"
           % (cod, len(cuerpo), "  <- " + _que_es(cod) if _que_es(cod) else ""))
    else:
        # sin codigo: ni eso ha llegado. Tambien es un dato, y de los buenos:
        # el TCP de Cloudflare abre pero la peticion no vuelve.
        di("   respuesta en crudo: NINGUNA (%s)  <- Cloudflare acepta la "
           "conexión pero la petición no vuelve: el panel de detrás no contesta"
           % cuerpo.decode("utf-8", "replace"))
    ini = time.monotonic()
    try:
        info = cl.login()
        di("   login OK en %.2f s%s" % (
            time.monotonic() - ini,
            " · %s" % info.get("status") if isinstance(info, dict) and
            info.get("status") else ""))
        panel_ok = True
    except Exception as e:
        di("   login FALLA: %s  <- es la cuenta o el panel" % tapa(str(e)))
        panel_ok = False

    if not panel_ok:
        di("\n   No se sigue: sin panel no hay canales que mirar.")
        return

    di("\n=== salto 2: a qué orígenes manda ===")
    origenes = descubre(cl, cache, 12, di)
    if not origenes:
        di("   ningún canal devolvió redirección  <- el panel no da canales")
        return
    for h, canales in origenes.items():
        di("   %-26s %2d de 12 canales: %s"
           % (h, len(canales), ", ".join(c[:18] for c in canales[:4])))

    di("\n=== salto 3: los orígenes, y sus vecinos al lado ===")
    di("   " + LEYENDA)
    for h in origenes:
        ips = resuelve(h)
        if not ips:
            di("   %-26s NO RESUELVE" % h)
            continue
        ip = ips[0]
        v = vecina(ip)
        fila = []
        for p in (443, 22, 80):
            r, dt = toca(ip, p)
            fila.append("%d:%s" % (p, r))
        rv, _ = toca(v, 443)
        di("   %-26s %-16s %s   vecino %s :443 %s"
           % (h, ip, "  ".join(fila), v, rv))

    di("\n   Cómo se lee:")
    di("   · origen en silencio y vecino conecta -> el camino a esa red está")
    di("     bien; lo que falla apunta a esa IP concreta.")
    di("   · origen y vecino los dos en silencio -> es la ruta o la red entera.")
    di("   · rechazo (R) -> la IP se alcanza: ahí no hay filtro, hay un puerto")
    di("     cerrado.")
    di("   · todo bien aquí y aun así no se ve -> mira el vídeo, no la red.")


def vigila(cl, cfg, cache, minutos, cada, salida, di=print):
    """Sondeo largo. El filtro va y viene: una foto puede caer en un hueco."""
    origenes = descubre(cl, cache, 12, di)
    if not origenes:
        di("no se descubrió ningún origen: no hay nada que vigilar")
        return
    panel = urllib.parse.urlsplit(cfg["server"]).hostname
    objetivos = []
    for h in origenes:
        ips = resuelve(h)
        if not ips:
            continue
        objetivos.append(("origen %s" % h, ips[0], 443))
        objetivos.append(("origen %s" % h, ips[0], 22))
        objetivos.append(("vecino de %s" % h, vecina(ips[0]), 443))
    for ip in resuelve(panel)[:1]:
        objetivos.append(("CONTROL panel", ip, 443))
    objetivos.append(("CONTROL cloudflare", "1.1.1.1", 443))

    di("\nvigilando %d objetivos cada %d s durante %d min"
       % (len(objetivos), cada, minutos))
    di("el CSV se va escribiendo en %s" % salida)
    di("\n" + LEYENDA + "\n")
    hist = {i: "" for i in range(len(objetivos))}
    fin = time.monotonic() + minutos * 60
    with open(salida, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["hora", "que", "ip", "puerto", "resultado", "segundos"])
        vuelta = 0
        while time.monotonic() < fin:
            marca = time.strftime("%Y-%m-%d %H:%M:%S")
            fila = []
            for i, (nom, ip, p) in enumerate(objetivos):
                r, dt = toca(ip, p)
                hist[i] += r
                fila.append(r)
                w.writerow([marca, nom, ip, p, r, "%.3f" % dt])
            f.flush()
            vuelta += 1
            di("  %s  %s" % (marca[11:], " ".join(fila)))
            if vuelta % 20 == 0:
                di("")
                for i, (nom, ip, p) in enumerate(objetivos):
                    h = hist[i]
                    di("    %-28s %-16s :%-4d %d/%d"
                       % (nom, ip, p, h.count(CONECTA), len(h)))
                di("")
            time.sleep(cada)

    di("\n--- resumen (%d vueltas)" % len(next(iter(hist.values()), "")))
    for i, (nom, ip, p) in enumerate(objetivos):
        h = hist[i]
        di("   %-28s %-16s :%-4d  %3d/%-3d  %s"
           % (nom, ip, p, h.count(CONECTA), len(h), h[-60:]))
    di("\nLo que hay que mirar en ese resumen:")
    di("  · si el 443 de un origen cae mientras su 22 aguanta, el filtro va")
    di("    contra IP:puerto y mover el servicio de puerto puede bastar;")
    di("  · si caen los dos puertos pero el vecino aguanta, va contra la IP;")
    di("  · si cae también el vecino, es la ruta hasta esa red;")
    di("  · si caen los controles, es la línea de casa y no hay más que ver.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vigila", type=int, metavar="MIN", default=0,
                    help="en vez de una foto, vigila tantos minutos")
    ap.add_argument("--cada", type=int, default=30, help="segundos entre vueltas")
    ap.add_argument("--csv", default=os.path.join(AQUI, "ruta.csv"))
    a = ap.parse_args()

    cfg = settings.load_config()
    if not (cfg.get("server") and cfg.get("username")):
        sys.exit("no hay cuenta configurada: abre piedrasonic y mete la tuya")
    cl = XtreamClient(cfg["server"], cfg["username"], cfg["password"],
                      user_agent=cfg.get("user_agent") or settings.DEFAULT_UA,
                      output=cfg.get("output") or "m3u8", timeout=20)
    cache = []
    try:
        with open(os.path.join(settings.DATA_DIR, "cache.json"),
                  encoding="utf-8-sig") as f:
            d = json.load(f)
        cache = d.get("streams") or d.get("live") or []
    except Exception:
        pass
    if not cache:
        try:
            cache = cl.live_streams()
        except Exception as e:
            sys.exit("sin lista de canales y no se pudo pedir: %s" % e)

    if a.vigila:
        vigila(cl, cfg, cache, a.vigila, a.cada, a.csv)
    else:
        foto(cl, cfg, cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
