"""Genera star_on.png (dorada) y star_off.png (contorno) para los favoritos."""
import os
import math
from PIL import Image, ImageDraw

APP = os.path.dirname(os.path.abspath(__file__))
SS = 4          # supersampling
SZ = 18
S = SZ * SS


def star_points(cx, cy, ro, ri, n=5, rot=-math.pi / 2):
    pts = []
    for i in range(n * 2):
        r = ro if i % 2 == 0 else ri
        a = rot + i * math.pi / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def make(fill, outline, ow, path):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = star_points(S / 2, S / 2 + SS, S * 0.44, S * 0.19)
    d.polygon(pts, fill=fill, outline=outline, width=ow)
    img = img.resize((SZ, SZ), Image.LANCZOS)
    img.save(path)


# dorada rellena
make((242, 194, 76, 255), (201, 151, 31, 255), SS, os.path.join(APP, "star_on.png"))
# contorno sutil (no favorito)
make((0, 0, 0, 0), (120, 120, 130, 255), SS + 1, os.path.join(APP, "star_off.png"))
print("estrellas generadas")
