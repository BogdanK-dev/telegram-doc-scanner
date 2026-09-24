"""Документы и страницы пользователей (хранятся в памяти) + их отрисовка."""
from __future__ import annotations

import asyncio
import copy
import itertools
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

import pdfmaker as pm
import scanner as sc
import texts

MAX_PAGES = 60            # страниц в одном документе
KEEP_DOCS = 6             # сколько последних документов помнить на пользователя
DOC_TTL = 24 * 3600       # через сколько забываем документ (сек)
STALE_AFTER = 3 * 3600    # после такой паузы новое фото начинает новый документ

_ids = itertools.count(int(time.time() * 1000))


def new_doc_id() -> int:
    # уникален и между перезапусками бота (кнопки старых сообщений не перепутаются)
    return max(next(_ids), int(time.time() * 1000))


@dataclass
class Side:
    """Одно исходное фото: сам снимок + найденные углы листа/карты."""
    src: bytes
    quad: np.ndarray | None
    rotation: int = 0                                   # четверти оборота по часовой
    _cache_key: tuple | None = field(default=None, repr=False)
    _cache: bytes | None = field(default=None, repr=False)

    @classmethod
    def from_upload(cls, data: bytes, *, card: bool) -> "Side":
        img = sc.decode_image(data)
        quad = sc.find_document(img, card=card)
        return cls(src=sc.encode_jpeg(img, 92), quad=quad)

    def warped(self, *, card: bool, crop: bool) -> np.ndarray:
        key = (card, crop and self.quad is not None)
        if self._cache_key != key or self._cache is None:
            img = sc.decode_jpeg(self.src)
            if key[1]:
                img = sc.warp_card(img, self.quad) if card else sc.warp(img, self.quad)
            elif card:
                img = sc.resize_max(img, 1600)
            else:
                img = sc.resize_max(img, sc.MAX_OUT_SIDE)
            self._cache, self._cache_key = sc.encode_jpeg(img, 95), key
        return sc.decode_jpeg(self._cache)


@dataclass
class Page:
    id: int
    kind: str                                 # "doc" — лист, "id" — удостоверение на A4
    sides: list[Side]
    filter: str
    crop: bool = True                         # обрезать по найденным краям (для листа)
    exact: list[bool] = field(default_factory=list)  # для удостоверения: карта найдена на фото
    msg_id: int | None = None

    @property
    def detected(self) -> bool:
        return self.sides[0].quad is not None

    def render(self, filt: str | None = None) -> pm.PdfPage:
        filt = filt or self.filter
        if self.kind == "id":
            tiles = []
            for side, exact in zip(self.sides, self.exact):
                img = sc.rotate(side.warped(card=True, crop=exact), side.rotation)
                tiles.append((sc.apply_filter(img, filt, card=True), exact))
            return pm.compose_id_sheet(tiles)
        side = self.sides[0]
        img = sc.rotate(side.warped(card=False, crop=self.crop), side.rotation)
        return pm.layout_page(sc.apply_filter(img, filt))

    def preview_jpeg(self) -> bytes:
        img = self.render().image
        if self.kind == "id":  # на превью показываем верхнюю часть листа с картами крупнее
            h = img.shape[0]
            img = img[: int(h * 0.62)]
        return sc.encode_jpeg(sc.resize_max(img, 1600), 85)

    def render_for_ocr(self) -> tuple[pm.PdfPage, pm.PdfPage]:
        """(страница как в PDF, та же страница в сером — для распознавания)."""
        shown = self.render()
        if self.filter in ("bw", "gray"):
            return shown, shown
        return shown, self.render("gray")


@dataclass
class Doc:
    id: int
    pages: list[Page] = field(default_factory=list)
    exported: bool = False
    updated: float = field(default_factory=time.time)
    ocr_busy: bool = False
    _next_page: int = 1

    @property
    def created(self) -> datetime:
        return datetime.fromtimestamp(self.id / 1000)

    def add_page(self, **kw) -> Page:
        page = Page(id=self._next_page, **kw)
        self._next_page += 1
        self.pages.append(page)
        self.touch()
        return page

    def page(self, page_id: int) -> Page | None:
        return next((p for p in self.pages if p.id == page_id), None)

    def number(self, page: Page) -> int:
        return self.pages.index(page) + 1 if page in self.pages else 0

    def remove(self, page: Page) -> None:
        if page in self.pages:
            self.pages.remove(page)
        self.touch()

    def touch(self) -> None:
        self.updated = time.time()

    def title(self, lang: str) -> str:
        only_id = self.pages and all(p.kind == "id" for p in self.pages)
        kind = texts.tr(lang, "file_id" if only_id else "file_scan")
        return f"{kind}_{self.created:%Y-%m-%d_%H-%M}"

    def filename(self, lang: str, suffix: str = "", ext: str = "pdf") -> str:
        return f"{self.title(lang)}{suffix}.{ext}"

    def snapshot(self) -> list[Page]:
        """Копия страниц для долгих операций (распознавание) без блокировки пользователя."""
        out = []
        for p in self.pages:
            q = copy.copy(p)
            q.sides = [copy.copy(s) for s in p.sides]
            q.exact = list(p.exact)
            out.append(q)
        return out

    def build_pdf(self, lang: str) -> bytes:
        return pm.build_pdf([p.render() for p in self.pages], title=self.title(lang))


@dataclass
class UserState:
    lang: str = texts.DEFAULT_LANG
    default_filter: str = "bw"
    id_filter: str = "gray"
    docs: dict[int, Doc] = field(default_factory=dict)
    current: int | None = None
    # пошаговый режим «удостоверение»: None | "front" | "back"
    id_step: str | None = None
    id_sides: list[tuple[Side, bool]] = field(default_factory=list)
    id_pending: Side | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_active: float = field(default_factory=time.time)

    @property
    def current_doc(self) -> Doc | None:
        return self.docs.get(self.current) if self.current is not None else None

    def new_doc(self) -> Doc:
        doc = Doc(id=new_doc_id())
        self.docs[doc.id] = doc
        self.current = doc.id
        self.trim()
        return doc

    def doc_for_new_page(self) -> tuple[Doc, bool]:
        """Куда добавить новую страницу: в текущий документ или в новый."""
        doc = self.current_doc
        if (doc is None or doc.exported or len(doc.pages) >= MAX_PAGES
                or (doc.pages and time.time() - doc.updated > STALE_AFTER)):
            return self.new_doc(), True
        return doc, False

    def start_id_flow(self) -> None:
        self.id_step, self.id_sides, self.id_pending = "front", [], None

    def reset_id_flow(self) -> None:
        self.id_step, self.id_sides, self.id_pending = None, [], None

    def trim(self) -> None:
        now = time.time()
        for doc_id in list(self.docs):
            if doc_id != self.current and now - self.docs[doc_id].updated > DOC_TTL:
                del self.docs[doc_id]
        while len(self.docs) > KEEP_DOCS:
            oldest = min((d for d in self.docs if d != self.current), default=None)
            if oldest is None:
                break
            del self.docs[oldest]
