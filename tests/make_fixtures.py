"""Generates the test images (flyer-style prices). Real flyer images cannot be downloaded
in the build workspace, so the image reader is tested on these."""
import glob
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FIX = Path(__file__).parent / "fixtures"
_f = [f for f in glob.glob("/usr/share/fonts/**/*.ttf", recursive=True) if "LiberationSans-Bold.ttf" in f or "DejaVuSans-Bold.ttf" in f]
FONT = _f[0] if _f else None


def font(size):
    return ImageFont.truetype(FONT, size) if FONT else ImageFont.load_default(size)


def price_image(name, big, small, caption=None, fg="red", bg="white", size=(600, 400), top_text=None):
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    F, S, T = font(160), font(70), font(30)
    x = 60
    d.text((x, 70), "$", font=S, fill=fg); x += 55
    d.text((x, 50), big, font=F, fill=fg); x += d.textlength(big, font=F) + 8
    d.text((x, 62), small, font=S, fill=fg)
    if top_text:
        d.text((40, 10), top_text, font=T, fill="black" if bg == "white" else "white")
    if caption:
        d.text((40, 290), caption, font=T, fill="black" if bg == "white" else "white")
    FIX.mkdir(exist_ok=True)
    path = FIX / name
    im.save(path)
    return path


def text_image(name, lines, size=(600, 300)):
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((30, 30 + i * 60), line, font=font(40), fill="black")
    FIX.mkdir(exist_ok=True)
    path = FIX / name
    im.save(path)
    return path


def product_image(name, label, color=(200, 160, 60), size=(320, 240)):
    im = Image.new("RGB", size, color)
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, size[0] - 10, size[1] - 10), outline="white", width=4)
    d.text((24, size[1] // 2 - 16), label, font=font(26), fill="white")
    FIX.mkdir(exist_ok=True)
    path = FIX / name
    im.save(path)
    return path
