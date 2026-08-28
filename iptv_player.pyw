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
import queue
import threading
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import theme
from theme import C, font
from xtream import XtreamClient, SALIDA_POR_DEFECTO
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


def _texto_error(e):
    return (str(e).strip() or type(e).__name__)[:90]


class _Descarga:
    """Baja la lista por categorias y va soltando lo que trae segun llega.

    El motivo esta medido contra el panel de este proyecto: pedir la lista
    entera de una vez tarda 27,6 s, y de esos, 27,6 son UNA categoria
    ("Latinos", 1382 de los 1737 canales). Las otras nueve contestan en
    0,13-0,16 s cada una. Pidiendolas por separado el total no baja —eso ya se
    comprobo y sigue siendo verdad—, pero a los 0,42 s el usuario tiene 355
    canales delante y funcionando en vez de una ventana vacia durante medio
    minuto. Y la gorda tampoco llega de golpe: el panel la va soltando, asi que
    a los 7,4 s hay ya varios cientos mas.

    Todo lo que produce sale por una cola de eventos. Aqui NO se toca ni un
    widget: Tk solo aguanta que lo toque su propio hilo, y los fallos de
    hacerlo desde otro no son excepciones limpias, son cuelgues raros que no
    hay manera de reproducir. La ventana vacia esa cola cuando le viene bien.

    Eventos: ("categorias", lista) · ("empieza", cid) · ("canales", cid, lote)
             ("lista", cid, completa) · ("falla", cid, motivo) · ("fin", info)
    """

    def __init__(self, cliente, esperado=None, ocultas=(), hilos=6):
        self.cliente = cliente
        self.cola = queue.Queue()
        self.esperado = dict(esperado or {})   # cid -> lo que tenia la ultima vez
        self.ocultas = set(ocultas or ())
        self.hilos = hilos
        self._corta = threading.Event()
        self._entero = None       # cid del que descubrio que el panel no filtra
        self._cerrojo = threading.Lock()

    # -- vida ----------------------------------------------------------
    def arranca(self):
        threading.Thread(target=self._corre, daemon=True).start()
        return self

    def cancela(self):
        """Abandona la descarga. Los hilos vivos mueren en cuanto respiran.

        No es instantaneo: un hilo parado esperando el primer byte de la
        categoria gorda no se entera hasta que ese byte llega, y pueden ser
        siete segundos. Da igual: son hilos demonio y lo que traigan se tira,
        porque la ventana solo hace caso a la descarga que tiene en la mano.
        """
        self._corta.set()

    @property
    def cancelada(self):
        return self._corta.is_set()

    # -- trabajo ---------------------------------------------------------
    def _corre(self):
        try:
            cats = self._login_y_categorias()
        except Exception as e:
            self.cola.put(("fin", {"ok": False, "error": _texto_error(e)}))
            return
        if self._corta.is_set():
            return
        self.cola.put(("categorias", cats))

        ids = [str(c["category_id"]) for c in cats]
        if not ids:
            ids = [None]          # panel sin categorias: la lista entera
        # Cuantos canales trae cada una. El panel lo dice en `stream_count` y
        # es exacto: comprobado contra este servidor, categoria a categoria.
        # Vale mas que la cache porque llega tambien en el primer arranque,
        # que es justo cuando cache no hay.
        tam = {str(c["category_id"]): int(c.get("stream_count") or 0) for c in cats}
        if not any(tam.values()):
            tam = self.esperado
        # Las ocultas al final y la mas gorda primero: es el palo largo de la
        # carga y todo lo demas cabe a su lado.
        orden = sorted(ids, key=lambda cid: (cid in self.ocultas,
                                             -tam.get(cid, 0)))

        sem = threading.Semaphore(self.hilos)
        hilos = []
        for cid in orden:
            h = threading.Thread(target=self._una, args=(cid, sem), daemon=True)
            h.start()
            hilos.append(h)
        limite = getattr(self.cliente, "timeout", 25) + 60
        for h in hilos:
            h.join(limite)
        if self._corta.is_set():
            return
        self.cola.put(("fin", {"ok": True, "error": None}))

    def _login_y_categorias(self):
        """Las dos a la vez: son viajes independientes y cada uno paga su TLS."""
        cajas = {}
        for nombre, fn in (("login", self.cliente.login),
                           ("cats", self.cliente.live_categories)):
            caja = {}

            def corre(fn=fn, caja=caja):
                try:
                    caja["v"] = fn()
                except Exception as e:
                    caja["e"] = e

            h = threading.Thread(target=corre, daemon=True)
            h.start()
            cajas[nombre] = (h, caja)
        for h, _ in cajas.values():
            h.join(getattr(self.cliente, "timeout", 25) + 5)
        # El login manda: si la cuenta no vale, el otro fallo es consecuencia y
        # su mensaje solo despistaria.
        for nombre in ("login", "cats"):
            _, caja = cajas[nombre]
            if "e" in caja:
                raise caja["e"]
            if "v" not in caja:
                raise RuntimeError(f"el servidor no respondio a tiempo ({nombre})")
        return cajas["cats"][1]["v"] or []

    def _sobro(self, cid):
        """¿Este hilo ya no pinta nada? (cancelado, o el panel no filtra)."""
        if self._corta.is_set():
            return True
        with self._cerrojo:
            return self._entero is not None and self._entero != cid

    def _una(self, cid, sem):
        with sem:
            if self._sobro(cid):
                return
            self.cola.put(("empieza", cid))
            try:
                completa = self.cliente.live_streams_goteo(
                    category_id=cid,
                    on_lote=lambda lote, cid=cid: self._lote(cid, lote),
                    corta=lambda cid=cid: self._sobro(cid))
            except Exception as e:
                if not self._corta.is_set() and not self._sobro(cid):
                    self.cola.put(("falla", cid, _texto_error(e)))
                return
            if not self._corta.is_set():
                self.cola.put(("lista", cid, completa))

    def _lote(self, cid, lote):
        if self._corta.is_set():
            return
        # Hay paneles que se pasan el category_id por alto y contestan la lista
        # entera a cualquier peticion. Se detecta en cuanto asoma un canal de
        # otra categoria, y entonces los demas hilos sobran: esta respuesta ya
        # lo trae todo. Los canales se guardan siempre por SU category_id, no
        # por el que se pidio, asi que aunque no se detectara la lista saldria
        # bien igual; esto solo evita nueve peticiones inutiles de 28 s.
        if cid is not None and any(str(s.get("category_id")) != str(cid)
                                   for s in lote):
            with self._cerrojo:
                if self._entero is None:
                    self._entero = cid
        self.cola.put(("canales", cid, lote))


class LiveApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        theme.init()
        self.cfg = load_config()
        self.favorites = set(str(x) for x in self.cfg.get("favorites", []))
        self.fav_groups = self.cfg.get("favorite_groups", [])   # [{name, channels[]}]
        self.hidden = set(str(x) for x in self.cfg.get("hidden_categories", []))
        self.categories = []
        self.streams_by_cat = {}
        self.all_streams = []
        self.by_id = {}
        self.cat_name = {}
        self.orden_cat = []           # categorias en el orden que las da el panel
        self.current_list = []
        self.current_stream = None
        self.cat_buttons = []
        self.cat_key = "all"
        self._first_load = True
        self._formato_ok = False      # ya se comprobo que el video se ve
        self._loading = False         # hay una recarga de lista en curso
        self._requeue = False         # ...y se pidio otra mientras corria
        self._descarga = None         # la descarga viva
        self._token = 0               # cual es: lo de una anterior se ignora
        self._drenaje = None          # id del after que vacia la cola
        self._cat_widgets = {}        # cid -> widgets de su fila
        self._estado_cat = {}         # cid -> pendiente|cargando|lista|falla
        self._esperado = {}           # cid -> canales que tenia la ultima vez
        self._llegados = 0            # canales que ya estan en la lista
        self._latido = 0              # fase del parpadeo de "cargando"
        self._late_id = None
        self.fallidas = []            # categorias que no llegaron

        self._rebuild_client()
        # La lista se pide AQUI, antes de crear un solo widget. Levantar la
        # ventana de CustomTkinter cuesta cerca de un segundo; pidiendo ya la
        # lista, esa espera y la ida y vuelta al servidor se solapan en vez de
        # sumarse. Con la lista partida por categorias eso significa que para
        # cuando la ventana esta en pie, las categorias pequenas ya han
        # llegado y hay canales que ensenar desde el primer fotograma.
        if settings.has_credentials(self.cfg):
            self._prefetch_start()
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
            self.after(0, self.load)
        else:
            self.after(200, lambda: self.open_account(first=True))

    def _rebuild_client(self):
        # Un cambio de cuenta invalida una descarga en vuelo: sus datos son de
        # OTRO servidor y no deben acabar pintados ni en la cache.
        for viejo in (getattr(self, "_fetch", None), getattr(self, "_descarga", None)):
            if viejo is not None:
                viejo.cancela()
        self._fetch = None
        self._descarga = None
        self.client = XtreamClient(
            self.cfg.get("server", ""), self.cfg.get("username", ""),
            self.cfg.get("password", ""),
            user_agent=self.cfg.get("user_agent", "VLC/3.0"),
            output=self.cfg.get("output") or SALIDA_POR_DEFECTO)

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
            self._formato_ok = False       # otro servidor, otra comprobacion
            self.load()
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
                                        command=self.load)
        self.reload_btn.pack(side=tk.RIGHT)

        # Estado de sincronización de la lista. Existe porque el fallo mas
        # desconcertante de este programa era justo este: si el servidor dejaba
        # de responder, la interfaz seguia mostrando la lista guardada y todo
        # parecia normal hasta que pulsabas un canal y salia negro. Un fallo de
        # actualizacion tiene que VERSE.
        self.sync_lbl = ctk.CTkLabel(ch_col, text="", text_color=C["muted"],
                                     font=font(10), anchor="w", justify="left")
        self.sync_lbl.pack(anchor="w", padx=18, pady=(0, 6), fill=tk.X)

        # Barra fina de progreso, visible solo mientras la lista esta llegando.
        # Vive en un hueco fijo bajo la linea de estado, y no empaquetada con
        # before=self._tw: ahi competiria por el sitio con la barra de
        # favoritos y el orden acabaria dependiendo de por donde hayas
        # entrado. Aqui el hueco es siempre el mismo y solo se llena o vacia.
        self._zona_prog = ctk.CTkFrame(ch_col, fg_color="transparent", height=0)
        self._zona_prog.pack(fill=tk.X, padx=18)
        self.prog = ctk.CTkProgressBar(self._zona_prog, height=5, corner_radius=3,
                                       fg_color=C["surface3"],
                                       progress_color=C["accent"])
        self.prog.set(0)
        self._prog_modo = "determinate"

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
        self.bind("<F5>", lambda e: self.load())
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
    def load(self):
        """Vacía la lista y la vuelve a pedir, mostrándola según llega.

        Ya no se pinta la lista guardada al entrar. Se pintaba para que la
        ventana no apareciera en blanco durante los 28 s que tardaba la
        descarga, y como remedio tenía dos caras malas: durante medio minuto
        estabas mirando canales que podían llevar días sin existir, y no había
        manera de distinguir «esto es de ayer» de «esto es de ahora». Ahora la
        lista empieza vacía y se llena de verdad, que se entiende solo.

        Una sola descarga a la vez: el botón se puede pulsar repetidamente y F5
        se repite con dejarlo apretado. Lo pedido mientras hay una en curso no
        se tira, se relanza al terminar. Eso importa al cambiar de cuenta con
        la carga inicial todavía colgada del timeout del servidor viejo.
        """
        if self._loading:
            self._requeue = True
            return
        self._loading = True
        self._token += 1
        self._reload_ready(False)

        # La descarga adelantada del arranque, si sigue sirviendo. Solo vale si
        # es de ESTE cliente: un cambio de cuenta la deja inservible.
        desc, self._fetch = self._fetch, None
        if desc is None or desc.cliente is not self.client or desc.cancelada:
            desc = _Descarga(self.client, esperado=self._esperado,
                             ocultas=self.hidden).arranca()
        self._descarga = desc
        self._vacia_lista()
        self._drena()

    def _drain_requeue(self):
        """Relanza la recarga que se pidió mientras había otra en curso."""
        if self._requeue:
            self._requeue = False
            self.load()

    def _reload_ready(self, ready):
        """Habilita o agrisa el botón de recarga (llamable desde otro hilo)."""
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
                    with open(CACHE_PATH, encoding="utf-8-sig") as fh:
                        ts = json.load(fh).get("fetched_at")
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

    # ---- arranque adelantado -------------------------------------------
    def _prefetch_start(self):
        """Arranca la descarga antes de que exista la ventana.

        Levantar la ventana de CustomTkinter cuesta cerca de un segundo;
        pidiendo ya la lista, esa espera y la ida y vuelta al servidor se
        solapan en vez de sumarse. Aquí no hay ni un widget que tocar todavía:
        la descarga escribe en su cola y `load` la recoge cuando la interfaz
        está en pie.
        """
        self._esperado = self._esperado_de_cache()
        self._fetch = _Descarga(self.client, esperado=self._esperado,
                                ocultas=self.hidden).arranca()

    def _esperado_de_cache(self):
        """Cuántos canales traía cada categoría la última vez.

        Sirve para dos cosas, y ninguna es pintar canales viejos: saber por
        cuál empezar —la más gorda primero, que es el palo largo— y poder
        decir «355 de ~1737» en vez de un número que sube sin que se sepa
        hacia dónde. Una barra de progreso que no sabe dónde acaba no es una
        barra de progreso, es un adorno.
        """
        try:
            with open(CACHE_PATH, encoding="utf-8-sig") as fh:
                cache = json.load(fh)
        except Exception:
            return {}
        cuenta = {}
        for s in cache.get("streams", []):
            cid = str(s.get("category_id"))
            cuenta[cid] = cuenta.get(cid, 0) + 1
        return cuenta

    # ---- la lista creciendo --------------------------------------------
    def _vacia_lista(self):
        """Deja la lista en blanco y prepara la ventana para verla crecer."""
        self.categories = []
        self.orden_cat = []
        self.streams_by_cat = {}
        self.all_streams = []
        self.by_id = {}
        self.cat_name = {}
        self.current_list = []
        self.fallidas = []
        self._llegados = 0
        self._estado_cat = {}
        self._pinta_categorias([])
        self._repinta_arbol()
        self._progreso(0.0)
        self._sync("Conectando con el servidor…")
        self._cartel("Conectando con el servidor…",
                     "Se está pidiendo la lista de canales.")
        self._arranca_latido()

    def _drena(self):
        """Vacía la cola de la descarga. Corre SIEMPRE en el hilo de Tk.

        Se vacía entera de una vez y luego se pinta, en vez de pintar por cada
        evento: la categoría gorda llega en ráfagas de cientos de canales y
        repintar por cada uno sería tirar el tiempo que se acaba de ganar.
        """
        desc = self._descarga
        if desc is None or desc.cancelada:
            # La descarga se ha abandonado —cambio de cuenta, o cierre de la
            # ventana—. De una abandonada no llega ningún «fin», así que el
            # pestillo hay que soltarlo aquí: si no, `_loading` se queda
            # puesto, el botón de Actualizar agrisado para siempre y la
            # recarga que se pidió al cambiar de cuenta no llega a salir.
            self._drenaje = None
            self._para_latido()
            self._progreso(None)
            self._loading = False
            self._reload_ready(True)
            self.after(0, self._drain_requeue)
            return
        try:
            self._drena_una_vez(desc)
        except Exception as e:
            # Un fallo pintando no puede dejar la descarga colgada para
            # siempre, con el boton de recargar agrisado y sin explicacion.
            # Se termina aqui, se dice, y el usuario puede volver a intentarlo.
            self._drenaje = None
            self._para_latido()
            self._progreso(None)
            self._loading = False
            self._reload_ready(True)
            self._sync(f"⚠  Fallo al pintar la lista · {_texto_error(e)}", "danger")

    def _drena_una_vez(self, desc):
        nuevos = {}
        fin = None
        try:
            while True:
                ev = desc.cola.get_nowait()
                tipo = ev[0]
                if tipo == "categorias":
                    self._llegan_categorias(ev[1])
                elif tipo == "empieza":
                    self._marca_categoria(ev[1], "cargando")
                elif tipo == "canales":
                    nuevos.setdefault(ev[1], []).extend(ev[2])
                elif tipo == "lista":
                    nuevos.setdefault(ev[1], []).extend(ev[2])
                    self._marca_categoria(ev[1], "lista")
                elif tipo == "falla":
                    self.fallidas.append((ev[1], ev[2]))
                    self._marca_categoria(ev[1], "falla")
                elif tipo == "fin":
                    fin = ev[1]
        except queue.Empty:
            pass

        for cid, lote in nuevos.items():
            self._mete_canales(cid, lote)
        if nuevos:
            self._autoplay()      # una vez, con todo lo del vaciado ya dentro
        if nuevos or fin:
            self._actualiza_cuentas()
        if fin is None:
            self._sync(self._texto_progreso())
            self._progreso(self._fraccion())
            self._drenaje = self.after(70, self._drena)
        else:
            self._fin_descarga(fin)

    def _llegan_categorias(self, cats):
        self.categories = cats
        self.orden_cat = [str(c["category_id"]) for c in cats]
        self.cat_name = {str(c["category_id"]): c["category_name"] for c in cats}
        # El panel dice cuántos canales tiene cada categoría, y es exacto.
        # Llega a los 0,13 s y existe también en el primer arranque, así que
        # manda sobre lo que dijera la caché: la barra de progreso sabe dónde
        # acaba desde el principio, incluso la primera vez que se abre.
        tam = {str(c["category_id"]): int(c.get("stream_count") or 0) for c in cats}
        if any(tam.values()):
            self._esperado = tam
        for cid in self.orden_cat:
            self._estado_cat.setdefault(cid, "pendiente")
        self._pinta_categorias(cats)
        self._cartel("Pidiendo los canales…",
                     "Aparecerán aquí conforme vayan llegando, y se pueden usar "
                     "desde el primero.")

    def _mete_canales(self, cid, lote):
        """Mete los canales recién llegados SIN repintar la lista entera.

        Repintarla cuesta 12 ms y es tentador por lo simple, pero un repintado
        borra la selección —y la selección es lo que está sonando— y devuelve
        el scroll al principio. Con la lista creciendo durante medio minuto,
        eso sería cortar el canal que estás viendo cada vez que llega un lote.

        Los canales se archivan por SU category_id, no por el que se pidió: si
        un panel se pasa el filtro por alto y contesta de más, la lista sale
        bien igual.
        """
        porcat = {}
        for s in lote:
            sid = str(s.get("stream_id") or "")
            if not sid or sid in self.by_id:
                continue
            self.by_id[sid] = s
            suya = str(s.get("category_id"))
            self.streams_by_cat.setdefault(suya, []).append(s)
            porcat.setdefault(suya, []).append(s)
        if not porcat:
            return
        self._llegados = len(self.by_id)
        self._reordena()
        for real, grupo in porcat.items():
            self._inserta_en_arbol(real, grupo)
        # La sonda de vídeo, en cuanto hay un canal que sondear. Antes esperaba
        # a la lista entera, o sea 28 s para enterarse de que no se ve nada.
        if not self._formato_ok and self.all_streams:
            self._formato_ok = True
            primero = self.all_streams[0]
            threading.Thread(target=self._verificar_formato,
                             args=(primero, self._token), daemon=True).start()

    def _reordena(self):
        """Rehace la lista global en el orden en que el panel da las categorías.

        Las categorías llegan cuando les toca —la pequeña a los 0,13 s y la
        gorda a los 27—, pero la lista tiene que verse siempre igual: el orden
        lo manda el panel, no quién gane la carrera. Rehacerla cuesta un
        milisegundo sobre 1737 canales.
        """
        orden = list(self.orden_cat)
        for cid in self.streams_by_cat:
            if cid not in orden:
                orden.append(cid)
        self.orden_cat = orden
        self.all_streams = [s for cid in orden
                            for s in self.streams_by_cat.get(cid, [])]

    def _hueco(self, cid):
        """En qué fila de la vista «Todos» empieza el bloque de una categoría."""
        n = 0
        for c in self.orden_cat:
            if c == cid:
                break
            if c in self.hidden:
                continue
            n += len(self.streams_by_cat.get(c, []))
        return n

    def _inserta_en_arbol(self, cid, grupo):
        if not hasattr(self, "tree"):
            return
        # La lista de la vista se mantiene al día SIEMPRE, se pinte o no: de
        # ella salen el buscador, la exportación a M3U y el contador. Cuando
        # esto se hacía solo en la rama que pinta, con el buscador escrito la
        # lista dejaba de crecer por dentro y lo que llegaba se perdía.
        if self.cat_key == "all":
            self.current_list = self._visible_streams()
        elif self.cat_key == cid:
            self.current_list = list(self.streams_by_cat.get(cid, []))

        if self.cat_key == "fav":
            self._repinta_arbol()          # los favoritos van agrupados aparte
            return
        if self.cat_key == "all" and cid in self.hidden:
            return                         # categoría oculta: solo cuenta
        if self.cat_key not in ("all", cid):
            return                         # no se está mirando: solo cuenta
        if self.search_var.get().strip():
            self._repinta_arbol()          # con filtro puesto, más vale rehacer
            return
        base = 0 if self.cat_key == cid else self._hueco(cid)
        ya = len(self.streams_by_cat.get(cid, [])) - len(grupo)
        for k, s in enumerate(grupo):
            fav = str(s["stream_id"]) in self.favorites
            self.tree.insert("", base + ya + k, iid=str(s["stream_id"]),
                             text="  " + s.get("name", ""),
                             image=(self.img_on if fav else self.img_off))
        self._cartel(None)

    def _refresca_vista(self):
        """Rehace `current_list` desde cero para la vista que esté puesta.

        La lista de la vista se va manteniendo al día lote a lote, que es lo
        rápido; esto es el cinturón: al acabar la carga se recalcula entera,
        para que el estado en que queda la ventana no dependa del camino que
        haya seguido la descarga.
        """
        if self.cat_key == "all":
            self.current_list = self._visible_streams()
        elif self.cat_key != "fav":
            self.current_list = list(self.streams_by_cat.get(self.cat_key, []))

    def _repinta_arbol(self):
        """Repinta la lista conservando la selección y el sitio del scroll.

        Devolver la selección vuelve a disparar <<TreeviewSelect>>, o sea que
        `on_channel` se ejecuta otra vez con el canal que ya estaba sonando.
        Lo que evita que se corte y se reanude no es una bandera —Tk entrega
        ese evento ANTES de que corra un `after_idle`, así que una bandera que
        se apaga al quedar libre llega tarde y encima se traga el arranque
        automático—, sino que `on_channel` mire si el canal es el mismo.
        """
        if not hasattr(self, "tree"):
            return
        sel = list(self.tree.selection())
        arriba = self.tree.yview()[0]
        self.apply_filter()
        quedan = [i for i in sel if self.tree.exists(i)]
        if quedan:
            self.tree.selection_set(quedan)
        self.tree.yview_moveto(arriba)

    def _autoplay(self):
        """Arranca el primer canal, pero solo cuando ya es EL primero.

        Con la lista creciendo por trozos, el primero de la lista cambia
        conforme llegan categorías que van por delante. Reproducir el primero
        que aparezca sería reproducir el que gane la carrera, que es distinto
        cada arranque. Se espera a que la primera categoría visible esté
        entera: contra este panel es MOVISTAR ESPAÑA y llega a los 0,13 s, o
        sea que la espera no se nota. Antes esto pasaba a los 28 s.
        """
        if not self._first_load or not self.orden_cat:
            return
        primera = next((c for c in self.orden_cat if c not in self.hidden), None)
        if primera is None or self._estado_cat.get(primera) != "lista":
            return
        # El primer canal DE ESA categoría, y no la primera fila del árbol.
        # Parecen lo mismo y no lo son: en el mismo vaciado de la cola pueden
        # llegar dos categorías, y si la que va detrás se mete en el árbol
        # antes, la primera fila todavía es suya. Contra el servidor real eso
        # se veía en que el canal que arrancaba solo cambiaba en cada
        # ejecución: unas veces Movistar, otras la NBA.
        primeros = self.streams_by_cat.get(primera) or []
        if not primeros:
            return
        iid = str(primeros[0].get("stream_id"))
        if not self.tree.exists(iid):
            return
        self._first_load = False
        self.tree.selection_set(iid)
        self.tree.see(iid)

    # ---- final de la descarga -------------------------------------------
    def _fin_descarga(self, info):
        self._drenaje = None
        self._para_latido()
        self._progreso(None)
        self._loading = False
        self._reload_ready(True)
        n = len(self.by_id)
        # Un repintado final, siempre. Durante la carga las filas se meten en
        # su hueco, que es un SEGUNDO cálculo del mismo orden que hace
        # `_reordena`. Este repintado cuadra los dos y cierra la posibilidad de
        # que hayan divergido sin que nadie se entere. Cuesta 12 ms, una vez.
        if n:
            self._refresca_vista()
            self._repinta_arbol()

        if not info.get("ok") and not n:
            motivo = info.get("error") or "el servidor no ha contestado"
            self._sync(f"⚠  Sin lista de canales · {motivo}", "danger")
            acciones = [("Reintentar", self.load)]
            if os.path.exists(CACHE_PATH):
                acciones.append((f"Usar la lista guardada ({self._cache_age()})",
                                 self._usa_cache))
            self._cartel("No se ha podido cargar la lista", motivo, acciones,
                         color=C["danger"])
        elif self.fallidas:
            cuales = ", ".join(self.cat_name.get(c, c) for c, _ in self.fallidas)
            self._sync(f"⚠  Lista incompleta · {n} canales · falló {cuales}", "warn")
        elif not n:
            self._sync("⚠  El servidor ha devuelto una lista vacía", "danger")
            self._cartel("El servidor no ha dado ni un canal",
                         "La cuenta conecta, pero la lista viene vacía.",
                         [("Reintentar", self.load)], color=C["danger"])
        else:
            faltan = self._faltan_canales()
            if faltan:
                # Nada de fallar en silencio. Pidiendo por categorías, un canal
                # que esté en una categoría que el panel no lista no se pide
                # nunca y desaparecería sin un solo aviso: el peor fallo
                # posible en este programa, porque parece que todo va bien.
                self._sync(f"⚠  Lista actualizada · {n} canales · faltan "
                           f"{faltan} que el panel dice tener", "warn")
            else:
                self._sync(f"Lista actualizada · {n} canales", "ok")
            self._guarda_cache()
        self.after(0, self._drain_requeue)

    def _faltan_canales(self):
        """Cuántos canales dice el panel que tiene y no han llegado."""
        dice = sum(int(c.get("stream_count") or 0) for c in self.categories)
        return max(0, dice - len(self.by_id)) if dice else 0

    def _guarda_cache(self):
        """Guarda la lista para el próximo arranque.

        Ya no se pinta al entrar, pero sigue haciendo falta: de ella salen las
        cuentas por categoría que hacen honesta la barra de progreso, y es lo
        único que queda si mañana el servidor no contesta. Solo se guarda
        cuando la descarga ha venido completa: media lista guardada como si
        fuera entera es una trampa que se descubre tarde.
        """
        if self.fallidas or not self.by_id:
            return
        try:
            tmp = CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"fetched_at": time.time(),
                           "categories": self.categories,
                           "streams": self.all_streams}, fh, ensure_ascii=False)
            os.replace(tmp, CACHE_PATH)
        except Exception:
            pass          # no poder guardar la caché no estropea la sesión

    def _usa_cache(self):
        """Pinta la lista guardada. SOLO si el usuario lo pide expresamente."""
        try:
            with open(CACHE_PATH, encoding="utf-8-sig") as fh:
                cache = json.load(fh)
            cats = cache.get("categories", [])
            streams = cache.get("streams", [])
        except Exception as e:
            self._sync(f"⚠  La lista guardada no se puede leer · {_texto_error(e)}",
                       "danger")
            return
        edad = self._cache_age()
        self.categories = cats
        self.orden_cat = [str(c["category_id"]) for c in cats]
        self.cat_name = {str(c["category_id"]): c["category_name"] for c in cats}
        self.streams_by_cat = {}
        self.by_id = {}
        for s in streams:
            sid = str(s.get("stream_id") or "")
            if not sid or sid in self.by_id:
                continue
            self.by_id[sid] = s
            self.streams_by_cat.setdefault(str(s.get("category_id")), []).append(s)
        self._reordena()
        self._llegados = len(self.by_id)
        self._estado_cat = {cid: "lista" for cid in self.orden_cat}
        self._pinta_categorias(cats)
        self._cartel(None)
        # select_key y no _repinta_arbol a secas: hay que rehacer current_list,
        # que es de donde apply_filter saca las filas.
        self.select_key(self.cat_key if self.cat_key in self._cat_widgets else "all")
        self._actualiza_cuentas()
        self._sync(f"⚠  Lista guardada {edad} · el servidor no ha contestado",
                   "warn")

    def _verificar_formato(self, stream, token):
        """Comprueba que los canales se pueden VER, no solo listar.

        Listar y reproducir van por caminos distintos: la lista puede llegar
        perfecta y el vídeo estar cortado. Pasa cuando el panel está detrás de
        un CDN —Cloudflare no permite repartir televisión por su red— que deja
        pasar la API y corta el flujo de vídeo, redirigiéndolo a un dominio que
        resuelve a 127.0.0.1. El síntoma es demoledor por lo mudo que es:
        están los 1737 canales en su sitio y ninguno arranca.

        Corre en su propio hilo, en cuanto hay UN canal que sondear.
        """
        actual = self.cfg.get("output") or SALIDA_POR_DEFECTO
        try:
            ext, motivo = self.client.formato_que_funciona(stream["stream_id"])
        except Exception:
            return                               # una sonda que falla no molesta
        if token != self._token:
            return                               # es de una carga ya superada
        if ext is None:
            self._sync(f"⚠  La lista carga pero el vídeo no: {motivo}", "danger")
            return
        if ext != actual:
            self.after(0, lambda e=ext: self._aplica_formato(e))

    def _aplica_formato(self, ext):
        """Cambia el formato de vídeo, ya en el hilo de Tk.

        Lo decide la sonda, que corre en su propio hilo, pero `self.cfg` es de
        la ventana: escribirlo desde fuera podría pisarse con el guardado que
        hace `_on_close` al cerrar. El hilo solo trae la respuesta; el cambio
        se aplica aquí.
        """
        self.cfg["output"] = ext
        # Se toca solo la salida y no se rehace el cliente: rehacerlo
        # cancelaría la descarga que en este momento puede seguir en marcha.
        self.client.output = ext
        try:
            save_config(self.cfg)
        except Exception:
            pass
        self._sync(f"Vídeo por {ext.upper()} · {len(self.by_id)} canales", "ok")

    # ---- indicadores ------------------------------------------------------
    def _fraccion(self):
        # Si ya han contestado todas, la barra esta llena: da igual lo que
        # dijera el `stream_count`. Hay paneles que exageran, y un medidor
        # clavado al 90 % con la lista entera delante no se lo cree nadie.
        if self.orden_cat and all(self._estado_cat.get(c) in ("lista", "falla")
                                  for c in self.orden_cat):
            return 1.0
        total = sum(self._esperado.values())
        if not total:
            return None                          # sin datos no se sabe el final
        return min(1.0, self._llegados / float(total))

    def _texto_progreso(self):
        total = sum(self._esperado.values())
        n = self._llegados
        txt = f"Cargando la lista…  {n}"
        if total and n <= total:
            txt += f" de ~{total}"
        txt += " canales"
        if self.orden_cat:
            hechas = sum(1 for c in self.orden_cat
                         if self._estado_cat.get(c) in ("lista", "falla"))
            txt += f"  ·  {hechas} de {len(self.orden_cat)} categorías"
            faltan = [self.cat_name.get(c, c) for c in self.orden_cat
                      if self._estado_cat.get(c) not in ("lista", "falla")]
            if 0 < len(faltan) <= 2:
                txt += "  ·  falta " + " y ".join(faltan)
        return txt

    def _progreso(self, frac=0.0):
        """La barra fina sobre la lista. `frac=None` la retira."""
        barra = getattr(self, "prog", None)
        if barra is None:
            return
        if frac is None:
            if barra.winfo_ismapped():
                barra.stop()
                barra.pack_forget()
            return
        if not barra.winfo_ismapped():
            barra.pack(fill=tk.X, pady=(2, 8))
        f = self._fraccion()
        if f is None:
            # Primer arranque: no hay caché, o sea que no se sabe cuántos
            # canales van a venir. Una barra que se inventa el final miente;
            # esta solo dice «esto sigue vivo».
            if self._prog_modo != "indeterminate":
                self._prog_modo = "indeterminate"
                barra.configure(mode="indeterminate")
                barra.start()
        else:
            if self._prog_modo != "determinate":
                self._prog_modo = "determinate"
                barra.stop()
                barra.configure(mode="determinate")
            barra.set(f)

    def _arranca_latido(self):
        if self._late_id is None:
            self._late()

    def _para_latido(self):
        if self._late_id is not None:
            self.after_cancel(self._late_id)
            self._late_id = None
        for cid in list(self._cat_widgets):
            if self._estado_cat.get(cid) == "cargando":
                self._estado_cat[cid] = ("lista" if self.streams_by_cat.get(cid)
                                         else "falla")
            self._pinta_fila_categoria(cid)

    def _late(self):
        """Latido de la barrita de color de las categorías que aún llegan.

        Un solo temporizador para todas: así laten a la vez y parece
        intencionado, en vez de un baile de luces descoordinado.
        """
        self._latido = (self._latido + 1) % 16
        t = self._latido / 8.0
        if t > 1.0:
            t = 2.0 - t
        for cid, w in self._cat_widgets.items():
            if self._estado_cat.get(cid) != "cargando":
                continue
            if self.streams_by_cat.get(cid):
                continue        # ya tiene medidor que mirar: no hace falta latir
            w["bar"].configure(bg=theme.mezcla(C["surface"], w["color"],
                                               0.15 + 0.5 * t))
        self._late_id = self.after(80, self._late)

    def _visible_streams(self):
        return [s for s in self.all_streams
                if str(s.get("category_id")) not in self.hidden]

    # ---- columna de categorías -------------------------------------------
    PALETA = ["#0a84ff", "#2dd4bf", "#bf5af2", "#ff9f0a", "#30d158",
              "#ff6482", "#64d2ff", "#ff453a", "#5e5ce6", "#ffd60a", "#34c759"]

    def _pinta_categorias(self, cats):
        """Construye la columna de categorías. Se hace UNA vez por descarga.

        A partir de aquí las filas no se vuelven a crear, solo se les cambia
        el texto y el color de la barrita: crear widgets de CustomTkinter en
        cada lote que llega se vería como un parpadeo, y son veinticuatro
        lotes en una carga.
        """
        if not hasattr(self, "cat_holder"):
            return
        for w in self._cat_widgets.values():
            w["row"].destroy()
        self._cat_widgets = {}
        self.cat_buttons = []
        filas = [("fav", "★  Favoritos", C["gold"]),
                 ("all", "Todos", "#c7c7cf")]
        ci = 0
        for c in cats:
            cid = str(c["category_id"])
            if cid in self.hidden:
                continue
            filas.append((cid, c["category_name"], self.PALETA[ci % len(self.PALETA)]))
            ci += 1
        for key, nombre, color in filas:
            row = ctk.CTkFrame(self.cat_holder, fg_color="transparent", height=34)
            row.pack(fill=tk.X, pady=1)
            row.pack_propagate(False)
            # La barrita de color de la izquierda hace de medidor: el fondo es
            # el color apagado y el relleno sube desde abajo según van
            # llegando los canales de esa categoría. Diez medidores llenándose
            # a distinta velocidad se leen de un vistazo, sin números.
            bar = tk.Frame(row, width=4, bg=theme.mezcla(C["surface"], color, 0.22),
                           highlightthickness=0, bd=0)
            bar.pack(side=tk.LEFT, fill=tk.Y, padx=(4, 6), pady=6)
            bar.pack_propagate(False)
            relleno = tk.Frame(bar, bg=color, highlightthickness=0, bd=0)
            relleno.place(relx=0, rely=1.0, relwidth=1.0, relheight=1.0,
                          anchor="sw")
            b = ctk.CTkButton(row, text=nombre, anchor="w", height=32,
                              corner_radius=8, fg_color="transparent",
                              hover_color=C["hover"], text_color=C["muted"],
                              font=font(12), command=lambda k=key: self.select_key(k))
            b.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            b.configure(fg_color=C["accent"] if key == self.cat_key else "transparent",
                        text_color="#ffffff" if key == self.cat_key else C["muted"])
            self._cat_widgets[key] = {"row": row, "btn": b, "bar": bar,
                                      "relleno": relleno, "color": color,
                                      "nombre": nombre}
            self.cat_buttons.append((key, b, row))
        self._actualiza_cuentas()

    def _marca_categoria(self, cid, estado):
        if cid is None:
            return                    # panel sin categorias: no hay fila que pintar
        cid = str(cid)
        self._estado_cat[cid] = estado
        self._pinta_fila_categoria(cid)

    def _pinta_fila_categoria(self, cid):
        """Pone al día una fila: cuántos canales tiene y en qué estado está.

        Los cuatro estados se distinguen sin leer, por la barrita de color de
        la izquierda: apagada = todavía no ha llegado, latiendo = está
        llegando, encendida = completa, roja = no vino.
        """
        w = self._cat_widgets.get(cid)
        if not w or cid in ("fav", "all"):
            return
        estado = self._estado_cat.get(cid, "pendiente")
        n = len(self.streams_by_cat.get(cid, []))
        espera = self._esperado.get(cid, 0)
        nombre = w["nombre"]
        lleno = 1.0
        if estado == "lista":
            texto, color = f"{nombre}  ·  {n}", C["muted"]
            w["relleno"].configure(bg=w["color"])
        elif estado == "falla":
            texto, color = f"{nombre}  ·  no cargó", C["danger"]
            w["relleno"].configure(bg=C["danger"])
        elif estado == "cargando":
            texto = f"{nombre}  ·  {n} de ~{espera}" if espera else f"{nombre}  ·  {n}…"
            color = C["text"]
            lleno = min(1.0, n / float(espera)) if espera else (0.06 if not n else 0.5)
            w["relleno"].configure(bg=w["color"])
        else:
            texto = f"{nombre}  ·  ~{espera}" if espera else f"{nombre}  ·  …"
            color = C["faint"]
            lleno = 0.0
        w["relleno"].place_configure(relheight=lleno)
        if not (estado == "cargando" and not n):
            # el fondo vuelve a su color apagado; solo late mientras se espera
            # el primer canal de esa categoria, que es cuando no hay medidor
            w["bar"].configure(bg=theme.mezcla(C["surface"], w["color"], 0.22))
        w["btn"].configure(text=texto,
                           text_color="#ffffff" if self.cat_key == cid else color)

    def _actualiza_cuentas(self):
        """Los números que cambian mientras la lista crece."""
        w = self._cat_widgets.get("fav")
        if w:
            w["btn"].configure(text=f"★  Favoritos ({len(self.favorites)})")
        w = self._cat_widgets.get("all")
        if w:
            w["btn"].configure(text=f"Todos ({len(self._visible_streams())})")
        for cid in list(self._cat_widgets):
            self._pinta_fila_categoria(cid)
        if hasattr(self, "count_lbl") and self.cat_key != "fav":
            self.count_lbl.configure(
                text=f"CANALES · {len(self.tree.get_children())}",
                text_color=C["faint"])

    def _cartel(self, titulo=None, detalle=None, acciones=(), color=None):
        """Cartel centrado sobre la lista vacía. Con `titulo=None` se quita.

        Una lista vacía sin explicación es indistinguible de un programa roto,
        y es justo lo primero que ve el usuario al arrancar. Este cartel es lo
        que convierte «no hay nada» en «viene de camino».
        """
        viejo = getattr(self, "_cartel_w", None)
        if viejo is not None:
            viejo.destroy()
            self._cartel_w = None
        if not titulo or not hasattr(self, "_tw"):
            return
        caja = ctk.CTkFrame(self._tw, fg_color="transparent")
        ctk.CTkLabel(caja, text=titulo, text_color=color or C["muted"],
                     font=font(13, "bold"), justify="center").pack()
        if detalle:
            ctk.CTkLabel(caja, text=detalle, text_color=C["faint"], font=font(11),
                         justify="center", wraplength=260).pack(pady=(6, 0))
        if acciones:
            fila = ctk.CTkFrame(caja, fg_color="transparent")
            fila.pack(pady=(14, 0))
            for texto, cmd in acciones:
                ctk.CTkButton(fila, text=texto, height=30, corner_radius=8,
                              fg_color=C["surface2"], hover_color=C["hover"],
                              text_color=C["text"], font=font(11),
                              command=cmd).pack(side=tk.LEFT, padx=4)
        caja.place(relx=0.5, rely=0.40, anchor="center")
        self._cartel_w = caja

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
        # Ya está sonando: no se reinicia. Sin esto, cualquier repintado de la
        # lista —y durante la carga hay unos cuantos— cortaría el canal medio
        # segundo para volver a ponerlo.
        actual = self.current_stream
        if actual is not None and str(actual.get("stream_id")) == str(s.get("stream_id")):
            self.current_stream = s
            return
        self.current_stream = s
        # El usuario ha elegido: el arranque automático ya no pinta nada. Sin
        # esto, con la lista todavía creciendo, elegir un canal a los 0,6 s y
        # que luego llegara una categoría que va por delante hacía que el
        # arranque automático saltara encima y te cambiara de canal solo.
        self._first_load = False
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
            self._pinta_categorias(self.categories)
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
        for viva in (self._fetch, self._descarga):
            if viva is not None:
                viva.cancela()
        if self._drenaje is not None:
            try:
                self.after_cancel(self._drenaje)
            except Exception:
                pass
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
