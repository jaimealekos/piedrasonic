"""Genera icon.ico / icon.png: una TV antigua (CRT retro) para piedrasonic."""
import os
from PIL import Image, ImageDraw

APP = os.path.dirname(os.path.abspath(__file__))
S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

CREAM = (226, 208, 170, 255)
CREAM_D = (169, 150, 112, 255)
WOOD = (150, 96, 54, 255)
SCREEN = (18, 22, 26, 255)
GLOW = (86, 196, 190, 255)
DARK = (40, 34, 30, 255)
RED = (223, 78, 68, 255)
KNOB = (74, 66, 60, 255)


def rr(box, r, **kw):
    d.rounded_rectangle(box, radius=r, **kw)


# --- antenas ---
d.line([(130, 92), (74, 20)], fill=DARK, width=9)
d.line([(130, 92), (196, 26)], fill=DARK, width=9)
for pt in [(74, 20), (196, 26)]:
    d.ellipse([pt[0] - 9, pt[1] - 9, pt[0] + 9, pt[1] + 9], fill=DARK)

# --- cuerpo ---
rr([24, 84, 232, 224], 26, fill=CREAM, outline=CREAM_D, width=4)
rr([24, 84, 232, 224], 26, outline=WOOD, width=2)

# --- marco de pantalla ---
rr([42, 100, 186, 208], 18, fill=DARK)
# --- pantalla ---
rr([50, 108, 178, 200], 12, fill=SCREEN)
# brillo/scanline
for i, y in enumerate(range(116, 196, 9)):
    a = 40 - i * 3
    if a > 0:
        d.line([(58, y), (170, y)], fill=(GLOW[0], GLOW[1], GLOW[2], max(a, 0)), width=2)
# reflejo diagonal
d.polygon([(58, 116), (96, 116), (66, 192), (58, 192)], fill=(255, 255, 255, 22))

# --- panel derecho: diales + rejilla ---
d.ellipse([198, 116, 220, 138], fill=KNOB, outline=DARK, width=2)
d.ellipse([198, 148, 220, 170], fill=KNOB, outline=DARK, width=2)
d.ellipse([205, 186, 213, 194], fill=RED)               # piloto encendido
for gy in range(120, 172, 8):                            # rejilla altavoz
    d.line([(224, gy), (226, gy)], fill=CREAM_D, width=3)

# --- patas ---
d.line([(70, 224), (58, 240)], fill=DARK, width=8)
d.line([(186, 224), (198, 240)], fill=DARK, width=8)

img.save(os.path.join(APP, "icon.png"))
img.save(os.path.join(APP, "icon.ico"),
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icono generado:", os.path.join(APP, "icon.ico"))
