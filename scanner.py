"""
Движок сканера: находит лист (или карту) на фото, выравнивает перспективу
и применяет фильтры.

Только OpenCV + NumPy + Pillow, без нейросетей — работает на любом ПК.
"""
from __future__ import annotations

import io
import logging
import math

import cv2
import numpy as np
from PIL import Image, ImageOps

try:  # фото с iPhone, отправленные «файлом», приходят в HEIC
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception:  # пакет необязательный
    pass

log = logging.getLogger("scanner")

A4_RATIO = 297 / 210              # высота / ширина листа A4
ID1_RATIO = 85.60 / 53.98         # ширина / высота карты ID-1 (удостоверение, права)

MAX_SIDE = 4000                   # исходник храним не больше этого
DETECT_SIDE = 800                 # размер, на котором ищем лист
REFINE_SIDE = 1600                # размер, на котором уточняем края
MAX_OUT_SIDE = 3508               # A4 при 300 dpi — больше не нужно

FILTERS = ("bw", "gray", "color", "orig")  # названия на кнопках — в texts.py


# ───────────────────────────── ввод / вывод ──────────────────────────────

def decode_image(data: bytes, max_side: int = MAX_SIDE) -> np.ndarray:
    """Байты картинки (jpg/png/webp/heic…) → BGR-массив с учётом EXIF-поворота."""
    with Image.open(io.BytesIO(data)) as im:
        if im.format == "JPEG":
            im.draft("RGB", (max_side, max_side))  # быстрое уменьшение при декодировании
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side), Image.LANCZOS)
        return np.ascontiguousarray(np.asarray(im)[:, :, ::-1])


def encode_jpeg(img: np.ndarray, quality: int = 90) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        raise ValueError("Не удалось закодировать JPEG")
    return buf.tobytes()


