"""Image helpers with Pillow. See SKILL.md for every command.

python image_tools.py info|resize|crop|convert|compress|watermark|strip|sheet ...
"""

import argparse
import io
from pathlib import Path

from PIL import ExifTags, Image, ImageDraw, ImageFont, ImageOps


def load(path: str) -> Image.Image:
    img = Image.open(path)
    return ImageOps.exif_transpose(img)


def save(img: Image.Image, out: str, quality: int = 88) -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fmt = Path(out).suffix.lower()
    if fmt in (".jpg", ".jpeg", ".pdf") and img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, "white")
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    kwargs = {"quality": quality, "optimize": True} if fmt in (".jpg", ".jpeg", ".webp") else {}
    img.save(out, **kwargs)
    print(f"{out}: {img.width}x{img.height}, {Path(out).stat().st_size / 1024:,.0f} KB")


def font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def info(path: str) -> None:
    img = Image.open(path)
    print(
        f"{path}: {img.format}, {img.width}x{img.height}, mode "
        f"{img.mode}, {Path(path).stat().st_size / 1024:,.0f} KB"
    )
    if img.info.get("dpi"):
        print(f"DPI: {img.info['dpi']}")
    exif = img.getexif()
    if exif:
        names = {v: k for k, v in ExifTags.TAGS.items()}
        for key in ("Make", "Model", "DateTime", "Orientation", "Software"):
            if names[key] in exif:
                print(f"{key}: {exif[names[key]]}")
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        print("GPS location: present (strip it before sharing)" if gps else "GPS location: none")
    if getattr(img, "n_frames", 1) > 1:
        print(f"Frames: {img.n_frames}")


def resize(a) -> None:
    img = load(a.input)
    w, h = img.size
    if a.max:
        s = a.max / max(w, h)
    elif a.width:
        s = a.width / w
    elif a.height:
        s = a.height / h
    else:
        raise SystemExit("give --max, --width or --height")
    if s >= 1 and not a.upscale:
        print("Already smaller than that; copying without enlarging (use --upscale to enlarge)")
        s = 1
    save(img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS), a.output)


def crop(a) -> None:
    img = load(a.input)
    if a.box:
        box = tuple(int(v) for v in a.box.split(","))
    elif a.aspect:
        rw, rh = (float(v) for v in a.aspect.split(":"))
        w, h = img.size
        if w / h > rw / rh:
            nw = round(h * rw / rh)
            box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
        else:
            nh = round(w * rh / rw)
            box = (0, (h - nh) // 2, w, (h - nh) // 2 + nh)
    else:
        raise SystemExit("give --box or --aspect")
    save(img.crop(box), a.output)


def convert(a) -> None:
    imgs = [load(p) for p in a.inputs]
    if Path(a.output).suffix.lower() == ".pdf":
        rgb = [i.convert("RGB") for i in imgs]
        Path(a.output).parent.mkdir(parents=True, exist_ok=True)
        rgb[0].save(a.output, save_all=True, append_images=rgb[1:], resolution=150)
        print(f"{a.output}: {len(rgb)} page(s)")
        return
    if len(imgs) > 1:
        raise SystemExit("several inputs only make sense for a PDF")
    save(imgs[0], a.output)


def compress(a) -> None:
    img = load(a.input).convert("RGB")
    target = a.kb * 1024
    for scale in (1.0, 0.85, 0.7, 0.55, 0.4, 0.3):
        cand = (
            img
            if scale == 1
            else img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        )
        for q in (85, 75, 65, 55, 45):
            buf = io.BytesIO()
            cand.save(
                buf,
                "JPEG" if Path(a.output).suffix.lower() in (".jpg", ".jpeg") else "WEBP",
                quality=q,
                optimize=True,
            )
            if buf.tell() <= target:
                Path(a.output).parent.mkdir(parents=True, exist_ok=True)
                Path(a.output).write_bytes(buf.getvalue())
                print(
                    f"{a.output}: {cand.width}x{cand.height}, "
                    f"quality {q}, {buf.tell() / 1024:,.0f} KB"
                )
                return
    raise SystemExit(f"couldn't get under {a.kb} KB without wrecking the image")


def watermark(a) -> None:
    img = load(a.input).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    f = font(max(14, img.width // 30))
    box = d.textbbox((0, 0), a.text, font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    pad = img.width // 50
    x, y = img.width - tw - pad * 2, img.height - th - pad * 2
    d.rectangle((x - pad, y - pad, x + tw + pad, y + th + pad), fill=(0, 0, 0, 110))
    d.text((x, y - box[1]), a.text, font=f, fill=(255, 255, 255, 230))
    save(Image.alpha_composite(img, layer), a.output)


def strip(a) -> None:
    img = load(a.input)
    clean = Image.frombytes(img.mode, img.size, img.tobytes())
    if img.mode == "P":
        clean.putpalette(img.getpalette())
    save(clean, a.output, quality=95)
    print("Metadata removed")


def sheet(a) -> None:
    thumbs = []
    for p in a.inputs:
        im = load(p).convert("RGB")
        im.thumbnail((a.size, a.size))
        thumbs.append((Path(p).name, im))
    cols = a.cols
    rows = (len(thumbs) + cols - 1) // cols
    label = 24
    canvas = Image.new(
        "RGB", (cols * (a.size + 10) + 10, rows * (a.size + label + 10) + 10), "white"
    )
    d = ImageDraw.Draw(canvas)
    f = font(14)
    for i, (name, im) in enumerate(thumbs):
        x = 10 + (i % cols) * (a.size + 10)
        y = 10 + (i // cols) * (a.size + label + 10)
        canvas.paste(im, (x + (a.size - im.width) // 2, y + (a.size - im.height) // 2))
        d.text((x, y + a.size + 4), name[:40], font=f, fill="black")
    save(canvas, a.output)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("info")
    p.add_argument("input")
    p = sub.add_parser("resize")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--max", type=int)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--upscale", action="store_true")
    p = sub.add_parser("crop")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--box")
    p.add_argument("--aspect")
    p = sub.add_parser("convert")
    p.add_argument("inputs", nargs="+")
    p.add_argument("output")
    p = sub.add_parser("compress")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--kb", type=int, required=True)
    p = sub.add_parser("watermark")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("text")
    p = sub.add_parser("strip")
    p.add_argument("input")
    p.add_argument("output")
    p = sub.add_parser("sheet")
    p.add_argument("output")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--cols", type=int, default=4)
    p.add_argument("--size", type=int, default=240)
    a = ap.parse_args()
    if a.cmd == "convert":
        # argparse can't split "inputs... output"; the last path is the output.
        *a.inputs, a.output = a.inputs + [a.output]
        if not a.inputs:
            raise SystemExit("give at least one input and an output")
    if a.cmd == "info":
        info(a.input)
    else:
        {
            "resize": resize,
            "crop": crop,
            "convert": convert,
            "compress": compress,
            "watermark": watermark,
            "strip": strip,
            "sheet": sheet,
        }[a.cmd](a)


if __name__ == "__main__":
    main()
