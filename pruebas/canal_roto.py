"""Un canal que no va se dice y se para; no se martillea al servidor.

    python pruebas\\canal_roto.py

Antes, un canal que daba error entraba en un bucle de reintentos cada cinco
segundos que no acababa nunca. Contra un canal que sencillamente no existe eso
son setecientas peticiones por hora con la cuenta del usuario, y así es como se
acaba baneado.

Aquí se comprueban las tres cosas de las que va el arreglo:

1. **se deja de intentar**: unos pocos intentos y se para;
2. **se dice qué pasa**, preguntándoselo al servidor —«el servidor dice que ese
   canal no existe (404)»— en vez de un «no se pudo abrir» que no explica nada;
3. **y se puede reintentar a mano**, que es lo que sustituye al bucle.

Y una cuarta que es la que da sentido a las otras: **después de rendirse no se
manda ni una petición más** hasta que el usuario lo pida.

Las esperas de la caída se acortan a propósito para esta prueba (ver
`ESPERAS_CORTAS`): lo que se comprueba es la política —que los intentos se
acaban y se para—, no cuántos segundos dura cada una; con los valores de verdad
la prueba tardaría cuatro minutos.
"""
import os
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
sys.path.insert(0, AQUI)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import customtkinter as ctk                        # noqa: E402
from canal_falso import CanalFalso, haz_fuente     # noqa: E402
from player import VlcPlayer, VLC_OK, VLC_ERR      # noqa: E402

ESPERAS_CORTAS = (2, 2, 3)      # en vez de (5, 5, 10, 20, 30, 60)
# Lo que no puede decir un canal que nunca llegó a abrirse: no se ha perdido
# nada, es que no había nada.
ALARMA = ("perdido", "Reconectando", "No se pudo reconectar")


def espera(pl, root, segundos, hasta=None, canal=None):
    """Deja correr el reproductor mirando los carteles que van saliendo."""
    avisos, ini = [], time.monotonic()
    while time.monotonic() - ini < segundos:
        root.update()
        time.sleep(0.1)
        if pl._msg_txt and (not avisos or avisos[-1] != pl._msg_txt):
            avisos.append(pl._msg_txt)
            print("   [%4.1f s] cartel: %s" % (time.monotonic() - ini,
                                               pl._msg_txt.replace("\n", " · ")))
        if hasta and hasta():
            break
    return avisos


def prueba_no_existe(root, pl, mal):
    """Un canal que devuelve 404: ni bucle, ni silencio."""
    print("\n=== un canal que no existe (404) ===")
    canal = CanalFalso(di=lambda m: None)
    pl.play(canal.url_rota, "Canal roto", True)
    avisos = espera(pl, root, 40, hasta=lambda: pl._rendido and pl._sonda is None)

    if not pl._rendido:
        mal.append("no se dejó de intentar con un canal que da 404")
    # abriendo no se ha perdido nada: no toca hablar de conexiones perdidas
    perdidas = [a for a in avisos if any(p in a for p in ALARMA)]
    if perdidas:
        mal.append("un canal que nunca llegó a abrir se anunció como caída: %s"
                   % perdidas)
    if not pl._msg_boton:
        mal.append("el cartel no ofrece el botón de reintentar a mano")
    if "404" not in (pl._msg_txt or ""):
        mal.append("el cartel no dice el error del servidor: %r" % pl._msg_txt)
    else:
        print("   el cartel dice el error y trae botón: bien")

    intentos = canal.intentos
    print("   peticiones al servidor hasta rendirse: %d  (cartel de %d px)"
          % (intentos, pl._msg_h))
    # Tres intentos, y VLC pide dos veces por intento (medido: 7 peticiones en
    # total contando la sonda). Lo que se vigila aquí es que sea un puñado y no
    # un goteo sin fin: con el bucle de antes, en este mismo rato iban 8 y a la
    # hora 720.
    if intentos > 8:
        mal.append("demasiadas peticiones antes de rendirse: %d" % intentos)
    if pl._msg_h <= 56:
        mal.append("el cartel de error no creció para el botón: %d px" % pl._msg_h)

    # y ahora la que importa: rendido, no se manda NADA mas
    espera(pl, root, 12)
    if canal.intentos != intentos:
        mal.append("siguió pidiendo después de rendirse: %d -> %d"
                   % (intentos, canal.intentos))
    else:
        print("   doce segundos rendido sin una sola petición más: bien")

    # el botón vuelve a intentarlo
    pl._reintenta_a_mano()
    espera(pl, root, 3)
    if pl._rendido or canal.intentos <= intentos:
        mal.append("el botón de reintentar no volvió a intentarlo")
    else:
        print("   el botón vuelve a intentarlo: bien")

    pl.stop()
    canal.cerrar()
    root.update()


def prueba_no_vuelve(root, pl, mal):
    """Un canal que estaba sonando, se cae y no vuelve."""
    print("\n=== uno que se cae y no vuelve ===")
    canal = CanalFalso(di=lambda m: None)
    pl.play(canal.url, "Canal normal", True)
    espera(pl, root, 12, hasta=lambda: pl.mp.get_time() > 0)
    if not pl.mp.get_time() > 0:
        mal.append("el canal de la segunda prueba no llegó a sonar")
        pl.stop()
        canal.cerrar()
        return
    print("   suena; ahora el servidor se cae para siempre")
    canal.cortar()
    intentos = canal.intentos

    avisos = espera(pl, root, 60, hasta=lambda: pl._rendido and pl._sonda is None)
    if not pl._rendido:
        mal.append("una caída sin vuelta no acabó nunca de intentarse")
    else:
        print("   se dejó de intentar tras %d peticiones más"
              % (canal.intentos - intentos))
    if not any("perdido la conexión" in a for a in avisos):
        mal.append("una caída de verdad ya no se anuncia como tal")
    if not pl._msg_boton:
        mal.append("tras rendirse de una caída no hay botón de reintentar")

    hasta = canal.intentos
    espera(pl, root, 10)
    if canal.intentos != hasta:
        mal.append("siguió pidiendo tras rendirse de una caída")
    else:
        print("   y rendido no vuelve a pedir: bien")

    pl.stop()
    canal.cerrar()
    root.update()


def main():
    haz_fuente()
    mal = []
    VlcPlayer.ESPERAS_CAE = ESPERAS_CORTAS
    root = ctk.CTk()
    root.geometry("640x400+-4000+-4000")     # fuera de pantalla, que no moleste
    pl = VlcPlayer(root, network_caching=1500)
    pl.pack(fill="both", expand=True)
    root.update()

    prueba_no_existe(root, pl, mal)
    time.sleep(1)
    prueba_no_vuelve(root, pl, mal)

    root.destroy()
    print("\n--- %s" % ("BIEN" if not mal else "MAL: " + "; ".join(mal)))
    return 0 if not mal else 1


if __name__ == "__main__":
    if not VLC_OK:
        sys.exit("no se puede cargar VLC: %s" % VLC_ERR)
    sys.exit(main())
