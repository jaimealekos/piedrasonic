"""
Configuración compartida de piedrasonic:
  * carga/guardado de config.json con la contraseña OFUSCADA (no es cifrado
    real, solo evita el vistazo casual),
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

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DEFAULT_SERVER = "https://max.veraneo.bid"
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


def protect(s):
    if not s:
        return ""
    try:
        enc = _dpapi(ctypes.windll.crypt32.CryptProtectData, s.encode("utf-8"))
        return "dpapi:" + base64.b64encode(enc).decode("ascii")
    except Exception:
        return s        # último recurso: en claro (no debería ocurrir en Windows)


def _deobf_legacy(s):
    # compat: contraseñas de versiones previas ofuscadas con XOR ("obf:")
    try:
        key = b"piedrasonic-2026"
        x = base64.b64decode(s[4:])
        return bytes(c ^ key[i % len(key)] for i, c in enumerate(x)).decode("utf-8", "ignore")
    except Exception:
        return ""


def unprotect(s):
    if not isinstance(s, str) or not s:
        return ""
    if s.startswith("obf:"):
        return _deobf_legacy(s)     # config antigua ofuscada
    if not s.startswith("dpapi:"):
        return s                    # config antigua en claro -> se re-cifra al guardar
    try:
        raw = base64.b64decode(s[6:])
        val = _dpapi(ctypes.windll.crypt32.CryptUnprotectData, raw).decode("utf-8", "ignore")
        return _deobf_legacy(val) if val.startswith("obf:") else val
    except Exception:
        return ""       # cifrada para otro usuario/PC -> pedir login de nuevo


# --- config ---------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg.setdefault("server", "")
    cfg.setdefault("username", "")
    cfg.setdefault("user_agent", DEFAULT_UA)
    cfg["password"] = unprotect(cfg.get("password", ""))   # en memoria, en claro
    return cfg


def save_config(cfg):
    out = dict(cfg)
    out["password"] = protect(cfg.get("password", ""))     # en disco, cifrada (DPAPI)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)


def has_credentials(cfg):
    return bool(cfg.get("server") and cfg.get("username") and cfg.get("password"))


# --- diálogo de cuenta ----------------------------------------------------
def account_dialog(parent, cfg, on_success, first=False, icon=None):
    """Muestra el formulario de acceso. Valida con login() antes de guardar.
    on_success(server, user, password) se llama al conectar correctamente."""
    win = ctk.CTkToplevel(parent)
    win.title("Cuenta · piedrasonic")
    win.configure(fg_color=C["surface"])
    win.geometry("440x400")
    win.resizable(False, False)
    win.transient(parent)
    win.after(80, win.grab_set)
    if icon:
        win.after(250, lambda: _try(win.iconbitmap, icon))

    ctk.CTkLabel(win, text="Acceder a tu IPTV" if first else "Cuenta",
                 font=font(17, "bold"), text_color=C["text"]).pack(
                     anchor="w", padx=24, pady=(22, 2))
    ctk.CTkLabel(win, text="Protocolo Xtream Codes",
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
    pwd = field("Contraseña", cfg.get("password", ""), show="•")

    status = ctk.CTkLabel(win, text="", text_color=C["muted"], font=font(11))
    status.pack(anchor="w", padx=24, pady=(8, 0))

    bar = ctk.CTkFrame(win, fg_color="transparent")
    bar.pack(fill=tk.X, padx=24, pady=14, side=tk.BOTTOM)

    def do_connect():
        s = srv.get().strip().rstrip("/")
        u = usr.get().strip()
        p = pwd.get().strip()
        if not (s and u and p):
            status.configure(text="Rellena todos los campos.", text_color=C["warn"])
            return
        if "://" not in s:
            s = "https://" + s
        status.configure(text="Conectando…", text_color=C["muted"])
        connect_btn.configure(state="disabled")

        def work():
            try:
                XtreamClient(s, u, p, user_agent=cfg.get("user_agent", DEFAULT_UA)).login()
                parent.after(0, ok)
            except Exception as e:
                parent.after(0, lambda: bad(e))

        def ok():
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()
            on_success(s, u, p)

        def bad(e):
            connect_btn.configure(state="normal")
            msg = str(e)
            if len(msg) > 60:
                msg = msg[:60] + "…"
            status.configure(text=f"No se pudo conectar. {msg}", text_color=C["danger"])

        threading.Thread(target=work, daemon=True).start()

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

    if first:
        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), parent.destroy()))
    return win


def _try(fn, *a):
    try:
        fn(*a)
    except Exception:
        pass
