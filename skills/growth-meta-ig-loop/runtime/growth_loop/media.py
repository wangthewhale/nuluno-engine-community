import json
import math
from pathlib import Path

from .errors import WorkflowError
from .files import atomic_json, file_digest


def dependency_status():
    """Report optional packages without importing or downloading them."""
    from importlib.util import find_spec
    return {'image_package_available': find_spec('PIL') is not None,
            'video_package_available': find_spec('imageio_ffmpeg') is not None,
            'install_extra': 'nuluno-engine-community[media]', 'decoder_verified': False}


def _pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError as exc:
        raise WorkflowError("缺Pillow；在需要影像的執行主機安裝nuluno-engine[media]後從本步繼續。", "waiting_capability") from exc
    return Image, ImageDraw, ImageFont, ImageOps


def font(size, text=""):
    _, _, ImageFont, _ = _pillow()
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    if any('\u3400' <= char <= '\u9fff' for char in text):
        candidates = ["/System/Library/Fonts/PingFang.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"] + candidates
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise WorkflowError("缺可用字型；設定可讀字型後續接，不交付亂碼。", "waiting_capability")


def wrap(draw, text, face, width):
    lines, line = [], ""
    tokens = text.split(" ") if " " in text else list(text)
    separator = " " if " " in text else ""
    for word in tokens:
        candidate = line + (separator if line else "") + word
        if draw.textbbox((0, 0), candidate, font=face)[2] > width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def normalized_image(source, destination, size=(1080, 1350), fit_mode='contain'):
    Image, _, _, ImageOps = _pillow()
    if len(size)!=2 or any(type(v) is not int or not 64<=v<=4096 for v in size) or fit_mode not in ('contain','cover'):
        raise ValueError('Image size/fit mode is invalid')
    try:
        with Image.open(source) as raw:
            raw.verify()
        with Image.open(source) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
            if fit_mode=='cover':image=ImageOps.fit(image,size,method=Image.Resampling.LANCZOS)
            else:image=ImageOps.pad(image,size,method=Image.Resampling.LANCZOS,color='white')
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, "JPEG", quality=92, optimize=True)
    except (OSError, ValueError) as exc:
        raise WorkflowError("素材無法解碼；重新製作該檔，不重送壞檔。", "invalid_asset") from exc
    return verify_image(destination, size)


def verify_image(path, expected_size=None):
    Image, _, _, _ = _pillow()
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            info = {"format": image.format, "width": image.width, "height": image.height}
    except (OSError, ValueError) as exc:
        raise WorkflowError("素材檔未通過實際解碼。", "invalid_asset") from exc
    if info["format"] not in ("PNG", "JPEG"):
        raise WorkflowError("素材需轉成PNG或JPEG。", "invalid_asset")
    if expected_size and (info["width"], info["height"]) != tuple(expected_size):
        raise WorkflowError("素材尺寸與工作單不同。", "invalid_asset")
    return {**info, "path": str(Path(path).resolve()), "sha256": file_digest(path), "bytes": Path(path).stat().st_size}


