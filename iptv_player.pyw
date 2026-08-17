#!/usr/bin/env python3
"""
piedrasonic — TV en vivo (Xtream) con VLC embebido, catch-up y timeshift.
Interfaz minimalista (CustomTkinter). Un clic reproduce; controles translúcidos
sobre el vídeo. Favoritos con grupos desplegables y reordenables. Categorías
configurables. Auto-reproduce el primer canal.

Ejecutar:  pythonw iptv_player.pyw   (o run.bat)
"""
import os
import sys
import json
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

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CACHE_PATH = os.path.join(APP_DIR, "cache.json")
ICON = os.path.join(APP_DIR, "icon.ico")

STAR_ON = "⭐"
STAR_OFF = "☆"


class LiveApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        theme.init()
        self.cfg = load_config()
        self._rebuild_client()
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
        self._fs = False

        self.title("piedrasonic")
        self.geometry(self.cfg.get("window_geometry") or "2335x975")
        self.minsize(1040, 640)
        self.configure(fg_color=C["bg"])
        self._apply_icon()
        theme.style_tree(self)
        self.img_on = tk.PhotoImage(file=os.path.join(APP_DIR, "star_on.png"))
        self.img_off = tk.PhotoImage(file=os.path.join(APP_DIR, "star_off.png"))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        if settings.has_credentials(self.cfg):
            self.after(150, lambda: self.load(force=False))
        else:
            self.after(200, lambda: self.open_account(first=True))

    def _rebuild_client(self):
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
        body.columnconfigure(0, weight=0, minsize=214)
        body.columnconfigure(1, weight=0, minsize=330)
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
        self.count_lbl = ctk.CTkLabel(ch_col, text="CANALES", text_color=C["faint"],
                                      font=font(11, "bold"))
        self.count_lbl.pack(anchor="w", padx=18, pady=(0, 6))

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
                                request_fullscreen=self.toggle_fullscreen)
        self.player.grid(row=0, column=0, sticky="nsew")
        self._build_catchup()

        self.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.bind("<Escape>", lambda e: self.toggle_fullscreen(False))
        self.bind("<F5>", lambda e: self.load(force=True))

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
        ctk.CTkButton(a, text="● Directo", width=84, height=28, corner_radius=8,
                      fg_color=C["accent"], hover_color=C["accent_hi"],
                      text_color="#ffffff", font=font(11, "bold"),
                      command=self.play_current).pack(side=tk.LEFT, padx=(8, 14))

    # ------------------------------------------------------------------
    def load(self, force):
        threading.Thread(target=self._load, args=(force,), daemon=True).start()

    def _load(self, force):
        try:
            if not force and os.path.exists(CACHE_PATH):
                cache = json.load(open(CACHE_PATH, encoding="utf-8"))
                self.categories = cache["categories"]
                self.all_streams = cache["streams"]
            else:
                self.client.login()
                self.categories = self.client.live_categories()
                self.all_streams = self.client.live_streams()
                json.dump({"categories": self.categories, "streams": self.all_streams},
                          open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
            self.cat_name = {str(c["category_id"]): c["category_name"]
                             for c in self.categories}
            self.by_id = {str(s["stream_id"]): s for s in self.all_streams}
            by = {}
            for s in self.all_streams:
                by.setdefault(str(s.get("category_id")), []).append(s)
            self.streams_by_cat = by
            self.after(0, self._populate)
        except Exception as e:
            self.after(0, lambda: messagebox.showerror(
                "Error de conexión", f"No se pudo cargar la lista.\n\n{e}"))

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
    def toggle_fullscreen(self, want=None):
        target = (not self._fs) if want is None else bool(want)
        if target == self._fs:
            return
        self._fs = target
        if target:
            self.cat_col.grid_remove()
            self.ch_col.grid_remove()
            self.body.columnconfigure(0, minsize=0)
            self.body.columnconfigure(1, minsize=0)
            self.attributes("-fullscreen", True)
        else:
            self.attributes("-fullscreen", False)
            self.body.columnconfigure(0, minsize=214)
            self.body.columnconfigure(1, minsize=330)
            self.cat_col.grid()
            self.ch_col.grid()
        self.player.set_fullscreen(target)

    def play_current(self):
        s = self._selected() or self.current_stream
        if not s:
            return
        self.current_stream = s
        self.player.play(self.client.live_url(s["stream_id"]),
                         title=s.get("name", ""), live=True)

    def play_catchup(self, minutes_back):
        s = self.current_stream or self._selected()
        if not s:
            return
        start_dt = datetime.now() - timedelta(minutes=minutes_back)
        start = start_dt.strftime("%Y-%m-%d:%H-%M")
        url = self.client.timeshift_url(s["stream_id"], start, minutes_back + 240)
        self.player.play(url, title=f"{s.get('name','')} · catch-up", live=False)

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
        try:
            if not self._fs and self.state() == "normal":
                self.cfg["window_geometry"] = self.geometry()
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
