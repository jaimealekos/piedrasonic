"""Un canal que tarda en arrancar no es un canal caído.

    python pruebas\\arranque_lento.py

Hay canales que conectan enseguida pero tardan un buen rato en dar el primer
fotograma —La 1 lo hace—. El vigilante los daba por perdidos a los diez
segundos, así que al abrirlos lo primero que salía era «se ha perdido la
conexión, reconectando en 4…», y al reintentar entraban bien.

Aquí se comprueban las dos mitades del asunto, porque arreglar una rompiendo la
otra sería peor que el fallo:

1. un canal lento arranca **sin un solo aviso de alarma**;
2. un canal que se cae de verdad **se sigue detectando**.

Lo de «de alarma» no es un matiz: mientras el canal está abriendo sí sale un
cartel, pero de cortesía —«Conectando con el canal…»— y solo después de un par
de segundos, porque una pantalla negra sin explicación es indistinguible de un
programa roto. Lo que no puede salir es nada que hable de conexión perdida, de
reconexiones o de que el canal no arranca: eso es dar la alarma por algo que
todavía no ha fallado, y era justo el fallo.
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

LENTO = 14.0          # lo que tarda el canal lento en coger velocidad
ESPERA = 26.0         # lo que se le mira
# Lo que NO puede salir mientras un canal está abriendo: son las palabras con
# las que el programa da la alarma.
ALARMA = ("perdido", "Reconectando", "no arranca", "No se pudo")


def mira(pl, root, canal, segundos, corta_en=None):
    """Reproduce mirando el reloj del canal y los carteles que van saliendo."""
    avisos, arranco, ini = [], 0.0, time.monotonic()
    while time.monotonic() - ini < segundos:
        root.update()
        time.sleep(0.2)
        t = time.monotonic() - ini
        if corta_en and t >= corta_en and not canal._cortar:
            print("   [%4.1f s] el servidor corta" % t)
            canal.cortar()
        if not arranco and pl.mp.get_time() > 0:
            arranco = t
            print("   [%4.1f s] primer fotograma" % t)
        if pl._msg_txt and pl._msg_txt not in avisos:
            avisos.append(pl._msg_txt)
            print("   [%4.1f s] cartel: %s" % (t, pl._msg_txt))
    return arranco, avisos


def main():
    haz_fuente()
    mal = []
    root = ctk.CTk()
    root.geometry("640x400+-4000+-4000")     # fuera de pantalla, que no moleste
    pl = VlcPlayer(root, network_caching=1500)
    pl.pack(fill="both", expand=True)
    root.update()

    print("\n=== un canal que tarda %.0f s en coger velocidad ===" % LENTO)
    canal = CanalFalso(lento=LENTO, di=lambda m: None)
    pl.play(canal.url, "Canal lento", True)
    arranco, avisos = mira(pl, root, canal, ESPERA)
    if not arranco:
        mal.append("el canal lento no llegó a dar imagen")
    alarmas = [a for a in avisos if any(p in a for p in ALARMA)]
    if alarmas:
        mal.append("se dio la alarma con un canal que solo era lento: %s" % alarmas)
    else:
        print("   ni una alarma: bien")
    pl.stop()
    canal.cerrar()
    root.update()
    time.sleep(1)

    print("\n=== y uno que se cae de verdad ===")
    canal = CanalFalso(di=lambda m: None)
    pl.play(canal.url, "Canal normal", True)
    arranco, avisos = mira(pl, root, canal, 26.0, corta_en=8.0)
    if not any("perdido la conexión" in a for a in avisos):
        mal.append("una caída de verdad pasó desapercibida")
    else:
        print("   la caída se detectó: bien")
    pl.stop()
    canal.cerrar()
    root.destroy()

    print("\n--- %s" % ("BIEN" if not mal else "MAL: " + "; ".join(mal)))
    return 0 if not mal else 1


if __name__ == "__main__":
    if not VLC_OK:
        sys.exit("no se puede cargar VLC: %s" % VLC_ERR)
    sys.exit(main())
