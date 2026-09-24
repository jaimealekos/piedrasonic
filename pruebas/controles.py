"""Prueba de los controles del reproductor: cuándo deben verse y cuándo no.

No mira píxeles: mira la decisión, que es donde estaba el fallo. `_want()` del
reproductor dice si en ese momento tiene que haber barras, y aquí se le lleva
por la secuencia que las dejaba escondidas para siempre.

    python pruebas\\controles.py

Dos cosas distintas dejaban al usuario sin controles, y las dos se comprueban
aquí porque las dos acababan igual: reiniciando el programa.

1. **El interruptor.** Entrar a pantalla completa es un doble clic en el vídeo,
   y el PRIMER clic de ese doble llegaba como clic sencillo, que es el que
   alterna los controles. Se apagaban justo antes de entrar.
2. **Las ventanas de las barras se morían.** Poner o quitar la pantalla
   completa recrea la ventana nativa del root, y Windows destruye las que son
   propiedad suya. Las barras se quedaban con un handle muerto y no volvían a
   aparecer nunca. Esto no se ve mirando la decisión —`_want()` decía que sí—,
   así que se pregunta a Windows si las ventanas siguen existiendo.

No hace falta ni servidor ni vídeo. Eso sí: la segunda parte pone la ventana a
pantalla completa **de verdad** un par de segundos, que es la única forma de
provocar el fallo.
"""
import os
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import ctypes                                       # noqa: E402
import customtkinter as ctk                        # noqa: E402
from player import VlcPlayer, VLC_OK, VLC_ERR      # noqa: E402

mal = []


def comprueba_bruto(que, cond):
    print("   %-42s %s" % (que, "bien" if cond else "MAL"))
    if not cond:
        mal.append(que)


def comprueba(pl, que, quiere):
    pl._pointer_in = True            # el ratón, de mentira, sobre el vídeo
    hay = pl._want()
    ok = hay == quiere
    print("   %-42s barras: %-5s (esperaba %s)  %s"
          % (que, hay, quiere, "" if ok else "  <-- MAL"))
    if not ok:
        mal.append(que)


def clic_sencillo(pl, root):
    """Un clic de verdad espera por si viene un segundo; aquí se le da tiempo."""
    pl._on_video_click()
    root.update()
    time.sleep(0.45)
    root.update()


def main():
    root = ctk.CTk()
    root.geometry("640x400+-4000+-4000")     # fuera de pantalla, que no moleste
    pl = VlcPlayer(root, request_fullscreen=lambda: None)
    pl.pack(fill="both", expand=True)
    root.update()
    pl._focus_ts = 0                 # que los clics cuenten: ninguno da el foco

    print("\n=== los controles del reproductor ===")
    comprueba(pl, "recién abierto, con el ratón encima", True)

    # Un doble clic en el vídeo: Tk manda Button-1 y despues Double-1.
    pl._on_video_click()
    pl._fs_click()
    pl.set_fullscreen(True)
    comprueba(pl, "recién entrado en pantalla completa", False)   # empieza oculto

    pl._on_motion()                  # se mueve el ratón: tienen que salir
    comprueba(pl, "moviendo el ratón en pantalla completa", True)

    pl.set_fullscreen(False)
    comprueba(pl, "al salir de pantalla completa", True)

    clic_sencillo(pl, root)
    comprueba(pl, "un clic sencillo: los apaga", False)
    clic_sencillo(pl, root)
    comprueba(pl, "otro clic sencillo: los enciende", True)

    # --- y ahora la pantalla completa de verdad ---
    print("\n=== la pantalla completa de verdad (la ventana se ve un momento)")

    def vivas():
        """¿Siguen existiendo las ventanas de las barras, para Windows?"""
        try:
            u = ctypes.windll.user32
            return all(bool(u.IsWindow(w.winfo_id()))
                       for w in (pl.top, pl.bottom, pl.msg))
        except Exception:
            return True          # fuera de Windows esto no aplica

    def espera(seg):
        fin = time.time() + seg
        while time.time() < fin:
            root.update()
            time.sleep(0.03)

    root.geometry("900x560+80+80")
    espera(0.6)
    comprueba_bruto("antes de nada, las barras existen", vivas())
    # el mismo orden que sigue la aplicación: soltarlas ANTES de tocar el modo
    pl.suelta_barras()
    root.attributes("-fullscreen", True)
    espera(1.0)
    pl.set_fullscreen(True)
    espera(0.4)
    comprueba_bruto("en pantalla completa siguen existiendo", vivas())
    pl.suelta_barras()
    root.attributes("-fullscreen", False)
    espera(1.0)
    pl.set_fullscreen(False)
    espera(0.4)
    comprueba_bruto("y al salir, también", vivas())

    root.destroy()
    print("\n--- %s" % ("BIEN" if not mal else "MAL: " + "; ".join(mal)))
    return 0 if not mal else 1


if __name__ == "__main__":
    if not VLC_OK:
        sys.exit("no se puede cargar VLC: %s" % VLC_ERR)
    sys.exit(main())
