#!/usr/bin/env python3
"""
piedrasonic — TV en vivo (Xtream) con VLC embebido, catch-up y timeshift.
Interfaz minimalista (CustomTkinter). Un clic reproduce; controles translúcidos
sobre el vídeo. Favoritos con grupos desplegables y reordenables. Categorías
configurables. Auto-reproduce el primer canal.

Ejecutar:  pythonw iptv_player.pyw   (o run.bat)
"""
import os
import re
import sys
import json
import time
import threading
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import theme
from theme import C, font
from xtream import XtreamClient
from player import VlcPlayer, VLC_OK
import settings
from settings import load_config, save_config

APP_DIR = settings.DATA_DIR                      # datos del usuario (escribible)
RES_DIR = settings.RES_DIR                       # recursos (icono, estrellas)
CONFIG_PATH = settings.CONFIG_PATH
CACHE_PATH = os.path.join(APP_DIR, "cache.json")
ICON = os.path.join(RES_DIR, "icon.ico")


STAR_ON = "⭐"
STAR_OFF = "☆"
RELOAD = "⟳"

if sys.platform.startswith("win"):
    import ctypes
    from ctypes import wintypes

    WM_SIZING = 0x0214
    GWLP_WNDPROC = -4
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                                 ctypes.c_size_t, ctypes.c_ssize_t)
    # WMSZ_*: qué borde se está arrastrando
    SZ_IZQ = (1, 4, 7)                      # izquierda / sup-izq / inf-izq
    SZ_ARR = (3, 4, 5)                      # arriba / sup-izq / sup-der
    SZ_ALTO = (3, 6)                        # arriba o abajo: manda el alto


CAT_MIN = 214           # ancho mínimo de la columna de categorías
CH_MIN = 330            # ancho mínimo de la columna de canales
MIN_W, MIN_H = 1040, 640                  # tamaño mínimo de la ventana


class LiveApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        theme.init()
        self.cfg = load_config()
        self._rebuild_client()
        # La lista se pide AQUI, antes de crear un solo widget. Levantar la
        # ventana de CustomTkinter cuesta un par de segundos; pidiendo ya la
        # lista, esa espera y la ida y vuelta al servidor se solapan en vez de
        # sumarse: cuando la ventana esta en pie, la respuesta suele estarlo.
        if settings.has_credentials(self.cfg):
            self._prefetch_start()
        self.favorites = set(str(x) for x in self.cfg.get("favorites", []))
        self.fav_groups = self.cfg.get("favorite_groups", [])   # [{name, channels[]}]
        self.hidden = set(str(x) for x in self.cfg.get("hidden_categories", []))
        self.categories = []
        self.streams_by_cat = {}
        self.all_streams = []
        self.by_id = {}
        self.cat_name = {}
        self.current_list = []
        self.current_stream = None
        self.cat_buttons = []
        self.cat_key = "all"
        self._first_load = True
        self._loading = False         # hay una recarga de lista en curso
        self._requeue = False         # ...y se pidio otra mientras corria
        self._fs = False
        self._panels_off = False      # modo solo-reproductor (atajo L)
        self._panels_w = 0            # ancho que ocupaban las dos columnas
        self._ontop = False           # ventana siempre encima (atajo A)
        self._catchup_min = 0         # minutos por detrás del directo
        self._minw = None             # ancho mínimo: no cabe menos sin franjas
        self._shape_cache = None      # (columnas, aspecto) para el arrastre
        self._oldproc = None             # WndProc original (se restaura al cerrar)
        self._last_size = None           # para saber qué borde se arrastra
        self._fit_use_h = False
        self._fit_after = None
        self._fit_both = False        # el cambio de formato ajusta en ambos sentidos

        self.title("piedrasonic")
        self.geometry(self.cfg.get("window_geometry") or "2335x975")
        self.minsize(MIN_W, MIN_H)
        self.configure(fg_color=C["bg"])
        self._apply_icon()
        theme.style_tree(self)
        self.img_on = tk.PhotoImage(file=os.path.join(RES_DIR, "star_on.png"))
        self.img_off = tk.PhotoImage(file=os.path.join(RES_DIR, "star_off.png"))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        if settings.has_credentials(self.cfg):
            self.after(0, lambda: self.load(force=False))
        else:
            self.after(200, lambda: self.open_account(first=True))

    def _rebuild_client(self):
        # Un cambio de cuenta invalida una descarga adelantada en vuelo: sus
        # datos son de OTRO servidor y no deben acabar pintados ni en la cache.
        self._fetch = None
        self.client = XtreamClient(
            self.cfg.get("server", ""), self.cfg.get("username", ""),
            self.cfg.get("password", ""),
            user_agent=self.cfg.get("user_agent", "VLC/3.0"),
            output=self.cfg.get("output", "ts"))

    def open_account(self, first=False):
        def on_success(server, user, password):
            self.cfg["server"] = server
            self.cfg["username"] = user
            self.cfg["password"] = password
            save_config(self.cfg)
            self._rebuild_client()
            try:
                os.remove(CACHE_PATH)      # forzar recarga de la lista nueva
            except OSError:
                pass
            self._first_load = True
            self.load(force=True)
        settings.account_dialog(self, self.cfg, on_success, first=first, icon=ICON)

    def _apply_icon(self):
        def setit():
            try:
                self.iconbitmap(ICON)
            except Exception:
                pass
        setit()
        for d in (300, 900):
            self.after(d, setit)

    # ------------------------------------------------------------------
    def _build(self):
        body = ctk.CTkFrame(self, fg_color=C["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        body.columnconfigure(0, weight=0, minsize=CAT_MIN)
        body.columnconfigure(1, weight=0, minsize=CH_MIN)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)
        self.body = body

        # --- categorías (con Exportar M3U abajo) ---
        cat_col = ctk.CTkFrame(body, fg_color=C["surface"], corner_radius=14)
        cat_col.grid(row=0, column=0, sticky="nsew", padx=(14, 6), pady=14)
        self.cat_col = cat_col
        chead = ctk.CTkFrame(cat_col, fg_color="transparent")
        chead.pack(fill=tk.X, padx=14, pady=(14, 6))
        ctk.CTkLabel(chead, text="CATEGORÍAS", text_color=C["faint"],
                     font=font(11, "bold")).pack(side=tk.LEFT, padx=(4, 0))
        ctk.CTkButton(chead, text="⚙", width=28, height=28, corner_radius=8,
                      fg_color="transparent", hover_color=C["surface2"],
                      text_color=C["muted"], font=font(14),
                      command=self.open_category_editor).pack(side=tk.RIGHT)
        botrow = ctk.CTkFrame(cat_col, fg_color="transparent")
        botrow.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=12)
        ctk.CTkButton(botrow, text="Exportar M3U", height=34, corner_radius=10,
                      fg_color=C["surface2"], hover_color=C["surface3"],
                      text_color=C["muted"], font=font(12),
                      command=self.export_m3u).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ctk.CTkButton(botrow, text="⚙", width=38, height=34, corner_radius=10,
                      fg_color=C["surface2"], hover_color=C["hover"],
                      text_color=C["muted"], font=font(15),
                      command=self.open_account).pack(side=tk.LEFT, padx=(6, 0))
        self.cat_holder = ctk.CTkFrame(cat_col, fg_color="transparent")
        self.cat_holder.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))

        # --- canales ---
        ch_col = ctk.CTkFrame(body, fg_color=C["surface"], corner_radius=14)
        ch_col.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=14)
        self.ch_col = ch_col
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.apply_filter())
        ctk.CTkEntry(ch_col, textvariable=self.search_var, placeholder_text="Buscar canal…",
                     fg_color=C["surface2"], border_width=0, height=38,
                     font=font(12)).pack(fill=tk.X, padx=14, pady=(16, 8))
        head = ctk.CTkFrame(ch_col, fg_color="transparent")
        head.pack(fill=tk.X, padx=18, pady=(0, 2))
        self.count_lbl = ctk.CTkLabel(head, text="CANALES", text_color=C["faint"],
                                      font=font(11, "bold"))
        self.count_lbl.pack(side=tk.LEFT)
        # La lista ya se actualiza sola en cada arranque, pero un servidor
        # anade y quita canales a media tarde: hace falta poder volver a
        # pedirla sin cerrar el programa. F5 hace exactamente lo mismo.
        self.reload_btn = ctk.CTkButton(head, text=f"{RELOAD}  Actualizar",
                                        width=108, height=26, corner_radius=8,
                                        fg_color=C["surface2"], hover_color=C["hover"],
                                        text_color=C["muted"], font=font(11),
                                        command=lambda: self.load(force=True))
        self.reload_btn.pack(side=tk.RIGHT)

        # Estado de sincronización de la lista. Existe porque el fallo mas
        # desconcertante de este programa era justo este: si el servidor dejaba
        # de responder, la interfaz seguia mostrando la lista guardada y todo
        # parecia normal hasta que pulsabas un canal y salia negro. Un fallo de
        # actualizacion tiene que VERSE.
        self.sync_lbl = ctk.CTkLabel(ch_col, text="", text_color=C["muted"],
                                     font=font(10), anchor="w", justify="left")
        self.sync_lbl.pack(anchor="w", padx=18, pady=(0, 6), fill=tk.X)

        # barra de favoritos (solo visible en la vista Favoritos)
        self.fav_bar = ctk.CTkFrame(ch_col, fg_color="transparent")
        ctk.CTkButton(self.fav_bar, text="＋ Grupo", width=84, height=30, corner_radius=8,
                      fg_color=C["surface2"], hover_color=C["hover"],
                      text_color=C["text"], font=font(11),
                      command=self.new_group).pack(side=tk.LEFT)
        self.move_menu = ctk.CTkOptionMenu(
            self.fav_bar, values=["Mover a…"], width=140, height=30,
            command=self._move_selected_to, font=font(11),
            fg_color=C["accent"], button_color=C["accent_lo"],
            button_hover_color=C["accent_hi"], text_color="#ffffff",
            dropdown_fg_color=C["surface2"], dropdown_text_color=C["text"],
            dropdown_hover_color=C["accent"])
        self.move_menu.set("Mover a…")
        self.move_menu.pack(side=tk.LEFT, padx=6)
        ctk.CTkButton(self.fav_bar, text="▲", width=32, height=30, corner_radius=8,
                      fg_color=C["surface2"], hover_color=C["hover"],
                      text_color=C["text"], font=font(11),
                      command=lambda: self.move_selected(-1)).pack(side=tk.LEFT, padx=(2, 2))
        ctk.CTkButton(self.fav_bar, text="▼", width=32, height=30, corner_radius=8,
                      fg_color=C["surface2"], hover_color=C["hover"],
                      text_color=C["text"], font=font(11),
                      command=lambda: self.move_selected(1)).pack(side=tk.LEFT)

        self._tw = ctk.CTkFrame(ch_col, fg_color=C["surface"])
        self._tw.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 12))
        self.tree = ttk.Treeview(self._tw, show="tree",
                                 selectmode="extended", style="Dark.Treeview")
        self.tree.column("#0", width=290, stretch=True)
        self.tree.tag_configure("group", foreground=C["gold"])
        self._vs = ttk.Scrollbar(self._tw, orient="vertical", command=self.tree.yview,
                                 style="Dark.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=self._yscroll)   # auto-oculta la barra
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_channel())
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Button-3>", self._on_tree_menu)

        # --- vídeo: TODO el panel derecho ---
        vid_col = ctk.CTkFrame(body, fg_color=C["video"])
        vid_col.grid(row=0, column=2, sticky="nsew")
        vid_col.rowconfigure(0, weight=1)
        vid_col.columnconfigure(0, weight=1)
        self.vid_col = vid_col
        self.player = VlcPlayer(vid_col, user_agent=self.cfg.get("user_agent", "VLC/3.0"),
                                network_caching=int(self.cfg.get("network_caching", 1500)),
                                request_fullscreen=self.toggle_fullscreen,
                                request_panels=self.toggle_panels,
                                on_aspect=self._on_aspect,
                                request_ontop=self.toggle_ontop,
                                volume=int(self.cfg.get("volume", 90) or 90),
                                muted=bool(self.cfg.get("muted", False)))
        self.player.grid(row=0, column=0, sticky="nsew")
        self._build_catchup()

        self.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.bind("<Escape>", lambda e: self.toggle_fullscreen(False))
        self.bind("<F5>", lambda e: self.load(force=True))
        body.bind("<Configure>", self._fit_video, add="+")
        self.after(200, self._fit_video)
        self.after(300, self._hook_resize)
        atajos = (("l", self._key_panels), ("m", self._key_mute),
                  ("a", self._key_ontop), ("s", self._key_snap))
        for letra, fn in atajos:
            for k in (letra, letra.upper()):
                for w in (self, self.player.top, self.player.bottom):
                    w.bind(f"<KeyPress-{k}>", fn)

    def _build_catchup(self):
        a = self.player.actions
        ctk.CTkLabel(a, text="⟲  Catch-up", text_color=C["muted"],
                     font=font(11, "bold")).pack(side=tk.LEFT, padx=(14, 8))
        for label, mins in [("-30m", 30), ("-1h", 60), ("-3h", 180),
                            ("-6h", 360), ("-12h", 720), ("-24h", 1440)]:
            ctk.CTkButton(a, text=label, width=46, height=28, corner_radius=8,
                          fg_color=C["surface2"], hover_color=C["surface3"],
                          text_color=C["text"], font=font(11),
                          command=lambda m=mins: self.play_catchup(m)).pack(
                              side=tk.LEFT, padx=3)
        self.catchup_var = tk.StringVar(value="45")
        ent = ctk.CTkEntry(a, textvariable=self.catchup_var, width=48, height=28,
                           corner_radius=8, fg_color=C["surface2"], border_width=0,
                           justify="center", font=font(11))
        ent.pack(side=tk.LEFT, padx=(12, 2))
        ent.bind("<Return>", lambda e: self.catchup_step(True))
        ctk.CTkLabel(a, text="min", text_color=C["muted"],
                     font=font(10)).pack(side=tk.LEFT, padx=(0, 4))
        ctk.CTkButton(a, text="◀", width=30, height=28, corner_radius=8,
                      fg_color=C["surface2"], hover_color=C["surface3"],
                      text_color=C["text"], font=font(11),
                      command=lambda: self.catchup_step(True)).pack(
                          side=tk.LEFT, padx=(0, 2))
        ctk.CTkButton(a, text="▶", width=30, height=28, corner_radius=8,
                      fg_color=C["surface2"], hover_color=C["surface3"],
                      text_color=C["text"], font=font(11),
                      command=lambda: self.catchup_step(False)).pack(
                          side=tk.LEFT, padx=(0, 2))
        ctk.CTkButton(a, text="● Directo", width=84, height=28, corner_radius=8,
                      fg_color=C["accent"], hover_color=C["accent_hi"],
                      text_color="#ffffff", font=font(11, "bold"),
                      command=self.play_current).pack(side=tk.LEFT, padx=(8, 14))

    # ------------------------------------------------------------------
    def load(self, force):
        """Recarga la lista en segundo plano.

        Una sola a la vez: el boton se puede pulsar repetidamente y F5 se
        repite solo con dejarlo apretado, y dos hilos escribiendo cache.json a
        la vez es justo lo que no queremos. Lo pedido mientras hay una en curso
        no se tira, se relanza al terminar: importa al cambiar de cuenta con la
        carga inicial todavia colgada del timeout del servidor viejo.
        """
        if self._loading:
            self._requeue = True
            return
        self._loading = True
        self._reload_ready(False)

        def run():
            try:
                self._load(force)
            finally:
                self._loading = False
                self._reload_ready(True)
                self.after(0, self._drain_requeue)

        threading.Thread(target=run, daemon=True).start()

    def _drain_requeue(self):
        """Relanza la recarga que se pidio mientras habia otra en curso.

        Corre en el hilo de Tk a proposito: `load` tambien, asi que las dos no
        pueden entrelazarse y no hay manera de perder una peticion por haber
        leido `_loading` justo cuando el hilo de trabajo lo estaba bajando.
        """
        if self._requeue:
            self._requeue = False
            self.load(force=True)

    def _reload_ready(self, ready):
        """Habilita o agrisa el boton de recarga (llamable desde otro hilo)."""
        def apply():
            btn = getattr(self, "reload_btn", None)
            if btn is None:
                return
            btn.configure(state="normal" if ready else "disabled",
                          text=f"{RELOAD}  Actualizar" if ready
                               else f"{RELOAD}  …")
        self.after(0, apply)

    def _cache_age(self):
        """Antigüedad de la lista guardada, en lenguaje llano."""
        ts = None
        try:
            if os.path.exists(CACHE_PATH):
                try:
                    ts = json.load(open(CACHE_PATH, encoding="utf-8")).get("fetched_at")
                except Exception:
                    ts = None
                ts = ts or os.path.getmtime(CACHE_PATH)
        except OSError:
            pass
        if not ts:
            return "antigüedad desconocida"
        s = max(0.0, time.time() - ts)
        if s < 90:
            return "de hace un momento"
        if s < 5400:
            return f"de hace {int(s // 60)} min"
        if s < 172800:
            return f"de hace {int(s // 3600)} h"
        return f"de hace {int(s // 86400)} días"

    def _sync(self, text, color="muted"):
        self.after(0, lambda: self.sync_lbl.configure(text=text, text_color=C[color]))

    def _prefetch_start(self):
        """Arranca la descarga de la lista antes de que exista la ventana.

        No toca ni un widget (todavia no hay ninguno): deja el resultado en una
        caja que `_fetch_lists` recoge cuando la interfaz ya esta construida.
        """
        box = {"done": threading.Event(), "data": None, "error": None}
        self._fetch = box

        def run():
            try:
                box["data"] = self._download_lists()
            except Exception as e:               # se reporta al recogerla
                box["error"] = e
            finally:
                box["done"].set()

        threading.Thread(target=run, daemon=True).start()

    def _download_lists(self):
        """Categorias y canales del servidor. Solo red: no toca la interfaz."""
        cats, streams = self.client.catalog()
        if not streams:
            # Una lista vacia no es una lista: sobrescribir la cache con esto
            # dejaria al usuario sin canales y sin nada a lo que volver.
            raise RuntimeError("el servidor devolvió una lista vacía")
        return cats, streams

    def _fetch_lists(self):
        """Como `_download_lists`, pero aprovecha la descarga adelantada del
        arranque si sigue disponible. Se consume: solo sirve una vez.
        """
        pending, self._fetch = self._fetch, None
        if pending is None:
            return self._download_lists()
        pending["done"].wait()
        if pending["error"] is not None:
            raise pending["error"]
        return pending["data"]

    def _index_lists(self):
        self.cat_name = {str(c["category_id"]): c["category_name"]
                         for c in self.categories}
        self.by_id = {str(s["stream_id"]): s for s in self.all_streams}
        by = {}
        for s in self.all_streams:
            by.setdefault(str(s.get("category_id")), []).append(s)
        self.streams_by_cat = by
        self.after(0, self._populate)

    def _load(self, force):
        """Pinta la caché al instante y DESPUÉS habla siempre con el servidor.

        Antes, si existía cache.json el programa no volvía a contactar con el
        servidor nunca más: solo el refresco manual (F5) descargaba. Eso dejaba
        la lista congelada indefinidamente y, peor todavía, hacía invisible que
        el servidor hubiera dejado de autorizar la cuenta.

        Ahora se hacen las dos cosas: la caché se muestra de inmediato para que
        la ventana no se quede en blanco esperando a la red, y la lista se pide
        siempre. En el arranque ni siquiera se pide aquí: ya venía pedida de
        antes de construir la ventana (`_prefetch_start`), y `_fetch_lists` se
        limita a recoger el resultado. Si la descarga falla NO se borra lo que
        ya había —seguirías pudiendo navegar—, pero el aviso se ve.
        """
        served = False              # se ha llegado a pintar la caché al entrar
        if not force and os.path.exists(CACHE_PATH):
            try:
                cache = json.load(open(CACHE_PATH, encoding="utf-8"))
                self.categories = cache["categories"]
                self.all_streams = cache["streams"]
                self._index_lists()
                served = True
            except Exception:
                served = False

        self._sync(f"Lista guardada {self._cache_age()} · actualizando…"
                   if served else "Descargando lista de canales…")
        try:
            cats, streams = self._fetch_lists()
            self.categories, self.all_streams = cats, streams
            tmp = CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"fetched_at": time.time(), "categories": cats,
                           "streams": streams}, fh, ensure_ascii=False)
            os.replace(tmp, CACHE_PATH)
            self._index_lists()
            self._sync(f"Lista actualizada · {len(streams)} canales", "ok")
        except Exception as e:
            msg = (str(e).strip() or type(e).__name__)[:90]
            if self.all_streams:
                # Hay lista utilizable en pantalla: no molestamos con un
                # diálogo, pero que quede claro que puede estar caducada.
                #
                # La condición es «hay canales», no «acabo de pintar la
                # caché». Con el botón de Actualizar el usuario provoca
                # recargas forzadas a media sesión, que no pasan por la
                # caché; mirando lo segundo, un servidor que fallara justo
                # al pulsarlo anunciaba «Sin lista de canales» y sacaba un
                # diálogo de error con los canales ahí delante, intactos.
                self._sync(f"⚠  No se pudo actualizar · lista {self._cache_age()} · {msg}",
                           "danger")
            else:
                self._sync(f"⚠  Sin lista de canales · {msg}", "danger")
                self.after(0, lambda: messagebox.showerror(
                    "Error de conexión", f"No se pudo cargar la lista.\n\n{msg}"))

    def _visible_streams(self):
        return [s for s in self.all_streams
                if str(s.get("category_id")) not in self.hidden]

    def _populate(self):
        self._build_category_buttons()
        self.select_key("all")
        if self._first_load:
            self._first_load = False
            kids = self.tree.get_children()
            if kids:
                self.tree.selection_set(kids[0])
                self.tree.see(kids[0])

    def _build_category_buttons(self):
        for _, b, row in self.cat_buttons:
            row.destroy()
        self.cat_buttons = []
        palette = ["#0a84ff", "#2dd4bf", "#bf5af2", "#ff9f0a", "#30d158",
                   "#ff6482", "#64d2ff", "#ff453a", "#5e5ce6", "#ffd60a", "#34c759"]
        entries = [("fav", f"★  Favoritos ({len(self.favorites)})", C["gold"]),
                   ("all", f"Todos ({len(self._visible_streams())})", "#c7c7cf")]
        ci = 0
        for c in self.categories:
            cid = str(c["category_id"])
            if cid in self.hidden:
                continue
            n = len(self.streams_by_cat.get(cid, []))
            color = palette[ci % len(palette)]
            ci += 1
            entries.append((cid, f'{c["category_name"]}  ·  {n}', color))
        for key, text, color in entries:
            row = ctk.CTkFrame(self.cat_holder, fg_color="transparent", height=34)
            row.pack(fill=tk.X, pady=1)
            row.pack_propagate(False)
            tk.Frame(row, width=4, bg=color, highlightthickness=0, bd=0).pack(
                side=tk.LEFT, fill=tk.Y, padx=(4, 6), pady=6)
            b = ctk.CTkButton(row, text=text, anchor="w", height=32,
                              corner_radius=8, fg_color="transparent",
                              hover_color=C["hover"], text_color=C["muted"],
                              font=font(12), command=lambda k=key: self.select_key(k))
            b.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.cat_buttons.append((key, b, row))

    # ------------------------------------------------------------------
    def select_key(self, key):
        self.cat_key = key
        for k, b, _row in self.cat_buttons:
            on = (k == key)
            b.configure(fg_color=C["accent"] if on else "transparent",
                        text_color="#ffffff" if on else C["muted"])
        if key == "fav":
            self._show_fav_bar(True)
            self._refresh_move_menu()
            self.apply_filter()
            return
        self._show_fav_bar(False)
        if key == "all":
            self.current_list = self._visible_streams()
        else:
            self.current_list = list(self.streams_by_cat.get(key, []))
        self.apply_filter()

    def _yscroll(self, first, last):
        # muestra la barra de scroll solo cuando hace falta
        self._vs.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._vs.pack_forget()
        elif not self._vs.winfo_ismapped():
            self._vs.pack(side=tk.LEFT, fill=tk.Y)

    def _show_fav_bar(self, on):
        if on:
            self.fav_bar.pack(fill=tk.X, padx=14, pady=(0, 8), before=self._tw)
        else:
            self.fav_bar.pack_forget()

    def apply_filter(self):
        if not hasattr(self, "tree"):
            return
        q = self.search_var.get().strip().lower()
        if self.cat_key == "fav":
            self._render_favorites(q)
            return
        rows = [s for s in self.current_list
                if q in s.get("name", "").lower()] if q else self.current_list
        self.tree.delete(*self.tree.get_children())
        for s in rows:
            fav = str(s["stream_id"]) in self.favorites
            self.tree.insert("", tk.END, iid=str(s["stream_id"]),
                             text="  " + s.get("name", ""),
                             image=(self.img_on if fav else self.img_off))
        self.count_lbl.configure(text=f"CANALES · {len(rows)}", text_color=C["faint"])

    def _render_favorites(self, q):
        self.tree.delete(*self.tree.get_children())
        grouped = set()
        for gi, g in enumerate(self.fav_groups):
            gid = f"grp:{gi}"
            self.tree.insert("", tk.END, iid=gid, text="  " + g.get("name", "Grupo"),
                             open=True, tags=("group",))
            for cid in list(g.get("channels", [])):
                cid = str(cid)
                s = self.by_id.get(cid)
                if not s or cid not in self.favorites:
                    continue
                grouped.add(cid)
                name = s.get("name", "")
                if q and q not in name.lower():
                    continue
                self.tree.insert(gid, tk.END, iid=cid, text="  " + name,
                                 image=self.img_on)
        ung = [s for s in self.all_streams
               if str(s["stream_id"]) in self.favorites
               and str(s["stream_id"]) not in grouped]
        if ung:
            self.tree.insert("", tk.END, iid="grp:_ung", text="  Sin grupo",
                             open=True, tags=("group",))
            for s in ung:
                name = s.get("name", "")
                if q and q not in name.lower():
                    continue
                self.tree.insert("grp:_ung", tk.END, iid=str(s["stream_id"]),
                                 text="  " + name, image=self.img_on)
        self.count_lbl.configure(text=f"FAVORITOS · {len(self.favorites)}",
                                 text_color=C["gold"])

    # ------------------------------------------------------------------
    def _on_tree_click(self, event):
        # recordar si había Ctrl/Shift pulsado (para no reproducir al multiseleccionar)
        self._mod_click = bool(event.state & 0x0005)   # Control(0x4) o Shift(0x1)
        # zona de la estrella (izquierda del nombre) -> favorito; nombre -> reproducir
        row = self.tree.identify_row(event.y)
        if not row or row.startswith("grp:"):
            return
        if self.tree.identify_column(event.x) != "#0":
            return
        if "text" in self.tree.identify_element(event.x, event.y):
            return
        self._toggle_fav(row)
        return "break"

    def _toggle_fav(self, sid):
        sid = str(sid)
        if sid in self.favorites:
            self.favorites.discard(sid)
            for g in self.fav_groups:
                if sid in g.get("channels", []):
                    g["channels"].remove(sid)
        else:
            self.favorites.add(sid)
        self._save_favs()
        for k, b, _row in self.cat_buttons:
            if k == "fav":
                b.configure(text=f"★  Favoritos ({len(self.favorites)})")
        if self.cat_key == "fav":
            self.apply_filter()
        elif self.tree.exists(sid):
            self.tree.item(sid, image=(self.img_on if sid in self.favorites
                                       else self.img_off))

    def _save_favs(self):
        self.cfg["favorites"] = sorted(self.favorites)
        self.cfg["favorite_groups"] = self.fav_groups
        save_config(self.cfg)

    # ---- grupos de favoritos -----------------------------------------
    def _ask(self, title, text, initial=""):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.configure(fg_color=C["surface"])
        win.geometry("380x180")
        win.resizable(False, False)
        win.transient(self)
        win.after(60, win.grab_set)
        try:
            win.after(250, lambda: win.iconbitmap(ICON))
        except Exception:
            pass
        ctk.CTkLabel(win, text=text, font=font(13), text_color=C["text"]).pack(
            anchor="w", padx=20, pady=(22, 8))
        var = tk.StringVar(value=initial)
        ent = ctk.CTkEntry(win, textvariable=var, fg_color=C["surface2"],
                           border_width=0, height=38, font=font(12))
        ent.pack(fill=tk.X, padx=20)
        ent.focus_set()
        res = {"v": ""}

        def ok():
            res["v"] = var.get().strip()
            win.destroy()

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill=tk.X, padx=20, pady=18)
        ctk.CTkButton(bar, text="Aceptar", fg_color=C["accent"],
                      hover_color=C["accent_hi"], text_color="#ffffff",
                      font=font(12, "bold"), command=ok).pack(side=tk.RIGHT)
        ctk.CTkButton(bar, text="Cancelar", fg_color=C["surface2"],
                      hover_color=C["surface3"], text_color=C["muted"],
                      font=font(12), command=win.destroy).pack(side=tk.RIGHT, padx=8)
        ent.bind("<Return>", lambda e: ok())
        ent.bind("<Escape>", lambda e: win.destroy())
        win.wait_window()
        return res["v"]

    def new_group(self):
        name = self._ask("Nuevo grupo", "Nombre del grupo:")
        if name:
            self.fav_groups.append({"name": name, "channels": []})
            self._save_favs()
            self.select_key("fav")

    def rename_group(self, gi):
        name = self._ask("Renombrar grupo", "Nuevo nombre:",
                         initial=self.fav_groups[gi].get("name", ""))
        if name:
            self.fav_groups[gi]["name"] = name
            self._save_favs()
            self._refresh_move_menu()
            self.apply_filter()

    def delete_group(self, gi):
        # los canales siguen siendo favoritos (pasan a "Sin grupo")
        self.fav_groups.pop(gi)
        self._save_favs()
        self._refresh_move_menu()
        self.apply_filter()

    def _selected_channels(self):
        return [i for i in self.tree.selection() if not i.startswith("grp:")]

    def move_channels_to(self, cids, target):
        """Mueve varios canales de una vez a un grupo (target=índice o None)."""
        for cid in cids:
            cid = str(cid)
            for g in self.fav_groups:
                if cid in g.get("channels", []):
                    g["channels"].remove(cid)
            if target is not None:
                self.fav_groups[target].setdefault("channels", []).append(cid)
        self._save_favs()
        self.apply_filter()

    def move_to_group(self, cid, target):
        self.move_channels_to([cid], target)

    def _refresh_move_menu(self):
        vals = [g.get("name", "Grupo") for g in self.fav_groups]
        vals += ["Sin grupo", "＋ Nuevo grupo…"]
        self.move_menu.configure(values=vals)
        self.move_menu.set("Mover a…")

    def _move_selected_to(self, choice):
        cids = self._selected_channels()
        self.move_menu.set("Mover a…")
        if choice == "＋ Nuevo grupo…":
            name = self._ask("Nuevo grupo", "Nombre del grupo:")
            if not name:
                return
            self.fav_groups.append({"name": name, "channels": []})
            self.move_channels_to(cids, len(self.fav_groups) - 1)
            self._refresh_move_menu()
            return
        if not cids:
            return
        if choice == "Sin grupo":
            self.move_channels_to(cids, None)
        else:
            idx = next((i for i, g in enumerate(self.fav_groups)
                        if g.get("name") == choice), None)
            if idx is not None:
                self.move_channels_to(cids, idx)

    def move_selected(self, delta):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("grp:"):
            if iid == "grp:_ung":
                return
            gi = int(iid.split(":")[1])
            j = gi + delta
            if 0 <= j < len(self.fav_groups):
                self.fav_groups[gi], self.fav_groups[j] = \
                    self.fav_groups[j], self.fav_groups[gi]
                self._save_favs()
                self.apply_filter()
                if self.tree.exists(f"grp:{j}"):
                    self.tree.selection_set(f"grp:{j}")
        else:
            gi = self._group_of(iid)
            if gi is None:
                return
            ch = self.fav_groups[gi]["channels"]
            k = ch.index(iid)
            j = k + delta
            if 0 <= j < len(ch):
                ch[k], ch[j] = ch[j], ch[k]
                self._save_favs()
                self.apply_filter()
                if self.tree.exists(iid):
                    self.tree.selection_set(iid)
                    self.tree.see(iid)

    def _group_of(self, cid):
        cid = str(cid)
        for gi, g in enumerate(self.fav_groups):
            if cid in g.get("channels", []):
                return gi
        return None

    def _on_tree_menu(self, event):
        if self.cat_key != "fav":
            return
        row = self.tree.identify_row(event.y)
        m = tk.Menu(self, tearoff=0, bg=C["surface2"], fg=C["text"],
                    activebackground=C["accent"], activeforeground="#ffffff",
                    bd=0, font=("Segoe UI", 10))
        if row and row.startswith("grp:") and row != "grp:_ung":
            gi = int(row.split(":")[1])
            self.tree.selection_set(row)
            m.add_command(label="Nuevo grupo", command=self.new_group)
            m.add_command(label="Renombrar", command=lambda: self.rename_group(gi))
            m.add_command(label="Eliminar grupo", command=lambda: self.delete_group(gi))
            m.add_separator()
            m.add_command(label="Subir grupo", command=lambda: self.move_selected(-1))
            m.add_command(label="Bajar grupo", command=lambda: self.move_selected(1))
        elif row and not row.startswith("grp:"):
            # si la fila no está en la selección actual, seleccionar solo esa
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            cids = self._selected_channels()
            n = len(cids)
            head = f"{n} canales seleccionados" if n > 1 else None
            if head:
                m.add_command(label=head, state="disabled")
                m.add_separator()
            m.add_command(label="Subir", command=lambda: self.move_selected(-1))
            m.add_command(label="Bajar", command=lambda: self.move_selected(1))
            sub = tk.Menu(m, tearoff=0, bg=C["surface2"], fg=C["text"],
                          activebackground=C["accent"], activeforeground="#ffffff", bd=0)
            for gi, g in enumerate(self.fav_groups):
                sub.add_command(label=g.get("name", "Grupo"),
                                command=lambda t=gi: self.move_channels_to(cids, t))
            sub.add_separator()
            sub.add_command(label="Sin grupo",
                            command=lambda: self.move_channels_to(cids, None))
            m.add_cascade(label="Mover a grupo", menu=sub)
            m.add_separator()
            m.add_command(label=("Quitar de favoritos" if n <= 1
                                 else f"Quitar {n} de favoritos"),
                          command=lambda: [self._toggle_fav(c) for c in list(cids)])
        else:
            m.add_command(label="Nuevo grupo", command=self.new_group)
        m.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------
    def _selected(self):
        sel = self.tree.selection()
        if not sel or sel[0].startswith("grp:"):
            return None
        return self.by_id.get(sel[0])

    def on_channel(self):
        # solo reproduce con UNA selección y sin Ctrl/Shift (no al multiseleccionar)
        mod = getattr(self, "_mod_click", False)
        self._mod_click = False
        sel = self.tree.selection()
        if len(sel) != 1 or sel[0].startswith("grp:") or mod:
            return
        s = self.by_id.get(sel[0])
        if not s:
            return
        self.current_stream = s
        self.player.enable_actions(str(s.get("tv_archive")) == "1")
        self.play_current()
        self.player.set_epg("cargando guía…")
        threading.Thread(target=self._load_epg, args=(s,), daemon=True).start()

    def _load_epg(self, s):
        try:
            epg = self.client.short_epg(s["stream_id"], limit=2)
            if epg:
                txt = f"AHORA · {epg[0]['title']}"
                if len(epg) > 1:
                    txt += f"     LUEGO · {epg[1]['title']}"
            else:
                txt = ""
        except Exception:
            txt = ""
        self.after(0, lambda: self.player.set_epg(txt))

    # ------------------------------------------------------------------
    def open_category_editor(self):
        win = ctk.CTkToplevel(self)
        win.title("Mostrar categorías")
        win.geometry("340x540")
        win.configure(fg_color=C["surface"])
        win.transient(self)
        win.after(60, win.grab_set)
        try:
            win.after(250, lambda: win.iconbitmap(ICON))
        except Exception:
            pass
        ctk.CTkLabel(win, text="Categorías visibles", font=font(15, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=18, pady=(16, 4))
        ctk.CTkLabel(win, text="Desmarca las que quieras ocultar",
                     font=font(11), text_color=C["muted"]).pack(anchor="w", padx=18,
                                                                pady=(0, 8))
        sf = ctk.CTkScrollableFrame(win, fg_color="transparent")
        sf.pack(fill=tk.BOTH, expand=True, padx=10)
        vars = {}
        for c in self.categories:
            cid = str(c["category_id"])
            v = tk.IntVar(value=0 if cid in self.hidden else 1)
            vars[cid] = v
            ctk.CTkCheckBox(sf, text=c["category_name"], variable=v, onvalue=1,
                            offvalue=0, fg_color=C["accent"], hover_color=C["accent_hi"],
                            text_color=C["text"], font=font(12)).pack(anchor="w",
                                                                      pady=5, padx=6)

        def save():
            self.hidden = {cid for cid, v in vars.items() if v.get() == 0}
            self.cfg["hidden_categories"] = sorted(self.hidden)
            save_config(self.cfg)
            win.destroy()
            self._build_category_buttons()
            self.select_key("all" if self.cat_key in self.hidden else self.cat_key)

        ctk.CTkButton(win, text="Guardar", height=38, corner_radius=10,
                      fg_color=C["accent"], hover_color=C["accent_hi"],
                      text_color="#ffffff", font=font(12, "bold"),
                      command=save).pack(fill=tk.X, padx=14, pady=12)

    # ------------------------------------------------------------------
    def _side_panels(self, show):
        """Muestra u oculta las dos columnas de la izquierda."""
        if show:
            self.body.columnconfigure(0, minsize=CAT_MIN)
            self.body.columnconfigure(1, minsize=CH_MIN)
            self.cat_col.grid()
            self.ch_col.grid()
            self._fit_video()
        else:
            self.cat_col.grid_remove()
            self.ch_col.grid_remove()
            self.body.columnconfigure(0, minsize=0)
            self.body.columnconfigure(1, minsize=0)

    def toggle_fullscreen(self, want=None):
        target = (not self._fs) if want is None else bool(want)
        if target == self._fs:
            return
        self._fs = target
        if target:
            self._side_panels(False)
            self.attributes("-fullscreen", True)
        else:
            self.attributes("-fullscreen", False)
            if not self._panels_off:          # respeta el modo solo-reproductor
                self._side_panels(True)
        self.player.set_fullscreen(target)

    def _cols_width(self):
        """Ancho real (fijo) de las dos columnas con sus márgenes de rejilla.
        Se mide sobre las propias columnas: al contrario que cuerpo−vídeo, no
        se descoloca en los reflows intermedios de un cambio de geometría."""
        try:
            a = self.cat_col.winfo_width()
            b = self.ch_col.winfo_width()
            if a > 10 and b > 10:
                return a + b + 30        # padx de la rejilla: (14+6)+(0+10)
        except Exception:
            pass
        return CAT_MIN + CH_MIN + 30

    def _fit_video(self, _e=None):
        """Las columnas tienen ANCHO FIJO y el vídeo ocupa el resto. Aquí solo
        se vigila que no queden franjas arriba y abajo: si el vídeo se queda
        corto de ancho se acomoda la VENTANA (diferido), nunca las columnas.
        El aire a los lados (maximizada, canal 4:3…) se deja en paz."""
        if self._fs or self._panels_off:
            return
        try:
            bw, bh = self.body.winfo_width(), self.body.winfo_height()
            if bw < 200 or bh < 200:
                return
            cols = self._cols_width()
            self._shape_cache = (cols, self.player.aspect())
            self._apply_minsize(cols)
            prev, self._last_size = self._last_size, (bw, bh)
            aire = int(round(bh * self.player.aspect())) - (bw - cols)
            if aire > 2:                             # franjas arriba/abajo
                self._fit_use_h = bool(prev) and prev[0] != bw
                self._schedule_window_fit()
        except Exception:
            pass

    def _shape(self, w=None, h=None):
        """Forma válida del área de cliente: columnas fijas + vídeo sin franjas."""
        cols, ar = self._shape_cache or (CAT_MIN + CH_MIN + 30, 16 / 9)
        if w is not None:
            return int(w), max(MIN_H, int(round((w - cols) / ar)))
        return int(round(cols + h * ar)), int(h)

    def _apply_minsize(self, cols=None):
        """Ancho mínimo de la ventana: por debajo no cabe el vídeo sin franjas
        ni con el alto al mínimo, así que el arrastre se detiene ahí."""
        try:
            if cols is None:
                cols = self._cols_width()
            g = self._geom_parts()
            rw = self.winfo_width()
            escala = (rw / g[0]) if (g and g[0] and rw) else 1.0
            w = int(round((cols + MIN_H * self.player.aspect()) / escala)) + 2
            w = max(MIN_W, min(w, int(self.winfo_screenwidth() / escala)))
            if w != self._minw:
                self._minw = w
                self.minsize(w, MIN_H)
        except Exception:
            pass

    def _on_aspect(self):
        """Ha cambiado el formato del canal: la ventana se acomoda en ambos
        sentidos (se ensancha para el cine, vuelve al acabar); las columnas
        no se mueven."""
        self._fit_use_h = False
        self._fit_both = True
        self._fit_video()
        self._schedule_window_fit()

    def _schedule_window_fit(self):
        # diferido: así no se pelea con el arrastre del borde de la ventana
        if self._fit_after:
            try:
                self.after_cancel(self._fit_after)
            except Exception:
                pass
        self._fit_after = self.after(180, self._fit_window)

    def _fit_window(self):
        """Deja la ventana en una forma válida tocando el eje que NO se está
        arrastrando (al arrancar o al cambiar de formato, el ancho)."""
        self._fit_after = None
        both, self._fit_both = self._fit_both, False
        if self._fs or self._panels_off or self.state() != "normal":
            return
        try:
            g = self._geom_parts()
            rw, rh = self.winfo_width(), self.winfo_height()
            if not g or not g[0] or rw < 200 or rh < 200:
                return
            cols, ar = self._shape_cache or (CAT_MIN + CH_MIN + 30, 16 / 9)
            aire = int(round(rh * ar)) - (rw - cols)
            if not both and aire <= 2:
                return              # el estado que lo programó ya no existe
            gw, gh, x, y = g
            escala = rw / g[0]
            ancho = self._shape(h=rh)[0]         # ancho que pide el alto actual
            alto = self._shape(w=rw)[1]          # alto que pide el ancho actual
            new_gw = int(round(ancho / escala))
            new_gh = int(round(alto / escala))
            ancho_ok = ancho <= self.winfo_screenwidth() and new_gw >= MIN_W
            alto_ok = new_gh >= MIN_H
            if self._fit_use_h and alto_ok:
                gh = new_gh
            elif ancho_ok:
                gw = new_gw
            elif alto_ok:
                gh = new_gh
            else:
                return                           # no cabe: se deja estar
            if abs(gw - g[0]) <= 2 and abs(gh - g[1]) <= 2:
                return
            sw = int(self.winfo_screenwidth() / escala)
            self.geometry(f"{gw}x{gh}+{max(0, min(x, sw - gw))}+{y}")
        except Exception:
            pass

    # ---- límite del arrastre EN VIVO (Windows) ------------------------
    def _hook_resize(self):
        """Intercepta WM_SIZING para limitar el arrastre mientras ocurre: el
        borde solo se mueve a formas válidas, así que se ve en tiempo real cómo
        queda en vez de arrastrar libremente y corregir al soltar."""
        if not sys.platform.startswith("win"):
            return
        try:
            u = ctypes.windll.user32
            u.CallWindowProcW.restype = ctypes.c_ssize_t
            u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                          ctypes.c_uint, ctypes.c_size_t,
                                          ctypes.c_ssize_t]
            u.GetAncestor.restype = ctypes.c_void_p
            self._hwnd = ctypes.c_void_p(u.GetAncestor(self.winfo_id(), 2))  # GA_ROOT
            setter = getattr(u, "SetWindowLongPtrW", u.SetWindowLongW)
            setter.restype = ctypes.c_void_p
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            self._proc = WNDPROC(self._wndproc)     # referencia viva: no tocar
            self._setter = setter
            self._oldproc = setter(self._hwnd, GWLP_WNDPROC,
                                   ctypes.cast(self._proc, ctypes.c_void_p))
        except Exception:
            self._oldproc = None

    def _unhook_resize(self):
        try:
            if self._oldproc:
                self._setter(self._hwnd, GWLP_WNDPROC, self._oldproc)
                self._oldproc = None
        except Exception:
            pass

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_SIZING and not (self._fs or self._panels_off):
            try:
                self._limit_drag(int(wparam), lparam)
            except Exception:
                pass
        return ctypes.windll.user32.CallWindowProcW(self._oldproc, hwnd, msg,
                                                    wparam, lparam)

    def _limit_drag(self, borde, lparam):
        """Recorta el rectángulo que propone el arrastre a la forma válida."""
        u = ctypes.windll.user32
        r = ctypes.cast(ctypes.c_void_p(lparam),
                        ctypes.POINTER(wintypes.RECT)).contents
        marco = wintypes.RECT(); cliente = wintypes.RECT()
        u.GetWindowRect(self._hwnd, ctypes.byref(marco))
        u.GetClientRect(self._hwnd, ctypes.byref(cliente))
        dw = (marco.right - marco.left) - (cliente.right - cliente.left)
        dh = (marco.bottom - marco.top) - (cliente.bottom - cliente.top)
        cw, ch = (r.right - r.left) - dw, (r.bottom - r.top) - dh
        if borde in SZ_ALTO:                 # arrastra arriba/abajo: manda el alto
            cw, ch = self._shape(h=ch)
        else:                                # laterales y esquinas: manda el ancho
            cw, ch = self._shape(w=cw)
        w, h = cw + dw, ch + dh
        if borde in SZ_IZQ:
            r.left = r.right - w
        else:
            r.right = r.left + w
        if borde in SZ_ARR:
            r.top = r.bottom - h
        else:
            r.bottom = r.top + h

    # ---- modo solo-reproductor (botón de la barra / tecla L) ----------
    def _geom_parts(self):
        """(ancho, alto, x, y) de la ventana, o None si no se puede leer."""
        m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", self.geometry())
        return tuple(int(v) for v in m.groups()) if m else None

    def _panels_width(self, g):
        """Ancho de las dos columnas, en las unidades que usa geometry().
        CustomTkinter escala la geometría con los DPI del monitor mientras que
        winfo_* da píxeles reales; el factor se mide comparando ambos."""
        real = self.winfo_width()
        if real <= 0 or not g[0]:
            return 0
        scale = real / g[0]
        return max(int(round((real - self.vid_col.winfo_width()) / scale)), 0)

    def _restored_geometry(self):
        """Geometría equivalente con las dos columnas visibles."""
        g = self._geom_parts()
        if not (self._panels_w and g):
            return self.geometry()
        w, h, x, y = g
        return f"{w + self._panels_w}x{h}+{max(x - self._panels_w, 0)}+{y}"

    def toggle_panels(self, want=None):
        """Oculta las dos columnas de la izquierda y encoge la ventana justo lo
        que ocupaban, de modo que el vídeo no cambia ni de tamaño ni de sitio.
        Al volver a pulsar se recupera todo. Atajo: L."""
        if self._fs:                      # en pantalla completa ya están fuera
            return
        target = (not self._panels_off) if want is None else bool(want)
        if target == self._panels_off:
            return
        self._panels_off = target
        self.update_idletasks()           # geometría ya asentada antes de medir
        if target:
            g = self._geom_parts() if self.state() == "normal" else None
            self._panels_w = self._panels_width(g) if g else 0
            self._side_panels(False)
            if self._panels_w:
                self.minsize(480, 400)    # el vídeo solo puede ser más estrecho
                self._minw = None
                w, h, x, y = g
                self.geometry(f"{w - self._panels_w}x{h}+{x + self._panels_w}+{y}")
        else:
            geo = self._restored_geometry() if self.state() == "normal" else None
            self._side_panels(True)
            if self._panels_w:
                self._apply_minsize()
                if geo:
                    self.geometry(geo)
            self._panels_w = 0
        self.player.set_panels_hidden(target)

    def _typing(self, e):
        # para no robar la tecla mientras se escribe (buscador, diálogos…)
        return isinstance(getattr(e, "widget", None), (tk.Entry, ttk.Entry, tk.Text))

    def _key_panels(self, e):
        if not self._typing(e):
            self.toggle_panels()

    def _key_mute(self, e):
        if not self._typing(e):
            self.player.toggle_mute()

    def _key_ontop(self, e):
        if not self._typing(e):
            self.toggle_ontop()

    def _key_snap(self, e):
        if not self._typing(e):
            self.player.snapshot()

    def toggle_ontop(self, want=None):
        """Fija la ventana por encima de todas (botón 📌 / tecla A)."""
        self._ontop = (not self._ontop) if want is None else bool(want)
        try:
            self.attributes("-topmost", self._ontop)
        except Exception:
            pass
        self.player.set_ontop(self._ontop)

    def play_current(self):
        s = self._selected() or self.current_stream
        if not s:
            return
        self.current_stream = s
        self._catchup_min = 0
        self.player.play(self.client.live_url(s["stream_id"]),
                         title=s.get("name", ""), live=True)

    def play_catchup(self, minutes_back):
        s = self.current_stream or self._selected()
        if not s:
            return
        minutes_back = int(minutes_back)
        self._catchup_min = minutes_back
        start_dt = datetime.now() - timedelta(minutes=minutes_back)
        start = start_dt.strftime("%Y-%m-%d:%H-%M")
        url = self.client.timeshift_url(s["stream_id"], start, minutes_back + 240)
        eti = (f"-{minutes_back // 60}h"
               if minutes_back % 60 == 0 and minutes_back >= 60
               else f"-{minutes_back}m")
        self.player.play(url, title=f"{s.get('name','')} · catch-up {eti}",
                         live=False)

    def catchup_step(self, back):
        """Salta los minutos de la caja: ◀ hacia atrás, ▶ hacia delante,
        siempre desde el punto actual; llegar a 0 devuelve al directo."""
        try:
            x = int(float(self.catchup_var.get().strip().replace(",", ".")))
        except (ValueError, AttributeError):
            return
        if x <= 0:
            return
        m = self._catchup_min + (x if back else -x)
        if m <= 0:
            self.play_current()
        else:
            self.play_catchup(min(m, 10080))         # tope: 7 días

    def export_m3u(self):
        if not self.all_streams:
            return
        only = messagebox.askyesno("Exportar M3U",
                                   "¿Solo la lista actual?\nSí = visible · No = todos")
        streams = self.current_list if only else self._visible_streams()
        path = filedialog.asksaveasfilename(defaultextension=".m3u",
                                            filetypes=[("Playlist", "*.m3u")],
                                            initialfile="piedrasonic.m3u")
        if not path:
            return
        open(path, "w", encoding="utf-8").write(
            self.client.build_m3u(streams, self.cat_name))

    def _on_close(self):
        self._unhook_resize()
        try:
            if not self._fs and self.state() == "normal":
                self.cfg["window_geometry"] = self._restored_geometry()
            v, m = self.player.get_volume_state()
            self.cfg["volume"], self.cfg["muted"] = int(v), bool(m)
            save_config(self.cfg)
        except Exception:
            pass
        try:
            self.player.release()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    LiveApp().mainloop()
