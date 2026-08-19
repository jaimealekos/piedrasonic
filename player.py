"""
Reproductor VLC embebido con controles en capas flotantes TRANSLÚCIDAS.
El vídeo ocupa toda la superficie; las barras de control son ventanas sin
borde con canal alfa, PROPIEDAD (owner) de la ventana principal y SIN
-topmost: al ser propiedad del root van siempre por encima de él (y del
vídeo nativo de VLC), pero por debajo de cualquier otra aplicación que nos
tape, así que Windows las recorta solo — nada de regiones ni sondeos de
foco. Minimizar el root las esconde también solo (regla de owned windows).

Claves de Windows/Tk aprendidas:
  * -alpha se fija AL CREAR la ventana.
  * Se oculta moviéndola fuera de pantalla (withdraw/deiconify no re-mapea
    ventanas overrideredirect de forma fiable).
  * El dueño se fija con GWLP_HWNDPARENT; una ventana owned se dibuja sobre
    su dueño sin necesidad de -topmost (también sobre el hijo de VLC).
  * SetWindowRgn sobre ventanas con -alpha (layered) es terreno minado: se
    probó para recortar las barras y provocaba cuelgues y redibujos rotos.

Requiere: python-vlc + VLC instalado (libvlc.dll).
"""
import os
import re
import sys
import time
from datetime import datetime
import tkinter as tk
import customtkinter as ctk
from theme import C, font

try:
    import vlc
    VLC_OK = True
    VLC_ERR = None
except Exception as e:                # pragma: no cover
    VLC_OK = False
    VLC_ERR = e

if sys.platform.startswith("win"):
    import ctypes

    _GA_ROOT = 2
    _GWLP_HWNDPARENT = -8
    _U = ctypes.windll.user32          # objeto cacheado: firmas, una sola vez
    _U.GetAncestor.restype = ctypes.c_void_p
    _U.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    _SETPTR = getattr(_U, "SetWindowLongPtrW", _U.SetWindowLongW)
    _SETPTR.restype = ctypes.c_void_p
    _SETPTR.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    _U.WindowFromPoint.restype = ctypes.c_void_p
    _U.WindowFromPoint.argtypes = [_POINT]


def _desktop_dir():
    """Carpeta real del Escritorio (aguanta OneDrive y Windows en español)."""
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        try:
            class _GUID(ctypes.Structure):
                _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort),
                            ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]
            fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,      # FOLDERID_Desktop
                        (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                             0x9A, 0x87, 0xC6, 0x41))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(fid), 0, None, ctypes.byref(p)) == 0:
                d = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                if d and os.path.isdir(d):
                    return d
        except Exception:
            pass
    d = os.path.join(home, "Desktop")
    return d if os.path.isdir(d) else home


OVL = "#0d0d10"
ASPECT_MIN = 16 / 9                # nunca más estrecho que 16:9
ASPECT_MAX = 3.0
ALPHA = 0.78
OFFSCREEN = "1x1+-10000+-10000"


def _fmt(ms):
    if ms is None or ms < 0:
        return "--:--"
    s = int(ms // 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _icon(master, glyph, cmd, size=34, fsize=15):
    return ctk.CTkButton(master, text=glyph, width=size, height=size,
                         corner_radius=size // 2, fg_color="transparent",
                         hover_color=C["surface3"], text_color=C["text"],
                         font=font(fsize), command=cmd)


class _Bar(tk.Toplevel):
    """Ventana translúcida sin borde usada como capa sobre el vídeo. No es
    -topmost: se le pone como dueño el root (ver _own_bars), con lo que va
    encima de él pero debajo de las demás aplicaciones."""
    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        try:
            self.attributes("-alpha", ALPHA)
        except Exception:
            pass
        self.configure(bg=OVL)
        self.geometry(OFFSCREEN)
        self.inner = ctk.CTkFrame(self, fg_color=OVL, corner_radius=0)
        self.inner.pack(fill=tk.BOTH, expand=True)

    def place_over(self, x, y, w, h):
        self.geometry(f"{max(w,1)}x{max(h,1)}+{int(x)}+{int(y)}")

    def hide_off(self):
        self.geometry(OFFSCREEN)


class VlcPlayer(ctk.CTkFrame):
    def __init__(self, master, user_agent="VLC/3.0", network_caching=1500,
                 request_fullscreen=None, request_panels=None, on_aspect=None,
                 request_ontop=None, snapshot_dir=None, volume=90, muted=False):
        super().__init__(master, fg_color=C["video"], corner_radius=0)
        self.user_agent = user_agent
        self.network_caching = network_caching
        self.request_fullscreen = request_fullscreen
        self.request_panels = request_panels
        self.on_aspect = on_aspect
        self.request_ontop = request_ontop
        self.snapshot_dir = snapshot_dir
        self._seeking = False
        self._live = True
        self._overlay_on = True
        self._actions_on = False
        self._focus_ts = 0.0           # última vez que la app ganó el foco
        self._pointer_in = False       # puntero sobre el vídeo o las barras
        self._leave_after = None
        self._resize_ts = 0.0          # último Configure (arrastre del borde)
        self._resize_after = None
        self._muted = False
        self._vol_prev = int(volume) if int(volume) > 0 else 90
        self._ar = ASPECT_MIN
        self._cfg_after = None
        self._idle_pending = False
        self._fs_mode = False
        self._revealed = True          # en ventana siempre visible
        self._hide_after = None
        self._anim = None

        args = ["--no-video-title-show", "--quiet", "--intf", "dummy",
                f"--network-caching={network_caching}",
                f"--http-user-agent={user_agent}"]
        self.instance = vlc.Instance(args) if VLC_OK else None
        self.mp = self.instance.media_player_new() if VLC_OK else None

        # ---------- superficie de vídeo ----------
        self.video = tk.Frame(self, bg=C["video"], highlightthickness=0, bd=0)
        self.video.place(x=0, y=0, relwidth=1, relheight=1)
        self.placeholder = ctk.CTkLabel(self.video, text="●  IPTV",
                                        text_color=C["faint"], fg_color=C["video"],
                                        font=font(22, "bold"))
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self.video.bind("<Button-1>", lambda e: self._on_video_click())
        self.video.bind("<Double-1>", lambda e: self._fs_click())
        self.video.bind("<Motion>", self._on_motion)
        self.video.bind("<Enter>", self._on_enter)
        self.video.bind("<Leave>", self._on_leave)

        # ---------- capas ----------
        self.top = _Bar(self)
        self.bottom = _Bar(self)

        self.title_lbl = ctk.CTkLabel(self.top.inner, text="", text_color=C["text"],
                                      font=font(14, "bold"), anchor="w")
        self.title_lbl.pack(side=tk.LEFT, padx=(16, 10))
        self.epg_lbl = ctk.CTkLabel(self.top.inner, text="", text_color=C["muted"],
                                    font=font(11), anchor="e")
        self.epg_lbl.pack(side=tk.RIGHT, padx=(10, 16))

        self.actions = ctk.CTkFrame(self.bottom.inner, fg_color="transparent")
        self.bar = ctk.CTkFrame(self.bottom.inner, fg_color="transparent")
        self.bar.pack(side=tk.BOTTOM, fill=tk.X)

        self.btn_play = _icon(self.bar, "▶", self.toggle_pause, 38, 16)
        self.btn_play.pack(side=tk.LEFT, padx=(14, 2), pady=8)
        _icon(self.bar, "⏹", self.stop, 34, 13).pack(side=tk.LEFT, padx=2, pady=8)
        self.time_lbl = ctk.CTkLabel(self.bar, text="--:--", width=52,
                                     text_color=C["text"], font=font(11))
        self.time_lbl.pack(side=tk.LEFT, padx=(8, 4))
        self.seek = ctk.CTkSlider(self.bar, from_=0, to=1000, height=16,
                                  button_color=C["accent"], button_hover_color=C["accent_hi"],
                                  progress_color=C["accent"], fg_color=C["surface3"],
                                  command=self._on_seek_move)
        self.seek.set(1000)
        self.seek.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.seek.bind("<Button-1>", lambda e: setattr(self, "_seeking", True))
        self.seek.bind("<ButtonRelease-1>", self._on_seek_release)
        self.dur_lbl = ctk.CTkLabel(self.bar, text="", width=52,
                                    text_color=C["text"], font=font(11))
        self.dur_lbl.pack(side=tk.LEFT, padx=(4, 8))
        self.live_badge = ctk.CTkLabel(self.bar, text="● DIRECTO",
                                       text_color=C["danger"], font=font(11, "bold"))
        self.live_badge.pack(side=tk.LEFT, padx=(4, 10))
        self.btn_mute = _icon(self.bar, "🔊", self.toggle_mute, 30, 13)
        self.btn_mute.pack(side=tk.LEFT, padx=(2, 2), pady=8)
        self.vol = ctk.CTkSlider(self.bar, from_=0, to=100, width=90, height=16,
                                 button_color=C["text"], button_hover_color="#ffffff",
                                 progress_color=C["muted"], fg_color=C["surface3"],
                                 command=self._on_vol)
        self.vol.set(self._vol_prev)
        self.vol.pack(side=tk.LEFT, padx=(0, 6))
        if muted:
            self.toggle_mute(True)
        self.btn_snap = _icon(self.bar, "📷", self.snapshot, 34, 13)
        self.btn_snap.pack(side=tk.LEFT, padx=(4, 0), pady=8)
        self.btn_ontop = _icon(self.bar, "📌", self._ontop_click, 34, 13)
        self.btn_ontop.pack(side=tk.LEFT, padx=(4, 0), pady=8)
        self.btn_panels = _icon(self.bar, "◧", self._panels_click, 34, 17)
        self.btn_panels.pack(side=tk.LEFT, padx=(4, 0), pady=8)
        _icon(self.bar, "⛶", self._fs_click, 34, 14).pack(side=tk.LEFT, padx=(4, 14), pady=8)

        self._bind_hwnd()
        self._rootwin = self.winfo_toplevel()
        self._own_bars()
        # reposicionar SÍNCRONO en cada resize/move (root y vídeo) para que las
        # capas no se queden descolgadas al arrastrar el borde de la ventana
        self._rootwin.bind("<Configure>", self._reposition, add="+")
        self.video.bind("<Configure>", self._reposition, add="+")
        self._rootwin.bind("<FocusIn>", self._on_focus_in, add="+")
        # movimiento del ratón sobre vídeo o barras -> revela en pantalla completa
        for w in (self.top, self.top.inner, self.bottom, self.bottom.inner):
            w.bind("<Motion>", self._on_motion, add="+")
        for w in (self.top, self.bottom):
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
        self.after(120, self._refresh)
        self.after(500, self._tick)
        if not VLC_OK:
            self.placeholder.configure(text="VLC no disponible\n" + str(VLC_ERR),
                                       font=font(12))

    # ---- embedding -----------------------------------------------------
    def _bind_hwnd(self):
        if not VLC_OK:
            return
        self.update_idletasks()
        hwnd = self.video.winfo_id()
        if sys.platform.startswith("win"):
            self.mp.set_hwnd(hwnd)
        elif sys.platform == "darwin":
            self.mp.set_nsobject(hwnd)
        else:
            self.mp.set_xwindow(hwnd)
        try:
            self.mp.video_set_mouse_input(False)
            self.mp.video_set_key_input(False)
        except Exception:
            pass

    # ---- posicionamiento de capas -------------------------------------
    def _geom(self):
        try:
            if not self.video.winfo_ismapped():
                return None
            vx = self.video.winfo_rootx()
            vy = self.video.winfo_rooty()
            vw = self.video.winfo_width()
            vh = self.video.winfo_height()
            if vw < 40 or vh < 40:
                return None
            m = 14
            top_h = 44
            bar_h = 58 + (40 if self._actions_on else 0)
            return (vx, vy, vw, vh, m, top_h, bar_h)
        except Exception:
            return None

    def _place(self, prog=1.0):
        g = self._geom()
        if not g:
            return False
        vx, vy, vw, vh, m, top_h, bar_h = g
        # prog<1 => deslizando: arriba baja desde el borde, abajo sube desde abajo
        ot = int((1 - prog) * (top_h + m + 12))
        ob = int((1 - prog) * (bar_h + m + 12))
        self.top.place_over(vx + m, vy + m - ot, vw - 2 * m, top_h)
        self.bottom.place_over(vx + m, vy + vh - bar_h - m + ob, vw - 2 * m, bar_h)
        return True

    def _want(self):
        if not self._want_base():
            return False
        return not (self._fs_mode and not self._revealed)

    def _refresh(self):
        if self._want() and self._place():
            pass
        else:
            self.top.hide_off()
            self.bottom.hide_off()

    def _reposition(self, _e=None):
        # mientras dura el arrastre del borde las barras se quedan puestas; al
        # acabar se vuelve a mirar si el ratón sigue encima
        self._resize_ts = time.time()
        if self._resize_after:
            try:
                self.after_cancel(self._resize_after)
            except Exception:
                pass
        self._resize_after = self.after(900, self._resize_done)
        # inmediato (sigue el arrastre) + pasada after_idle (fija la posición
        # final exacta cuando el layout se ha asentado) => sin lag residual
        if self._want() and self._place():
            if not self._idle_pending:
                self._idle_pending = True
                self.after_idle(self._settle)
            return
        self.top.hide_off()
        self.bottom.hide_off()

    def _settle(self):
        self._idle_pending = False
        if self._want():
            self._place()

    def _resize_done(self):
        # al expirar el margen hay que RE-EVALUAR sí o sí: aunque el ratón no
        # haya cambiado de sitio, la excusa de "se está redimensionando" acaba
        # de caducar y con ella la razón por la que estaban puestas
        self._resize_after = None
        self._pointer_in = self._pointer_over()
        self._refresh()

    def _on_focus_in(self, _e):
        # marca de tiempo: el clic que ACTIVA la ventana no debe además
        # alternar los controles (ver _on_video_click)
        self._focus_ts = time.time()
        self._refresh()

    @staticmethod
    def _hwnd(w):
        """HWND de la ventana de nivel superior que contiene al widget."""
        return _U.GetAncestor(w.winfo_id(), _GA_ROOT)

    def _own_bars(self):
        """Hace a las barras PROPIEDAD del root (GWLP_HWNDPARENT): quedan por
        encima de él y del vídeo sin -topmost, y por debajo de las demás
        aplicaciones, que las recortan/tapan de forma natural."""
        if not sys.platform.startswith("win"):
            try:
                for w in (self.top, self.bottom):
                    w.wm_transient(self._rootwin)
            except Exception:
                pass
            return
        try:
            root_h = self._hwnd(self._rootwin)
            for w in (self.top, self.bottom):
                _SETPTR(self._hwnd(w), _GWLP_HWNDPARENT, root_h)
                w.lift()
        except Exception:
            pass

    # ---- pantalla completa: auto-ocultar y revelar al mover el ratón ---
    def set_fullscreen(self, on):
        self._fs_mode = bool(on)
        if on:
            self._revealed = False       # empieza oculto en fullscreen
            self._refresh()
        else:
            self._anim_cancel()
            if self._hide_after:
                try:
                    self.after_cancel(self._hide_after)
                except Exception:
                    pass
                self._hide_after = None
            self._revealed = True        # en ventana siempre visible
            self._refresh()

    def _on_video_click(self):
        if self._fs_mode:
            self._reveal()
        elif time.time() - self._focus_ts > 0.35:
            # el clic que trae la ventana al frente no alterna los controles
            self.toggle_overlay()

    def _on_motion(self, _e=None):
        if not self._pointer_in:
            self._on_enter()
        if self._fs_mode and self._want_base():
            self._reveal()

    def _on_enter(self, _e=None):
        self._leave_cancel()
        if not self._pointer_in:
            self._pointer_in = True
            self._refresh()

    def _on_leave(self, _e=None):
        # con margen: al cruzar del vídeo a la barra hay un Leave transitorio
        self._leave_cancel()
        self._leave_after = self.after(250, self._leave_check)

    def _leave_cancel(self):
        if self._leave_after:
            try:
                self.after_cancel(self._leave_after)
            except Exception:
                pass
            self._leave_after = None

    def _leave_check(self):
        self._leave_after = None
        dentro = self._pointer_over()
        if dentro != self._pointer_in:
            self._pointer_in = dentro
            self._refresh()

    def _pointer_over(self):
        """True si el puntero está de verdad sobre NUESTRO vídeo o barras (si
        otra ventana tapa ese punto, el punto es suyo y devuelve False)."""
        try:
            px, py = self.winfo_pointerxy()
        except Exception:
            return False
        if not sys.platform.startswith("win"):
            try:
                x, y = self.video.winfo_rootx(), self.video.winfo_rooty()
                return (x <= px < x + self.video.winfo_width()
                        and y <= py < y + self.video.winfo_height())
            except Exception:
                return False
        try:
            under = _U.WindowFromPoint(_POINT(int(px), int(py)))
            if not under:
                return False
            mine = {self._hwnd(w) for w in (self._rootwin, self.top, self.bottom)}
            return _U.GetAncestor(under, _GA_ROOT) in mine
        except Exception:
            return False

    def _want_base(self):
        """ÚNICO sitio donde se decide si debe haber barras (ignorando el
        auto-ocultado de pantalla completa, que va en _want):

          * los controles alternados con un clic mandan sobre todo lo demás;
          * en ventana solo se ven con el ratón encima del reproductor...
          * ...salvo mientras se redimensiona o se mueve la ventana: ahí el
            puntero está en el borde, fuera del vídeo, y ocultarlos sería
            justo lo contrario de lo que uno quiere ver al ajustar el tamaño;
          * minimizada, nunca.
        """
        if not self._overlay_on:
            return False
        if not self._fs_mode and not self._pointer_in and not self._resizing():
            return False
        try:
            return self._rootwin.state() not in ("iconic", "withdrawn")
        except Exception:
            return True

    def _resizing(self):
        return (time.time() - self._resize_ts) < 0.8

    def _reveal(self):
        if self._hide_after:
            try:
                self.after_cancel(self._hide_after)
            except Exception:
                pass
        self._hide_after = self.after(5000, self._unreveal)
        if not self._revealed:
            self._revealed = True
            self._slide_in()
        else:
            self._place(1.0)

    def _unreveal(self):
        self._hide_after = None
        self._revealed = False
        self._refresh()

    def _slide_in(self):
        self._anim_cancel()

        def step(i):
            prog = min(i / 6.0, 1.0)
            if not (self._want() and self._place(prog)):
                return
            if i < 6:
                self._anim = self.after(16, lambda: step(i + 1))
            else:
                self._anim = None
        step(0)

    def _anim_cancel(self):
        if self._anim:
            try:
                self.after_cancel(self._anim)
            except Exception:
                pass
            self._anim = None

    def toggle_overlay(self, show=None):
        self._overlay_on = (not self._overlay_on) if show is None else bool(show)
        self._refresh()

    def enable_actions(self, on):
        self._actions_on = bool(on)
        if on:
            self.actions.pack(side=tk.TOP, fill=tk.X, before=self.bar, pady=(6, 0))
        else:
            self.actions.pack_forget()
        self._refresh()

    # compat
    def hide_controls(self):
        self.toggle_overlay(False)

    def show_controls(self):
        self.toggle_overlay(True)

    # ---- API -----------------------------------------------------------
    def play(self, url, title="", live=True):
        if not VLC_OK:
            return
        self._live = live
        self.placeholder.place_forget()
        media = self.instance.media_new(url)
        media.add_option(f":http-user-agent={self.user_agent}")
        media.add_option(f":network-caching={self.network_caching}")
        self.mp.set_media(media)
        self.mp.audio_set_volume(int(self.vol.get()))
        self.mp.play()
        self.btn_play.configure(text="⏸")
        self.title_lbl.configure(text=title)
        if live:
            self.seek.configure(state="disabled")
            self.live_badge.configure(text="● DIRECTO", text_color=C["danger"])
        else:
            self.seek.configure(state="normal")
            self.live_badge.configure(text="VOD", text_color=C["muted"])
        self._overlay_on = True
        for d in (200, 800, 1600):
            self.after(d, self._refresh)

    def set_epg(self, text):
        self.epg_lbl.configure(text=text)

    def toggle_pause(self):
        if not VLC_OK or self.mp.get_media() is None:
            return
        self.mp.pause()
        self.btn_play.configure(text="⏸" if self.mp.is_playing() else "▶")

    def stop(self):
        if not VLC_OK:
            return
        self.mp.stop()
        self.btn_play.configure(text="▶")
        self.placeholder.configure(text="●  IPTV", font=font(22, "bold"))
        self.placeholder.place(relx=0.5, rely=0.5, anchor="center")

    def _on_vol(self, _v):
        v = int(float(self.vol.get()))
        if v:
            self._vol_prev = v
        self._set_muted(v == 0)
        self._apply_vol()

    def toggle_mute(self, on=None):
        target = (not self._muted) if on is None else bool(on)
        self.vol.set(0 if target else (self._vol_prev or 50))
        self._set_muted(target)
        self._apply_vol()

    def _set_muted(self, on):
        self._muted = bool(on)
        self.btn_mute.configure(text="🔇" if self._muted else "🔊")

    def _apply_vol(self):
        if VLC_OK:
            self.mp.audio_set_volume(int(float(self.vol.get())))

    def get_volume_state(self):
        """(nivel, silenciado) para recordarlo entre sesiones."""
        nivel = self._vol_prev if self._muted else int(float(self.vol.get()))
        return nivel, self._muted

    def _on_seek_move(self, _v):
        if self._seeking:
            self.time_lbl.configure(text=_fmt(self._target_ms()))

    def _target_ms(self):
        dur = self.mp.get_length() if VLC_OK else 0
        return int(dur * (float(self.seek.get()) / 1000.0)) if dur else 0

    def _on_seek_release(self, _e):
        if VLC_OK and not self._live and self.mp.get_length() > 0:
            self.mp.set_position(float(self.seek.get()) / 1000.0)
        self._seeking = False

    def _fs_click(self):
        if callable(self.request_fullscreen):
            self.request_fullscreen()

    def aspect(self):
        """Proporción (ancho/alto) que debe tener la superficie para que el
        vídeo no salga con franjas arriba y abajo. Nunca baja de 16:9: si el
        canal es más estrecho (4:3, SD anamórfico) las franjas caen a los
        lados, que es justo lo que se busca."""
        return self._ar

    def _read_aspect(self):
        """Proporción del vídeo en curso, o None si todavía no se sabe."""
        if not VLC_OK or self.mp is None:
            return None
        try:
            w, h = self.mp.video_get_size(0)      # (0, 0) hasta que arranca
        except Exception:
            return None
        if not (w and h):
            return None
        return min(max(w / float(h), ASPECT_MIN), ASPECT_MAX)

    def _panels_click(self):
        if callable(self.request_panels):
            self.request_panels()

    def _ontop_click(self):
        if callable(self.request_ontop):
            self.request_ontop()

    def set_ontop(self, on):
        """El root cambia de banda (-topmost): las barras tienen que ir CON él,
        o quedarían por debajo del propio root y desaparecerían."""
        self.btn_ontop.configure(text_color=C["accent"] if on else C["text"])
        for w in (self.top, self.bottom):
            try:
                w.attributes("-topmost", bool(on))
                w.lift()
            except Exception:
                pass
        self._refresh()               # re-fijar -topmost puede mover la geometría

    def snapshot(self):
        """Guarda un PNG del fotograma actual y devuelve la ruta (o None)."""
        if not VLC_OK or self.mp.get_media() is None:
            return None
        d = self.snapshot_dir
        if not (d and os.path.isdir(d)):
            d = _desktop_dir()
        base = re.sub(r"[^\w\- ]", "", self.title_lbl.cget("text")).strip()[:40]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(d, f"{base or 'piedrasonic'} {stamp}.png")
        ok = self.mp.video_take_snapshot(0, path, 0, 0) == 0
        self.btn_snap.configure(text="✓" if ok else "✕")
        self.after(900, lambda: self.btn_snap.configure(text="📷"))
        return path if ok else None

    def set_panels_hidden(self, on):
        """Refleja en el botón si las columnas laterales están ocultas."""
        self.btn_panels.configure(text="▭" if on else "◧")

    def _tick(self):
        try:
            ar = self._read_aspect()
            if ar and abs(ar - self._ar) > 0.01:
                self._ar = ar
                if callable(self.on_aspect):
                    self.on_aspect()
        except Exception:
            pass
        try:
            if VLC_OK and self.mp.get_media() is not None and not self._seeking:
                pos = self.mp.get_time()
                dur = self.mp.get_length()
                self.time_lbl.configure(text=_fmt(pos))
                if self._live:
                    self.dur_lbl.configure(text="")
                    self.seek.set(1000)
                else:
                    self.dur_lbl.configure(text=_fmt(dur))
                    if dur > 0:
                        self.seek.set(1000 * pos / dur)
        except Exception:
            pass
        self.after(500, self._tick)

    def release(self):
        try:
            for w in (self.top, self.bottom):
                w.destroy()
        except Exception:
            pass
        try:
            if VLC_OK:
                self.mp.stop()
                self.mp.release()
                self.instance.release()
        except Exception:
            pass