def render_variant(profile, cycle, output_dir):
    Image, ImageDraw, _, _ = _pillow()
    variants = profile["creative"]["variants"]
    variant = variants[(cycle - 1) % len(variants)]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    palette = [("#101D33", "#C7F0D8"), ("#F4EFE6", "#153E36"), ("#182333", "#EBC9A1")]
    background, accent = palette[(cycle - 1) % len(palette)]
    foreground = "#FFFFFF" if cycle % 3 != 2 else "#142A26"
    design = profile["brand"].get("design", {})
    import re
    for field in ("background", "accent", "foreground"):
        if design.get(field) and not re.fullmatch(r"#[0-9a-fA-F]{6}", design[field]):
            raise WorkflowError("品牌色需是六位十六進位色碼。", "invalid_input")
    background, accent, foreground = design.get("background", background), design.get("accent", accent), design.get("foreground", foreground)
    image = Image.new("RGB", (1080, 1350), background)
    draw = ImageDraw.Draw(image)
    draw.text((82, 70), profile["brand"]["name"], font=font(42, profile["brand"]["name"]), fill=foreground)
    motif = design.get("motif", "abstract")
    if motif == "music":
        # Music is an explicit brand option, never the default for all brands.
        staff_y = 265
        for line in range(5):
            draw.line((82, staff_y + line * 24, 998, staff_y + line * 24), fill=accent, width=3)
        for n in range(6):
            x = 180 + n * 125
            y = staff_y + 96 - (n % 4) * 12
            draw.ellipse((x, y - 10, x + 25, y + 7), fill=accent)
            draw.line((x + 24, y, x + 24, y - 65), fill=accent, width=4)
    elif motif == "abstract":
        for index, width in enumerate((390, 260, 180)):
            x = 82 + index * 315
            draw.rounded_rectangle((x, 235 + index * 15, min(x + width, 998), 340 + index * 15), radius=28, outline=accent, width=5)
        draw.line((105, 405, 975, 405), fill=accent, width=3)
    else:
        raise WorkflowError("未知素材樣式；請選abstract或music，或由宿主另製已核對素材。", "invalid_input")
    headline_size, body_size = 78, 34
    while True:
        headline_font = font(headline_size, variant["headline"])
        body_font = font(body_size, variant["body"])
        head_lines = wrap(draw, variant["headline"], headline_font, 916)
        body_lines = wrap(draw, variant["body"], body_font, 900)
        height = len(head_lines) * int(headline_size * 1.27) + 50 + len(body_lines) * int(body_size * 1.53)
        if height <= 620:
            break
        if headline_size <= 48 and body_size <= 26:
            raise WorkflowError("文字仍超出安全區；宿主需縮短本輪文案後續接。", "waiting_agent", details={"repair": "shorten_copy", "headline": variant["headline"], "body": variant["body"]})
        headline_size, body_size = max(48, headline_size - 4), max(26, body_size - 2)
    y = 460
    for line in head_lines:
        draw.text((82, y), line, font=headline_font, fill=foreground)
        y += int(headline_size * 1.27)
    y += 50
    for line in body_lines:
        draw.text((82, y), line, font=body_font, fill=foreground)
        y += int(body_size * 1.53)
    if y > 1090:
        raise WorkflowError("文字超出素材安全區；縮短文案後重製。", "copy_overflow")
    draw.rounded_rectangle((82, 1135, 998, 1240), radius=20, fill=accent)
    cta = variant.get("cta", "Learn more")
    cta_size = 40
    while draw.textbbox((0, 0), cta, font=font(cta_size, cta))[2] > 840 and cta_size > 24:
        cta_size -= 2
    if draw.textbbox((0, 0), cta, font=font(cta_size, cta))[2] > 840:
        raise WorkflowError("行動文字太長，需縮短後重製。", "waiting_agent", details={"repair": "shorten_cta"})
    draw.text((118, 1161), cta, font=font(cta_size, cta), fill=background)
    path = output_dir / ("creative-c%02d.jpg" % cycle)
    image.save(path, "JPEG", quality=94, optimize=True)
    caption = variant.get("caption") or variant["headline"] + "\n\n" + variant["body"] + "\n\n" + profile["brand"]["url"]
    result = {
        "cycle": cycle, "variant_id": variant.get("id", str((cycle - 1) % len(variants))),
        "headline": variant["headline"], "body": variant["body"], "caption": caption,
        "cta": cta, "fact_ids": variant["fact_ids"], "design_motif": motif,
        "asset": verify_image(path, (1080, 1350)), "production": "code-rendered original text graphic",
        "public_image_url": variant.get("public_image_url"),
    }
    atomic_json(output_dir / ("creative-c%02d.json" % cycle), result)
    return result
