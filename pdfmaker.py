"""
Сборка страниц и PDF: размеры листа, «удостоверение на A4», собственный
лёгкий PDF-писатель (JPEG внутри PDF без перекодирования).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np

import scanner as sc

MM_PER_INCH = 25.4
A4_MM = (210.0, 297.0)
ID1_MM = (85.60, 53.98)
ID1_CORNER_MM = 3.18


@dataclass
class PdfPage:
    image: np.ndarray       # готовая картинка страницы (BGR или серая)
    dpi: int                # разрешение, с которым она ложится на лист
    width_pt: float         # размер листа в пунктах (1/72 дюйма)
    height_pt: float


# ───────────────────────────── раскладка листа ───────────────────────────

def layout_page(img: np.ndarray, *, min_dpi: int = 150, max_dpi: int = 300) -> PdfPage:
    """
    Подбираем физический размер листа. Если пропорции близки к A4 — ровно A4
    (книжный или альбомный), иначе ширина как у A4, высота по пропорциям
    (чеки, визитки и т.п.).
    """
    h, w = img.shape[:2]
    landscape = w > h
    ratio = h / w
    a4 = A4_MM[1] / A4_MM[0] if not landscape else A4_MM[0] / A4_MM[1]
    page_w_mm = A4_MM[1] if landscape else A4_MM[0]
    if abs(ratio / a4 - 1) < 0.07:
        page_h_mm = A4_MM[0] if landscape else A4_MM[1]
    else:
        page_h_mm = page_w_mm * ratio
    page_w_in, page_h_in = page_w_mm / MM_PER_INCH, page_h_mm / MM_PER_INCH
    dpi = int(min(max_dpi, max(min_dpi, round(w / page_w_in))))
    tw, th = max(1, round(page_w_in * dpi)), max(1, round(page_h_in * dpi))
    if (tw, th) != (w, h):
        interp = cv2.INTER_AREA if tw < w else cv2.INTER_CUBIC
        img = cv2.resize(img, (tw, th), interpolation=interp)
    return PdfPage(img, dpi, page_w_in * 72, page_h_in * 72)


def _rounded_mask(w: int, h: int, r: int) -> np.ndarray:
    mask = np.zeros((h, w), np.uint8)
    r = max(1, min(r, w // 2, h // 2))
    cv2.rectangle(mask, (r, 0), (w - 1 - r, h - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (w - 1, h - 1 - r), 255, -1)
    for cx, cy in ((r, r), (w - 1 - r, r), (r, h - 1 - r), (w - 1 - r, h - 1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
    return mask


def compose_id_sheet(sides: list[tuple[np.ndarray, bool]], dpi: int = 300) -> PdfPage:
    """
    Стороны удостоверения на листе A4 в натуральную величину, одна под другой.
    sides: [(картинка, это_точно_карта)], если «не карта» — вписываем с сохранением пропорций.
    """
    px = lambda mm: int(round(mm / MM_PER_INCH * dpi))  # noqa: E731
    W, H = px(A4_MM[0]), px(A4_MM[1])
    gray = all(img.ndim == 2 for img, _ in sides)
    sheet = np.full((H, W) if gray else (H, W, 3), 255, np.uint8)
    cw, ch = px(ID1_MM[0]), px(ID1_MM[1])
    y = px(40)
    for img, is_card in sides:
        if not gray and img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        ih, iw = img.shape[:2]
        if is_card:
            tw, th = cw, ch
        else:
            s = min(cw * 1.25 / iw, ch * 1.6 / ih)
            tw, th = max(1, round(iw * s)), max(1, round(ih * s))
        interp = cv2.INTER_AREA if tw < iw else cv2.INTER_CUBIC
        tile = cv2.resize(img, (tw, th), interpolation=interp)
        x = (W - tw) // 2
        if y + th > H:
            break
        if is_card:
            mask = _rounded_mask(tw, th, px(ID1_CORNER_MM)).astype(np.float32) / 255
            if not gray:
                mask = mask[..., None]
            region = sheet[y:y + th, x:x + tw].astype(np.float32)
            sheet[y:y + th, x:x + tw] = (tile * mask + region * (1 - mask)).astype(np.uint8)
            # тонкая рамка по контуру карты — как край карты на ксерокопии
            r = px(ID1_CORNER_MM)
            inner = _rounded_mask(tw - 4, th - 4, r - 2)
            ring = _rounded_mask(tw, th, r)
            ring[2:-2, 2:-2] = cv2.subtract(ring[2:-2, 2:-2], inner)
            sel = ring > 128
            roi = sheet[y:y + th, x:x + tw]
            roi[sel] = np.minimum(roi[sel], 150)
        else:
            sheet[y:y + th, x:x + tw] = tile
        y += th + px(15)
    return PdfPage(sheet, dpi, W / dpi * 72, H / dpi * 72)


# ───────────────────────────────── PDF ───────────────────────────────────

def _pdf_text(s: str) -> str:
    """Строка PDF в UTF-16BE (для кириллицы в свойствах файла)."""
    return "<FEFF" + s.encode("utf-16-be").hex().upper() + ">"


def page_jpeg(page: PdfPage, quality: int = 80) -> tuple[bytes, bool]:
    img = page.image
    if img.ndim == 3 and _looks_gray(img):
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return sc.encode_jpeg(img, quality), img.ndim == 2


def _looks_gray(img: np.ndarray) -> bool:
    small = sc.resize_max(img, 400).astype(np.int16)
    spread = small.max(axis=2) - small.min(axis=2)
    return float(np.percentile(spread, 99.5)) < 12


def build_pdf(pages: list[PdfPage], title: str = "Скан", quality: int = 80) -> bytes:
    """Простой и надёжный PDF: по одной JPEG-картинке на страницу."""
    out = io.BytesIO()
    offsets: dict[int, int] = {}

    def obj(num: int, body: bytes) -> None:
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode("ascii"))
        out.write(body)
        out.write(b"\nendobj\n")

    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    n = len(pages)
    kids = " ".join(f"{4 + 3 * i} 0 R" for i in range(n))
    obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode("ascii"))
    now = datetime.now().strftime("D:%Y%m%d%H%M%S")
    obj(3, f"<< /Title {_pdf_text(title)} /Producer (ScanBot) /Creator (ScanBot) "
           f"/CreationDate ({now}) /ModDate ({now}) >>".encode("ascii"))
    for i, page in enumerate(pages):
        p_num, im_num, c_num = 4 + 3 * i, 5 + 3 * i, 6 + 3 * i
        jpg, is_gray = page_jpeg(page, quality)
        h, w = page.image.shape[:2]
        pw, ph = page.width_pt, page.height_pt
        obj(p_num, (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.3f} {ph:.3f}] "
                    f"/Resources << /XObject << /Im0 {im_num} 0 R >> /ProcSet [/PDF /ImageB /ImageC] >> "
                    f"/Contents {c_num} 0 R >>").encode("ascii"))
        cs = "/DeviceGray" if is_gray else "/DeviceRGB"
        obj(im_num, (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {cs} "
                     f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpg)} >>\nstream\n").encode("ascii")
            + jpg + b"\nendstream")
        content = f"q {pw:.3f} 0 0 {ph:.3f} 0 0 cm /Im0 Do Q".encode("ascii")
        obj(c_num, f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream")
    xref = out.tell()
    size = 4 + 3 * n
    out.write(f"xref\n0 {size}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for num in range(1, size):
        out.write(f"{offsets[num]:010d} 00000 n \n".encode("ascii"))
    out.write(f"trailer\n<< /Size {size} /Root 1 0 R /Info 3 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return out.getvalue()


def add_text_layers(pdf: bytes, text_pages: list[bytes | None]) -> bytes:
    """Накладываем невидимый текстовый слой (PDF от Tesseract) на страницы-картинки."""
    from pypdf import PdfReader, PdfWriter, Transformation

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    for page, layer in zip(writer.pages, text_pages):
        if not layer:
            continue
        tpage = PdfReader(io.BytesIO(layer)).pages[0]
        sx = float(page.mediabox.width) / float(tpage.mediabox.width)
        sy = float(page.mediabox.height) / float(tpage.mediabox.height)
        if math.isclose(sx, 1, abs_tol=1e-3) and math.isclose(sy, 1, abs_tol=1e-3):
            page.merge_page(tpage)
        else:
            page.merge_transformed_page(tpage, Transformation().scale(sx, sy))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
