"""
Configuración compartida de piedrasonic:
  * carga/guardado de config.json con las contraseñas EN CLARO (legibles y
    copiables; `unprotect` aún descifra las `dpapi:`/`obf:` de configs antiguas),
  * diálogo de acceso / cuenta (servidor + usuario + contraseña) con
    validación real contra el panel antes de guardar.
"""
import os
import sys
import json
import base64
import threading
import ctypes
from ctypes import wintypes
import tkinter as tk
import customtkinter as ctk

from theme import C, font
from xtream import XtreamClient


# Rutas: empaquetado (PyInstaller) los recursos van en _MEIPASS (solo lectura) y
# los datos del usuario junto al .exe, o en %LOCALAPPDATA% si ahi no se escribe.
def _writable(d):
    """Si se puede escribir de verdad en `d`.

    Se prueba escribiendo. `os.access` miente en Windows: con la
    virtualizacion de UAC dice que si en sitios donde el primer open() falla.
    """
    probe = os.path.join(d, ".piedrasonic-write-test")
    try:
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def _data_dir():
    """Donde guardar config.json y cache.json.

    Junto al .exe mientras se pueda, que es lo comodo: mueves la carpeta a otro
    PC y la cuenta y la lista se van con ella. Pero descomprimido en Archivos
    de programa —o en cualquier sitio protegido— ahi no se escribe, y el
    programa se quedaria sin recordar ni la cuenta ni el volumen sin decir por
    que. En ese caso se cae a %LOCALAPPDATA%\\piedrasonic.
    """
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))
    beside = os.path.dirname(sys.executable)
    if _writable(beside):
        return beside
    home = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback = os.path.join(home, "piedrasonic")
    try:
        os.makedirs(fallback, exist_ok=True)
    except OSError:
        return beside            # no hay a donde ir: que falle a la vista
    return fallback


DATA_DIR = _data_dir()
RES_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))

APP_DIR = DATA_DIR
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DEFAULT_SERVER = "https://"      # prefijo genérico; el usuario introduce el suyo
DEFAULT_UA = "VLC/3.0.20 LibVLC/3.0.20"