def decode_jpeg(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Не удалось прочитать картинку")
    return img


def resize_max(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def rotate(img: np.ndarray, quarter_turns: int) -> np.ndarray:
    """Поворот по часовой стрелке на 90° × quarter_turns."""
    k = quarter_turns % 4
    if k == 0:
        return img
    code = {1: cv2.ROTATE_90_CLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_COUNTERCLOCKWISE}[k]
    return cv2.rotate(img, code)


def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _odd(x: float) -> int:
    x = max(1, int(round(x)))
    return x if x % 2 else x + 1


# ───────────────────────────── поиск листа ───────────────────────────────

def order_corners(pts: np.ndarray) -> np.ndarray:
    """4 точки → порядок: левый-верх, правый-верх, правый-низ, левый-низ."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]  # по часовой (ось y вниз)
    start = int(np.argmin(pts.sum(axis=1)))
    return np.roll(pts, -start, axis=0)


def _canny(img: np.ndarray, lo: float, hi: float) -> np.ndarray:
    e = cv2.Canny(img, lo, hi, L2gradient=True)
    return cv2.dilate(e, np.ones((3, 3), np.uint8))


def _contour_quad(cnt: np.ndarray) -> np.ndarray | None:
    hull = cv2.convexHull(cnt)
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.07):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return order_corners(approx.reshape(4, 2))
        if len(approx) < 4:
            break
    # лист с «отрезанным» пальцем углом и т.п. — берём описанный прямоугольник,
    # если контур на него очень похож
    rect = cv2.minAreaRect(hull)
    rw, rh = rect[1]
    if rw * rh > 0 and cv2.contourArea(hull) / (rw * rh) > 0.93:
        return order_corners(cv2.boxPoints(rect))
    return None


def _side_support(gx: np.ndarray, gy: np.ndarray, quad: np.ndarray, n: int = 60,
                  band: float = 2.5, min_grad: float = 6.0) -> np.ndarray:
    """
    Насколько каждая сторона четырёхугольника похожа на настоящий край листа:
    доля точек, где есть перепад яркости поперёк стороны, и перепад везде
    одного знака (лист светлее/темнее фона по всей длине). Полосы на столе,
    плитка и т.п. дают перепады «вразнобой» — такие стороны не засчитываются.
    """
    h, w = gx.shape
    res = np.zeros(4)
    center = quad.mean(axis=0)
    ts = np.linspace(0.06, 0.94, n)
    offs = np.linspace(-band, band, 9)
    m = 0.015 * max(h, w)
    for i in range(4):
        p0, p1 = quad[i].astype(np.float64), quad[(i + 1) % 4].astype(np.float64)
        # сторона, лежащая на рамке кадра, — это не край листа
        if (max(p0[0], p1[0]) < m or min(p0[0], p1[0]) > w - 1 - m or
                max(p0[1], p1[1]) < m or min(p0[1], p1[1]) > h - 1 - m):
            continue
        d = p1 - p0
        d /= np.linalg.norm(d) + 1e-9
        nrm = np.array([-d[1], d[0]])
        if np.dot(center - p0, nrm) < 0:
            nrm = -nrm  # нормаль внутрь
        P = p0 + ts[:, None] * (p1 - p0)
        Q = P[:, None, :] + offs[None, :, None] * nrm
        mx, my = Q[..., 0].astype(np.float32), Q[..., 1].astype(np.float32)
        gxs = cv2.remap(gx, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        gys = cv2.remap(gy, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        gn = gxs * nrm[0] + gys * nrm[1]
        gt = gxs * d[0] + gys * d[1]
        j = np.abs(gn).argmax(axis=1)
        k = np.arange(n)
        gnb, gtb = gn[k, j], gt[k, j]
        good = (np.abs(gnb) > min_grad) & (np.abs(gnb) > 2.0 * np.abs(gtb))
        if not good.any():
            continue
        pos = np.count_nonzero(good & (gnb > 0))
        neg = np.count_nonzero(good & (gnb < 0))
        res[i] = max(pos, neg) / n
    return res


def _quad_ok(quad: np.ndarray, img_w: int, img_h: int) -> bool:
    if not cv2.isContourConvex(quad.reshape(-1, 1, 2).astype(np.float32)):
        return False
    sides = [np.linalg.norm(quad[(i + 1) % 4] - quad[i]) for i in range(4)]
    if min(sides) < 0.12 * max(sides):
        return False
    for i in range(4):
        a = quad[i - 1] - quad[i]
        b = quad[(i + 1) % 4] - quad[i]
        cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        ang = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
        if not 35 <= ang <= 145:
            return False
    # четырёхугольник по самой рамке кадра — это не лист
    m = 0.01 * max(img_w, img_h)
    on_border = sum(
        1 for x, y in quad if x < m or y < m or x > img_w - 1 - m or y > img_h - 1 - m
    )
    return on_border < 4


def _gradients(gray: np.ndarray, sigma: float = 1.2) -> tuple[np.ndarray, np.ndarray]:
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), sigma)
    return cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)


def _line_intersection(l1, l2):
    (p, r), (q, s) = l1, l2
    cross = r[0] * s[1] - r[1] * s[0]
    if abs(cross) < 1e-9:
        return None
    t = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / cross
    return p + t * r


def _fit_sides(gx: np.ndarray, gy: np.ndarray, quad: np.ndarray, r: float,
               min_grad: float = 10.0) -> np.ndarray:
    """
    Подгоняем каждую сторону к настоящему краю: вдоль стороны ищем максимум
    градиента поперёк неё (в полосе ±r), через найденные точки проводим прямую,
    углы — пересечения соседних прямых.
    """
    q = quad.astype(np.float64)
    offs = np.arange(-r, r + 0.5, 0.5 if r < 30 else 1.0)
    ts = np.linspace(0.1, 0.9, 90)
    lines = []
    for i in range(4):
        p0, p1 = q[i], q[(i + 1) % 4]
        d = p1 - p0
        d /= np.linalg.norm(d) + 1e-9
        n = np.array([-d[1], d[0]])
        P = p0 + ts[:, None] * (p1 - p0)
        Q = P[:, None, :] + offs[None, :, None] * n
        mx, my = Q[..., 0].astype(np.float32), Q[..., 1].astype(np.float32)
        gxs = cv2.remap(gx, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        gys = cv2.remap(gy, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        mag = np.abs(gxs * n[0] + gys * n[1])
        best = mag.argmax(axis=1)
        peak = mag[np.arange(len(ts)), best]
        valid = peak > max(min_grad, 0.4 * float(np.median(peak)))
        if valid.sum() < 0.35 * len(ts):
            lines.append((p0, d))
            continue
        pts = Q[np.arange(len(ts)), best][valid].astype(np.float32)
        vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0], dtype=np.float64), np.array([vx, vy], dtype=np.float64)))

    out = []
    for i in range(4):
        p = _line_intersection(lines[i - 1], lines[i])
        if p is None or not np.all(np.isfinite(p)) or np.linalg.norm(p - q[i]) > 2.5 * r:
            p = q[i]
        out.append(p)
    return order_corners(np.array(out))


def _candidate_contours(bm: np.ndarray, mode: int, img_area: float, min_area: float):
    """Крупные контуры + объединения пар (если край листа «порван» тенью или бликом)."""
    contours, _ = cv2.findContours(bm, mode, cv2.CHAIN_APPROX_SIMPLE)
    items = []
    for c in contours:
        if len(c) < 4:
            continue
        a = cv2.contourArea(cv2.convexHull(c))
        if a >= 0.004 * img_area:
            items.append((a, c))
    items.sort(key=lambda x: x[0], reverse=True)
    top = items[:8]
    for a, c in top:
        if a >= min_area * img_area:
            yield c
    for i in range(min(6, len(top))):
        for j in range(i + 1, min(6, len(top))):
            yield np.vstack([top[i][1], top[j][1]])


def _hough_quads(edges: np.ndarray, min_area: float, limit: int = 600):
    """
    Кандидаты из прямых линий: помогает, когда край листа «сливается» с
    линиями фона (плитка, край ноутбука, другой лист) и контур рвётся.
    """
    h, w = edges.shape
    diag = math.hypot(h, w)
    segs = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=max(20, int(0.08 * min(h, w))),
                           minLineLength=0.12 * min(h, w), maxLineGap=0.02 * diag)
    if segs is None:
        return
    raw = []
    # OpenCV 4 возвращает массив (N, 1, 4), OpenCV 5 — (N, 4); приводим к одному виду
    for x1, y1, x2, y2 in segs.reshape(-1, 4).astype(np.float64):
        th = math.atan2(y2 - y1, x2 - x1) % math.pi
        n = (-math.sin(th), math.cos(th))
        raw.append([th, n[0] * x1 + n[1] * y1, math.hypot(x2 - x1, y2 - y1)])
    raw.sort(key=lambda l: -l[2])
    merged: list[list[float]] = []
    for th, rho, ln in raw:
        for m in merged:
            d = abs(th - m[0])
            flip = d > math.pi / 2
            d = min(d, math.pi - d)
            if d < math.radians(3) and abs((-rho if flip else rho) - m[1]) < 0.015 * diag:
                m[2] += ln
                break
        else:
            merged.append([th, rho, ln])
    merged.sort(key=lambda l: -l[2])
    lines = merged[:14]

    def normal(l):
        return np.array([-math.sin(l[0]), math.cos(l[0])]), l[1]

    def inter(l1, l2):
        n1, r1 = normal(l1)
        n2, r2 = normal(l2)
        A = np.array([n1, n2])
        if abs(np.linalg.det(A)) < 1e-6:
            return None
        return np.linalg.solve(A, np.array([r1, r2]))

    def angdiff(a, b):
        d = abs(a - b) % math.pi
        return min(d, math.pi - d)

    pairs = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            if angdiff(lines[i][0], lines[j][0]) < math.radians(30):
                pairs.append((i, j))
    tol = 0.01 * max(h, w)
    count = 0
    for pi_, (a, b) in enumerate(pairs):
        for c, d in pairs[pi_ + 1:]:
            if len({a, b, c, d}) < 4:
                continue
            if angdiff(lines[a][0], lines[c][0]) < math.radians(50):
                continue
            pts = [inter(lines[a], lines[c]), inter(lines[c], lines[b]),
                   inter(lines[b], lines[d]), inter(lines[d], lines[a])]
            if any(p is None for p in pts):
                continue
            q = np.array(pts, dtype=np.float32)
            if (q[:, 0] < -tol).any() or (q[:, 1] < -tol).any() or (q[:, 0] > w - 1 + tol).any() or (q[:, 1] > h - 1 + tol).any():
                continue
            q = order_corners(q)
            if cv2.contourArea(q) < min_area * h * w:
                continue
            count += 1
            yield q
            if count >= limit:
                return


def _detect_quad(small: np.ndarray, min_area: float, card: bool = False) -> np.ndarray | None:
    h, w = small.shape[:2]
    img_area = float(h * w)
    gray = _gray(small)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)

    # «Стираем» текст: морфологическое закрытие заливает тёмные буквы фоном листа
    k = _odd(min(h, w) / 45)
    kern = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    closed = cv2.GaussianBlur(cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kern), (5, 5), 0)

    med = float(np.median(closed))
    maps = [
        _canny(closed, 0.66 * med, 1.33 * med),
        _canny(closed, 12, 36),
    ]
    for ch in (1, 2):  # цветовые каналы: белый лист на цветной скатерти и т.п.
        c = cv2.GaussianBlur(cv2.morphologyEx(lab[:, :, ch], cv2.MORPH_CLOSE, kern), (5, 5), 0)
        maps.append(_canny(c, 8, 24))
    all_edges = np.zeros_like(gray)
    for m in maps:
        all_edges |= m

    binmaps = [(m, cv2.RETR_LIST) for m in maps]
    binmaps.append((cv2.morphologyEx(all_edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)), cv2.RETR_LIST))
    _, otsu = cv2.threshold(closed, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    binmaps.append((otsu, cv2.RETR_EXTERNAL))

    gx, gy = _gradients(closed, 1.0)
    # градиент по цвету (канал b: жёлтый↔синий) — для белого листа на цветном фоне
    gxc, gyc = _gradients(cv2.morphologyEx(lab[:, :, 2], cv2.MORPH_CLOSE, kern), 1.5)
    r_fit = max(3.0, 0.015 * math.hypot(h, w))

    def contour_candidates():
        for bm, mode in binmaps:
            for cnt in _candidate_contours(bm, mode, img_area, min_area):
                if cv2.contourArea(cv2.convexHull(cnt)) < min_area * img_area:
                    continue
                quad = _contour_quad(cnt)
                if quad is not None:
                    yield quad

    bright_ref = float(np.percentile(closed, 99)) + 1.0
    best, best_score, best_sup = None, 0.0, 0.0
    seen: list[np.ndarray] = []

    def consider(quad: np.ndarray) -> None:
        nonlocal best, best_score, best_sup
        if any(np.abs(quad - s).max() < 3 for s in seen):
            return
        seen.append(quad)
        if not _quad_ok(quad, w, h):
            return
        # у карт скруглённые углы, у листа бывают загнутые — подгоняем стороны к краям
        fitted = _fit_sides(gx, gy, quad, r_fit, min_grad=6.0)
        if _quad_ok(fitted, w, h):
            quad = fitted
        area = cv2.contourArea(quad) / img_area
        if area < min_area:
            return
        if card:  # у карты известные пропорции — отсекаем всё непохожее
            r = estimate_ratio(quad, w, h)
            if min(abs(r / ID1_RATIO - 1), abs(r * ID1_RATIO - 1)) > 0.15:
                return
        sup = np.maximum(_side_support(gx, gy, quad), _side_support(gxc, gyc, quad, min_grad=4.0))
        if sup.min() < 0.3 or sup.mean() < 0.55:
            return
        score = area * sup.mean() ** 4  # чёткость краёв важнее площади
        if not card:  # бумага обычно светлая — лёгкий приоритет светлым областям
            mask = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(mask, np.rint(quad).astype(np.int32), 1)
            inner = float(np.median(closed[mask > 0])) / bright_ref
            score *= 0.4 + 0.6 * min(1.0, inner)
        if score > best_score:
            best, best_score, best_sup = quad, score, float(sup.mean())

    for q in contour_candidates():
        consider(q)
    if best is None or best_sup < 0.8:
        # контуры не дали уверенного результата — пробуем собрать лист из прямых
        for q in _hough_quads(maps[0] | maps[1], min_area):
            consider(q)
    return best


def _refine_quad(img: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Точная подгонка углов на большем разрешении."""
    h, w = img.shape[:2]
    small = resize_max(img, REFINE_SIDE)
    s = small.shape[1] / w
    gx, gy = _gradients(_gray(small), 1.2)
    r = max(4.0, 0.008 * math.hypot(*small.shape[:2]))
    return _fit_sides(gx, gy, quad * s, r) / s


def find_document(img: np.ndarray, *, card: bool = False) -> np.ndarray | None:
    """Ищет лист (или карту при card=True). Возвращает 4 угла в координатах img или None."""
    h, w = img.shape[:2]
    small = resize_max(img, DETECT_SIDE)
    s = small.shape[1] / w
    try:
        quad = _detect_quad(small, min_area=0.05 if card else 0.1, card=card)
    except Exception:  # сбой поиска не должен ломать обработку: вернём фото целиком
        log.exception("Ошибка при поиске границ листа")
        return None
    if quad is None:
        return None
    quad = quad / s
    try:
        quad = _refine_quad(img, quad)
    except Exception:
        pass
    quad[:, 0] = np.clip(quad[:, 0], 0, w - 1)
    quad[:, 1] = np.clip(quad[:, 1], 0, h - 1)
    return quad.astype(np.float32)


# ───────────────────────── выравнивание перспективы ──────────────────────

def estimate_ratio(quad: np.ndarray, img_w: int, img_h: int) -> float:
    """
    Настоящее соотношение сторон (ширина/высота) прямоугольника по его
    перспективной проекции (метод Zhang & He, 2004). Если геометрия
    вырожденная — берём фокус типичной камеры телефона.
    """
    tl, tr, br, bl = quad.astype(np.float64)
    naive = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / (
        np.linalg.norm(bl - tl) + np.linalg.norm(br - tr) + 1e-9
    )
    sc = float(max(img_w, img_h))
    u0, v0 = img_w / 2 / sc, img_h / 2 / sc
    m1, m2, m3, m4 = (np.array([p[0] / sc, p[1] / sc, 1.0]) for p in (tl, tr, bl, br))
    try:
        k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
        k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    except FloatingPointError:
        return naive
    if not (np.isfinite(k2) and np.isfinite(k3)):
        return naive
    n2 = k2 * m2 - m1
    n3 = k3 * m3 - m1
    n21, n22, n23 = n2
    n31, n32, n33 = n3

    def ratio_for(f: float) -> float:
        A = np.array([[f, 0, u0], [0, f, v0], [0, 0, 1.0]])
        Ai = np.linalg.inv(A)
        B = Ai.T @ Ai
        return math.sqrt(float(n2 @ B @ n2) / float(n3 @ B @ n3))

    f_typ = 0.75  # фокус типичной основной камеры телефона ≈ 0.75 × длинная сторона
    if abs(n23) > 1e-4 and abs(n33) > 1e-4:
        f2 = -(
            (n21 * n31 - (n21 * n33 + n23 * n31) * u0 + n23 * n33 * u0 * u0)
            + (n22 * n32 - (n22 * n33 + n23 * n32) * v0 + n23 * n33 * v0 * v0)
        ) / (n23 * n33)
        f = math.sqrt(f2) if f2 > 0 else 0.0
        if not 0.45 <= f <= 2.5:  # неправдоподобный фокус — оценка неустойчива
            f = f_typ
        r = ratio_for(f)
    elif abs(n23) > 1e-4 or abs(n33) > 1e-4:
        r = ratio_for(f_typ)
    else:  # проекция без перспективы (аффинная)
        r = math.sqrt((n21 ** 2 + n22 ** 2) / (n31 ** 2 + n32 ** 2))
    if not np.isfinite(r) or not 0.5 * naive <= r <= 2.0 * naive:
        return naive
    return r


def _snap_a4(ratio: float, tol: float = 0.05) -> float:
    for target in (1 / A4_RATIO, A4_RATIO):
        if abs(ratio / target - 1) < tol:
            return target
    return ratio


def warp(img: np.ndarray, quad: np.ndarray, ratio: float | None = None,
         max_side: int = MAX_OUT_SIDE) -> np.ndarray:
    """Вырезает четырёхугольник и «распрямляет» его в прямоугольник."""
    quad = order_corners(quad)
    tl, tr, br, bl = quad
    w_px = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    h_px = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    if ratio is None:
        ratio = _snap_a4(estimate_ratio(quad, img.shape[1], img.shape[0]))
    if h_px * ratio >= w_px:
        H, W = h_px, h_px * ratio
    else:
        W, H = w_px, w_px / ratio
    s = min(1.0, max_side / max(W, H))
    W, H = max(8, int(round(W * s))), max(8, int(round(H * s)))
    m = 0.004 * max(W, H)  # чуть «заходим» внутрь, чтобы по краям не осталось стола
    dst = np.array([[-m, -m], [W - 1 + m, -m], [W - 1 + m, H - 1 + m], [-m, H - 1 + m]], np.float32)
    M = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    return cv2.warpPerspective(img, M, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def warp_card(img: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Карта ID-1: всегда приводим к точным пропорциям 85.6×54 мм, горизонтально."""
    quad = order_corners(quad)
    tl, tr, br, bl = quad
    horiz = np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)
    vert = np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)
    if horiz >= vert:
        return warp(img, quad, ratio=ID1_RATIO, max_side=1600)
    return rotate(warp(img, quad, ratio=1 / ID1_RATIO, max_side=1600), 1)


# ──────────────────────────────── фильтры ────────────────────────────────

def _background(ch: np.ndarray) -> np.ndarray:
    """
    Оценка «чистой бумаги» (освещённости) для одного канала uint8.
    Закрытие убирает текст (тёмное мельче ядра) и сохраняет крупные тени;
    грубая оценка снизу не даёт фону «провалиться» внутрь фотографий на листе.
    """
    h, w = ch.shape
    small = resize_max(ch, 900)
    sh, sw = small.shape
    k = _odd(max(sh, sw) / 40)
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.medianBlur(bg, _odd(k * 1.5))

    tiny = resize_max(small, 225)
    K = _odd(max(tiny.shape) / 5)
    big = cv2.morphologyEx(tiny, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (K, K)))
    big = cv2.GaussianBlur(big, (0, 0), K / 3)
    big = cv2.resize(big, (sw, sh), interpolation=cv2.INTER_LINEAR)

    bg = np.maximum(bg.astype(np.float32), 0.55 * big.astype(np.float32))
    bg = cv2.GaussianBlur(bg, (0, 0), k / 4)
    return cv2.resize(bg, (w, h), interpolation=cv2.INTER_CUBIC)


def _normalize(ch: np.ndarray) -> np.ndarray:
    """Делим на фон: тени и неравномерный свет исчезают, бумага ≈ 1.0 (float32)."""
    bg = _background(ch)
    return ch.astype(np.float32) / np.maximum(bg, 1.0)


def _unsharp(x: np.ndarray, sigma: float, amount: float) -> np.ndarray:
    return x + amount * (x - cv2.GaussianBlur(x, (0, 0), sigma))


def _levels(x: np.ndarray, lo: float, hi: float, gamma: float = 1.0) -> np.ndarray:
    y = np.clip((x - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    if gamma != 1.0:
        y = y ** gamma
    return y


def _to_u8(x: np.ndarray) -> np.ndarray:
    return np.clip(x * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _sharp_sigma(img: np.ndarray) -> float:
    return max(0.8, max(img.shape[:2]) / 2000)


def filter_bw(img: np.ndarray) -> np.ndarray:
    """«Ксерокс»: белый фон, чёрный текст, без теней."""
    norm = _normalize(_gray(img))
    norm = _unsharp(norm, _sharp_sigma(img), 0.8)
    return _to_u8(_levels(norm, 0.42, 0.86, 1.25))


def filter_gray(img: np.ndarray) -> np.ndarray:
    norm = _normalize(_gray(img))
    norm = _unsharp(norm, _sharp_sigma(img), 0.5)
    return _to_u8(_levels(norm, 0.12, 0.94, 1.1))


def filter_color(img: np.ndarray) -> np.ndarray:
    """«Магический цвет»: белая бумага, без теней и желтизны, цвета сочнее."""
    chans = [_normalize(img[:, :, i]) for i in range(3)]
    out = np.dstack([_levels(_unsharp(c, _sharp_sigma(img), 0.5), 0.1, 0.93, 1.1) for c in chans])
    out = _to_u8(out)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.3, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


# — для карт (удостоверение): фон карты цветной, а фото на ней крупное, —
# — поэтому тени убираем «широким» ядром, которое не съедает фотографию  —

def _card_background(gray: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    small = resize_max(gray, 300)
    k = _odd(0.5 * min(small.shape))
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg.astype(np.float32), (0, 0), k / 4)
    return cv2.resize(bg, (w, h), interpolation=cv2.INTER_CUBIC)


def _pct_levels(x: np.ndarray, p_lo: float, p_hi: float) -> tuple[float, float]:
    lo, hi = np.percentile(x, (p_lo, p_hi))
    if hi - lo < 0.04 * (x.max() + 1e-6):
        lo, hi = float(x.min()), float(x.max()) + 1e-6
    return float(lo), float(hi)


def filter_card(img: np.ndarray, name: str) -> np.ndarray:
    if name == "orig":
        return img
    gray = _gray(img)
    bg = np.maximum(_card_background(gray), 1.0)
    if name == "color":
        # выравниваем освещение одинаково для всех каналов — цвета карты сохраняются
        gain = float(np.percentile(bg, 90)) / bg
        f = img.astype(np.float32) * gain[..., None]
        out = np.empty_like(f)
        for i in range(3):  # баланс белого + автоконтраст по каждому каналу
            lo, hi = _pct_levels(f[:, :, i], 0.5, 99.7)
            out[:, :, i] = _levels(f[:, :, i], lo, hi)
        out = _to_u8(_unsharp(out, 1.0, 0.4))
        hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.15, 0, 255)
        return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    norm = gray.astype(np.float32) / bg
    if name == "gray":
        lo, hi = _pct_levels(norm, 0.5, 99.5)
        return _to_u8(_levels(_unsharp(norm, 1.0, 0.5), lo, hi))
    # bw: светлый защитный фон карты уходит в белое, текст и фото — контрастные
    lo, _ = _pct_levels(norm, 1, 99)
    hi = max(float(np.percentile(norm, 60)), lo + 0.15)
    return _to_u8(_levels(_unsharp(norm, 1.0, 0.7), lo, hi, 1.2))


def apply_filter(img: np.ndarray, name: str, *, card: bool = False) -> np.ndarray:
    if card:
        return filter_card(img, name)
    if name == "bw":
        return filter_bw(img)
    if name == "gray":
        return filter_gray(img)
    if name == "color":
        return filter_color(img)
    return img
