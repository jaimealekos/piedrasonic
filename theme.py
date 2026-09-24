"""
Tema visual (oscuro, elegante) basado en CustomTkinter.
Define la paleta, las fuentes y el estilo del ttk.Treeview que se usa para
las listas largas (CustomTkinter no trae tabla nativa).
"""
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

# Paleta -------------------------------------------------------------------
C = {
    "bg":        "#0b0b0d",   # ventana
    "surface":   "#151517",   # tarjetas / sidebars
    "surface2":  "#1e1e22",   # elementos elevados / hover
    "surface3":  "#26262b",
    "stroke":    "#2a2a2f",
    "text":      "#f2f2f5",
    "muted":     "#9a9aa3",
    "faint":     "#6c6c74",
    "accent":    "#0a84ff",
    "accent_hi": "#3b9bff",
    "accent_lo": "#0060df",
    "hover":     "#17324b",   # tinte de acento para hover
    "gold":      "#f2c24c",   # estrellas / cabeceras de grupo
    "gold_dim":  "#8a7327",
    "teal":      "#2dd4bf",
    "video":     "#000000",
    "ok":        "#30d158",
    "warn":      "#ff9f0a",
    "danger":    "#ff453a",
    "overlay":   "#141416",
}

FONT_FAMILY = "Segoe UI"


def font(size=13, weight="normal"):
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def mezcla(a, b, t):
    """Color intermedio entre dos, en formato #rrggbb. t=0 da `a`, t=1 da `b`.

    Tk no sabe de transparencias: para que algo se vea "a medio encender" hay
    que darle el color ya mezclado con el fondo sobre el que se pinta.
    """
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    ra, ga, ba = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    rb, gb, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t),
                              round(ga + (gb - ga) * t),
                              round(ba + (bb - ba) * t))


def init():
    ctk.set_appearance_mode("dark")
    ctk.deactivate_automatic_dpi_awareness  # no-op ref; DPI se gestiona por CTk


def style_tree(root):
    """Estiliza ttk.Treeview y Scrollbar para integrarlos en el tema oscuro."""
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Dark.Treeview",
                    background=C["surface"], fieldbackground=C["surface"],
                    foreground=C["text"], borderwidth=0, relief="flat",
                    rowheight=34, font=(FONT_FAMILY, 11))
    style.map("Dark.Treeview",
              background=[("selected", C["accent"])],
              foreground=[("selected", "#ffffff")])
    style.layout("Dark.Treeview", [("Dark.Treeview.treearea", {"sticky": "nswe"})])
    # scrollbar muy sutil: sin flechas, fino y fundido con el panel
    style.layout("Dark.Vertical.TScrollbar", [
        ("Dark.Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Dark.Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
        ]})
    ])
    style.configure("Dark.Vertical.TScrollbar",
                    background=C["stroke"], troughcolor=C["surface"],
                    borderwidth=0, relief="flat", width=6)
    style.map("Dark.Vertical.TScrollbar",
              background=[("active", C["surface3"]), ("!active", C["stroke"])])
    return style