# --- cifrado de la contraseña con DPAPI de Windows ------------------------
# CryptProtectData: cifrado real ligado a la cuenta de Windows del usuario.
class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(fn, raw):
    buf = ctypes.create_string_buffer(raw, len(raw))
    bin_ = _BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _BLOB()
    ok = fn(ctypes.byref(bin_), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _deobf_legacy(s):
    # compat: contraseñas de versiones previas ofuscadas con XOR ("obf:")
    try:
        key = b"piedrasonic-2026"
        x = base64.b64decode(s[4:])
        return bytes(c ^ key[i % len(key)] for i, c in enumerate(x)).decode("utf-8", "ignore")
    except Exception:
        return ""


def unprotect(s):
    # Lee la contraseña venga como venga: en claro (lo de ahora), o cifrada de
    # versiones anteriores (`dpapi:` / `obf:`), que se sigue descifrando para no
    # perder la cuenta al actualizar. Al guardar se reescribe en claro.
    if not isinstance(s, str) or not s:
        return ""
    if s.startswith("obf:"):
        return _deobf_legacy(s)     # config antigua ofuscada
    if not s.startswith("dpapi:"):
        return s                    # en claro
    try:
        raw = base64.b64decode(s[6:])
        val = _dpapi(ctypes.windll.crypt32.CryptUnprotectData, raw).decode("utf-8", "ignore")
        return _deobf_legacy(val) if val.startswith("obf:") else val
    except Exception:
        return ""       # cifrada para otro usuario/PC -> pedir login de nuevo


# --- config ---------------------------------------------------------------
def load_config():
    try:
        # utf-8-sig y no utf-8: si algo reescribe config.json con BOM -el
        # Bloc de notas, PowerShell, cualquier editor de Windows- json.load
        # revienta con utf-8 a secas, y aqui eso se traduce en perder la
        # cuenta sin decir nada y volver a pedir usuario y contrasena.
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg.setdefault("server", "")
    cfg.setdefault("username", "")
    cfg.setdefault("user_agent", DEFAULT_UA)
    cfg.setdefault("load_on_start", True)   # pedir la lista al abrir el programa
    cfg["password"] = unprotect(cfg.get("password", ""))   # en memoria, en claro
    return cfg


def save_config(cfg):
    # La contraseña del IPTV (`password`) se guarda EN CLARO, legible, para que
    # el config.json se pueda leer y copiar entre máquinas. Antes iba cifrada con
    # DPAPI (atada a este PC y a este usuario); se quitó a propósito. El precio:
    # queda en claro en el disco. config.json no se versiona (`.gitignore`).
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def has_credentials(cfg):
    return bool(cfg.get("server") and cfg.get("username") and cfg.get("password"))


def prueba_cuenta(widget, cfg, servidor, usuario, clave, va, no_va):
    """Comprueba unas credenciales contra el panel, sin bloquear la ventana.

    Devuelve el servidor ya normalizado, o None si falta algún campo. El
    resultado llega por `va(servidor, usuario, clave)` o `no_va(mensaje)`,
    siempre en el hilo de Tk: el login tarda lo que tarde el servidor, y
    hacerlo en el hilo de la interfaz la deja congelada.

    Vive aquí y no en el diálogo porque lo usan dos sitios: la ventana del
    primer arranque y el panel de ajustes de dentro del programa.
    """
    s = (servidor or "").strip().rstrip("/")
    u = (usuario or "").strip()
    p = (clave or "").strip()
    if not (s and u and p):
        return None
    if "://" not in s:
        s = "https://" + s

    hecho = []            # la unica puerta entre el hilo que prueba y Tk

    def trabaja():
        try:
            XtreamClient(s, u, p, user_agent=cfg.get("user_agent", DEFAULT_UA)).login()
            hecho.append((True, None))
        except Exception as e:
            msg = str(e)
            if len(msg) > 60:
                msg = msg[:60] + "…"
            hecho.append((False, msg))

    def mira():
        # Se pregunta desde el hilo de Tk. Llamar a `after` desde el hilo de
        # trabajo parece que funciona y no lo es: Tcl revienta con "main thread
        # is not in main loop" en cuanto la ventana no esta dentro de su bucle,
        # y ahi la comprobacion se quedaba colgada en "Conectando..." para
        # siempre. La norma de la casa vale tambien aqui: los widgets, solo
        # desde su hilo.
        if not hecho:
            widget.after(120, mira)
            return
        bien, msg = hecho[0]
        va(s, u, p) if bien else no_va(msg)

    threading.Thread(target=trabaja, daemon=True).start()
    widget.after(120, mira)
    return s


# --- diálogo de ajustes ---------------------------------------------------
def settings_dialog(parent, cfg, on_success, first=False, icon=None):
    """Los ajustes del programa: la cuenta y poco más.

    La cuenta se valida con login() antes de guardarla y `on_success(server,
    user, password)` se llama solo si conecta. Lo demás son preferencias
    sueltas y se guardan al tocarlas, sin pasar por el botón de conectar: son
    de otra naturaleza, y hacerlas depender de que la cuenta valide sería
    absurdo.

    En el primer arranque solo se enseña la cuenta: aún no hay programa del que
    ajustar nada.
    """
    win = ctk.CTkToplevel(parent)
    win.title(("Cuenta" if first else "Ajustes") + " · piedrasonic")
    win.configure(fg_color=C["surface"])
    win.geometry("440x400" if first else "440x470")   # provisional: ver _ajusta_alto
    win.resizable(False, False)
    win.transient(parent)
    win.after(80, win.grab_set)
    if icon:
        win.after(250, lambda: _try(win.iconbitmap, icon))

    ctk.CTkLabel(win, text="Acceder a tu IPTV" if first else "Ajustes",
                 font=font(17, "bold"), text_color=C["text"]).pack(
                     anchor="w", padx=24, pady=(22, 2))
    ctk.CTkLabel(win, text="Cuenta · protocolo Xtream Codes",
                 font=font(11), text_color=C["muted"]).pack(anchor="w", padx=24)

    def field(label, initial, show=None):
        ctk.CTkLabel(win, text=label, text_color=C["muted"],
                     font=font(11)).pack(anchor="w", padx=24, pady=(12, 2))
        e = ctk.CTkEntry(win, fg_color=C["surface2"], border_width=0, height=36,
                         font=font(12), show=show)
        if initial:
            e.insert(0, initial)
        e.pack(fill=tk.X, padx=24)
        return e

    srv = field("Servidor", cfg.get("server") or DEFAULT_SERVER)
    usr = field("Usuario", cfg.get("username", ""))
    pwd = field("Contraseña", cfg.get("password", ""))

    status = ctk.CTkLabel(win, text="", text_color=C["muted"], font=font(11))
    status.pack(anchor="w", padx=24, pady=(8, 0))

    if not first:
        # Una preferencia, no parte del formulario: se guarda al tocarla. Si
        # esperase al boton de conectar, cambiarla obligaria a revalidar la
        # cuenta y a volver a descargar la lista entera, que es justo lo que
        # esta casilla sirve para evitar.
        # Un rotulo de seccion y no una raya: una linea de 1 px sobre este
        # fondo no se ve por mucho que se suba el color, y el rotulo ademas
        # dice de que va lo que viene, igual que el de la cuenta ahi arriba.
        ctk.CTkLabel(win, text="Arranque", font=font(11),
                     text_color=C["muted"]).pack(anchor="w", padx=24, pady=(14, 6))
        arranque = ctk.BooleanVar(value=bool(cfg.get("load_on_start", True)))

        def cambia_arranque():
            cfg["load_on_start"] = bool(arranque.get())
            _try(save_config, cfg)

        ctk.CTkCheckBox(win, text="Cargar la lista de canales al iniciar",
                        variable=arranque, command=cambia_arranque,
                        font=font(12), text_color=C["text"],
                        fg_color=C["accent"], hover_color=C["accent_hi"],
                        checkbox_width=18, checkbox_height=18,
                        corner_radius=5).pack(anchor="w", padx=24)
        ctk.CTkLabel(win, text="Si lo desmarcas, al abrir verás la última lista\n"
                              "guardada y la actualizas cuando quieras.",
                     font=font(11), text_color=C["muted"],
                     justify="left").pack(anchor="w", padx=48, pady=(4, 0))

    bar = ctk.CTkFrame(win, fg_color="transparent")
    bar.pack(fill=tk.X, padx=24, pady=14, side=tk.BOTTOM)

    def do_connect():
        def va(s, u, p):
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()
            on_success(s, u, p)

        def no_va(msg):
            connect_btn.configure(state="normal")
            status.configure(text=f"No se pudo conectar. {msg}", text_color=C["danger"])

        if prueba_cuenta(win, cfg, srv.get(), usr.get(), pwd.get(), va, no_va) is None:
            status.configure(text="Rellena todos los campos.", text_color=C["warn"])
            return
        status.configure(text="Conectando…", text_color=C["muted"])
        connect_btn.configure(state="disabled")

    connect_btn = ctk.CTkButton(bar, text="Conectar", height=38, corner_radius=10,
                                fg_color=C["accent"], hover_color=C["accent_hi"],
                                text_color="#ffffff", font=font(12, "bold"),
                                command=do_connect)
    connect_btn.pack(side=tk.RIGHT)
    if not first:
        ctk.CTkButton(bar, text="Cancelar", height=38, corner_radius=10,
                      fg_color=C["surface2"], hover_color=C["surface3"],
                      text_color=C["muted"], font=font(12),
                      command=win.destroy).pack(side=tk.RIGHT, padx=8)

    for e in (srv, usr, pwd):
        e.bind("<Return>", lambda ev: do_connect())

    _ajusta_alto(win, 440)
    if first:
        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), parent.destroy()))
    return win


def _ajusta_alto(win, ancho):
    """Le da a la ventana el alto que pide su contenido.

    Con un alto escrito a mano la fila de botones se quedaba aplastada —15 px
    de los 66 que pide— y no habia manera de leer «Conectar» ni «Cancelar»: se
    salian por debajo. Y no es cuestion de poner un numero mas grande, porque
    el que hace falta depende del escalado de la pantalla: aqui, al 175%, la
    ventana pide 499 px donde al 100% pediria 285.
    """
    try:
        win.update_idletasks()
        esc = ctk.ScalingTracker.get_window_scaling(win)
        alto = round(win.winfo_reqheight() / esc)
        win.geometry("%dx%d" % (ancho, alto))
    except Exception:
        pass          # con el alto provisional se ve algo justo, pero se ve


def _try(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass
