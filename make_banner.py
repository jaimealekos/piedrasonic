"""Genera screenshots/banner.png: cabecera del README (TV retro + nombre)."""
import os
from PIL import Image, ImageDraw, ImageFont

APP = os.path.dirname(os.path.abspath(__file__))
W, H = 1280, 340
BG = (13, 13, 16, 255)
TXT = (242, 242, 245, 255)
MUT = (154, 154, 163, 255)
GOLD = (242, 194, 76, 255)
PALETTE = ["#0a84ff", "#2dd4bf", "#bf5af2", "#ff9f0a", "#30d158",
           "#ff6482", "#64d2ff", "#ff453a", "#5e5ce6", "#ffd60a"]


def font(path, size):
    for p in (path, os.path.join(r"C:\Windows\Fonts", path)):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


img = Image.new("RGBA", (W, H), BG)
d = ImageDraw.Draw(img)

# leve degradado vertical para dar profundidad
for y in range(H):
    t = y / H
    c = (int(13 + 8 * t), int(13 + 8 * t), int(16 + 10 * t))
    d.line([(0, y), (W, y)], fill=c)

# TV retro
tv = Image.open(os.path.join(APP, "icon.png")).convert("RGBA")
tv = tv.resize((210, 210), Image.LANCZOS)
img.paste(tv, (70, (H - 210) // 2), tv)

# título + tagline
f_title = font("segoeuib.ttf", 96)
f_tag = font("segoeui.ttf", 30)
x = 320
d.text((x, 108), "piedrasonic", font=f_title, fill=TXT)
d.text((x + 4, 214), "Reproductor IPTV para Windows  ·  Xtream Codes",
       font=f_tag, fill=MUT)

# fila de puntos de color (guiño al diseño de la app)
cx = x + 6
for hexc in PALETTE:
    c = tuple(int(hexc[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
    d.ellipse([cx, 270, cx + 14, 284], fill=c)
    cx += 26

os.makedirs(os.path.join(APP, "screenshots"), exist_ok=True)
img.convert("RGB").save(os.path.join(APP, "screenshots", "banner.png"))
print("banner generado")
