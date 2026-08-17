"""
Reproductor VLC embebido con controles en capas flotantes TRANSLÚCIDAS.
El vídeo ocupa toda la superficie; las barras de control son ventanas sin
borde con canal alfa (transparencia real, -topmost) posicionadas sobre el
vídeo. Se muestran/ocultan con un clic y se esconden cuando la app pierde el
foco (para no flotar sobre otras aplicaciones).

Claves de Windows/Tk aprendidas:
  * -topmost y -alpha se fijan AL CREAR la ventana; re-fijar -topmost luego
    resetea su geometría.
  * Se oculta moviéndola fuera de pantalla (withdraw/deiconify no re-mapea
    ventanas overrideredirect de forma fiable).
  * Las ventanas -topmost SÍ se dibujan sobre el vídeo nativo de VLC.

Requiere: python-vlc + VLC instalado (libvlc.dll).
"""
import sys
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

OVL = "#0d0d10"
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
    """Ventana translúcida sin borde, siempre por encima, usada como capa."""
    def __init__(self, master):
        super().__init__(master)
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)   # AL CREAR (no re-fijar luego)
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
                 request_fullscreen=None):
        super().__init__(master, fg_color=C["video"], corner_radius=0)
        self.user_agent = user_agent
        self.network_caching = network_caching
        self.request_fullscreen = request_fullscreen
        self._seeking = False
        self._live = True
        self._overlay_on = True
        self._actions_on = False
        self._active = True
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
        ctk.CTkLabel(self.bar, text="🔊", text_color=C["text"],
                     font=font(13)).pack(side=tk.LEFT, padx=(2, 2))
        self.vol = ctk.CTkSlider(self.bar, from_=0, to=100, width=90, height=16,
                                 button_color=C["text"], button_hover_color="#ffffff",
                                 progress_color=C["muted"], fg_color=C["surface3"],
                                 command=self._on_vol)
        self.vol.set(90)
        self.vol.pack(side=tk.LEFT, padx=(0, 6))
        _icon(self.bar, "⛶", self._fs_click, 34, 14).pack(side=tk.LEFT, padx=(4, 14), pady=8)

        self._bind_hwnd()
        self._rootwin = self.winfo_toplevel()
        # reposicionar SÍNCRONO en cada resize/move (root y vídeo) para que las
        # capas no se queden descolgadas al arrastrar el borde de la ventana
        self._rootwin.bind("<Configure>", self._reposition, add="+")
        self.video.bind("<Configure>", self._reposition, add="+")
        self._rootwin.bind("<FocusIn>", self._on_focus_in, add="+")
        self._rootwin.bind("<FocusOut>", self._on_focus_out, add="+")
        # movimiento del ratón sobre vídeo o barras -> revela en pantalla completa
        for w in (self.top, self.top.inner, self.bottom, self.bottom.inner):
            w.bind("<Motion>", self._on_motion, add="+")
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
        if not self._overlay_on or not self._active:
            return False
        if self._fs_mode and not self._revealed:
            return False
        try:
            return self._rootwin.state() not in ("iconic", "withdrawn")
        except Exception:
            return True

    def _refresh(self):
        if self._want() and self._place():
            pass
        else:
            self.top.hide_off()
            self.bottom.hide_off()

    def _reposition(self, _e=None):
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

    def _on_focus_in(self, _e):
        self._active = True
        self._refresh()

    def _on_focus_out(self, _e):
        self.after(120, self._check_active)

    def _check_active(self):
        self._active = self._is_foreground()
        self._refresh()

    def _is_foreground(self):
        """True si NUESTRA ventana principal es la activa del sistema.
        Evita que las barras -topmost floten sobre otras apps o sobre
        nuestros propios diálogos."""
        if not sys.platform.startswith("win"):
            try:
                return self.focus_get() is not None
            except Exception:
                return True
        try:
            import ctypes
            u = ctypes.windll.user32
            fg = u.GetForegroundWindow()
            root_hwnd = u.GetAncestor(self._rootwin.winfo_id(), 2)  # GA_ROOT
            return int(fg) == int(root_hwnd)
        except Exception:
            return True

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
        else:
            self.toggle_overlay()

    def _on_motion(self, _e=None):
        if self._fs_mode and self._want_base():
            self._reveal()

    def _want_base(self):
        # como _want pero ignorando el estado de revelado (para poder revelar)
        if not self._overlay_on or not self._active:
            return False
        try:
            return self._rootwin.state() not in ("iconic", "withdrawn")
        except Exception:
            return True

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
        if VLC_OK:
            self.mp.audio_set_volume(int(float(self.vol.get())))

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

    def _tick(self):
        # sondeo de ventana activa: oculta las barras si la app no está al frente
        try:
            act = self._is_foreground()
            if act != self._active:
                self._active = act
                self._refresh()
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
