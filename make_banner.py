"""Genera screenshots/banner.png: cabecera del README.
TV retro con carta de ajuste (barras de color) + nombre y subtítulo."""
import os
from PIL import Image, ImageDraw, ImageFont

APP = os.path.dirname(os.path.abspath(__file__))
W, H = 1280, 340
BG_TOP = (13, 13, 16)
BG_BOT = (21, 21, 26)
TXT = (242, 242, 245, 255)
MUT = (154, 154, 163, 255)

CREAM = (226, 208, 170, 255)
CREAM_D = (169, 150, 112, 255)
WOOD = (150, 96, 54, 255)
DARK = (36, 30, 26, 255)

# barras de color de la carta de ajuste (EBU)
BARS = [(236, 236, 236), (232, 220, 60), (48, 200, 214), (62, 190, 92),
        (200, 72, 180), (210, 60, 60), (52, 82, 202)]


def font(path, size):
    for p in (path, os.path.join(r"C:\Windows\Fonts", path)):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def make_tv(box_w, box_h, ss=3):
    """Dibuja la TV con carta de ajuste a alta resolución y la reduce."""
    W2, H2 = box_w * ss, box_h * ss
    img = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def s(v):
        return int(v * ss)

    cw = box_w
    # --- antenas ---
    topc = (cw * 0.52, 42)
    for end in ((cw * 0.16, 6), (cw * 0.86, 10)):
        d.line([(s(topc[0]), s(topc[1])), (s(end[0]), s(end[1]))],
               fill=DARK, width=s(3.2))
        d.ellipse([s(end[0]) - s(5), s(end[1]) - s(5),
                   s(end[0]) + s(5), s(end[1]) + s(5)], fill=DARK)

    # --- cuerpo ---
    body = (10, 40, cw - 10, box_h - 6)
    d.rounded_rectangle([s(body[0]), s(body[1]), s(body[2]), s(body[3])],
                        radius=s(22), fill=CREAM, outline=CREAM_D, width=s(3))

    # --- bisel de pantalla ---
    scr = (30, 58, cw - 78, box_h - 26)          # deja hueco a la derecha (diales)
    d.rounded_rectangle([s(scr[0]), s(scr[1]), s(scr[2]), s(scr[3])],
                        radius=s(12), fill=DARK)

    # --- pantalla: carta de ajuste ---
    m = 7
    x0, y0, x1, y1 = scr[0] + m, scr[1] + m, scr[2] - m, scr[3] - m
    sw, sh = x1 - x0, y1 - y0
    scr_img = Image.new("RGBA", (s(sw), s(sh)), (10, 12, 14, 255))
    sd = ImageDraw.Draw(scr_img)
    # barras verticales (2/3 superiores)
    band = int(s(sh) * 0.66)
    bw = s(sw) / len(BARS)
    for i, c in enumerate(BARS):
        sd.rectangle([int(i * bw), 0, int((i + 1) * bw), band], fill=c + (255,))
    # franja inferior: escala de grises
    steps = [(20, 20, 20), (70, 70, 70), (120, 120, 120), (170, 170, 170),
             (210, 210, 210), (240, 240, 240)]
    gw = s(sw) / len(steps)
    for i, c in enumerate(steps):
        sd.rectangle([int(i * gw), band, int((i + 1) * gw), s(sh)], fill=c + (255,))
    # círculo central (guiño a la carta de ajuste)
    cx, cy = s(sw) // 2, int(band * 0.5)
    r = int(band * 0.42)
    sd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(245, 245, 245, 230),
               width=s(1.4))
    sd.line([cx - r, cy, cx + r, cy], fill=(245, 245, 245, 120), width=s(1))
    sd.line([cx, cy - r, cx, cy + r], fill=(245, 245, 245, 120), width=s(1))
    # scanlines CRT
    for yy in range(0, s(sh), s(4)):
        sd.line([(0, yy), (s(sw), yy)], fill=(0, 0, 0, 45), width=s(1))
    img.paste(scr_img, (s(x0), s(y0)), scr_img)

    # --- diales + piloto a la derecha ---
    dx = cw - 60
    d.ellipse([s(dx), s(70), s(dx + 22), s(92)], fill=(74, 66, 60, 255),
              outline=DARK, width=s(2))
    d.ellipse([s(dx), s(104), s(dx + 22), s(126)], fill=(74, 66, 60, 255),
              outline=DARK, width=s(2))
    d.ellipse([s(dx + 7), s(box_h - 52), s(dx + 15), s(box_h - 44)],
              fill=(223, 78, 68, 255))

    # --- patas ---
    d.line([(s(38), s(box_h - 6)), (s(28), s(box_h + 8))], fill=DARK, width=s(5))
    d.line([(s(cw - 40), s(box_h - 6)), (s(cw - 30), s(box_h + 8))],
           fill=DARK, width=s(5))

    return img.resize((box_w, box_h + 8), Image.LANCZOS)


# ---- lienzo con degradado ----
img = Image.new("RGB", (W, H))
d = ImageDraw.Draw(img)
for y in range(H):
    t = y / H
    d.line([(0, y), (W, y)],
           fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT)))
img = img.convert("RGBA")
d = ImageDraw.Draw(img)

# ---- TV ----
TVW, TVH = 250, 224
tv = make_tv(TVW, TVH)
tv_x, tv_y = 66, (H - TVH) // 2 - 6
img.paste(tv, (tv_x, tv_y), tv)

# ---- textos (medidos para no solaparse) ----
f_title = font("segoeuib.ttf", 92)
f_tag = font("segoeui.ttf", 29)
x = tv_x + TVW + 44

tb = d.textbbox((0, 0), "piedrasonic", font=f_title)
th = tb[3] - tb[1]
sb = d.textbbox((0, 0), "Reproductor IPTV para Windows", font=f_tag)
sh = sb[3] - sb[1]
gap = 20
block = th + gap + sh
top = (H - block) // 2

d.text((x - tb[0], top - tb[1]), "piedrasonic", font=f_title, fill=TXT)
d.text((x - sb[0] + 2, top + th + gap - sb[1]),
       "Reproductor IPTV para Windows  ·  Xtream Codes", font=f_tag, fill=MUT)

os.makedirs(os.path.join(APP, "screenshots"), exist_ok=True)
img.convert("RGB").save(os.path.join(APP, "screenshots", "banner.png"))
print("banner generado")
