"""
Telegram-бот для сканирования документов.

Запуск: python bot.py   (на Windows — двойной клик по start.bat)
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import time
import zlib
from logging.handlers import RotatingFileHandler
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter,
                                TelegramUnauthorizedError)
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (BotCommand, BotCommandScopeChat, BufferedInputFile, CallbackQuery,
                           InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton, Message,
                           ReplyKeyboardMarkup, TelegramObject, User)
from aiogram.utils.chat_action import ChatActionSender
from aiogram.utils.keyboard import InlineKeyboardBuilder
from PIL import UnidentifiedImageError

import config
import docs as dm
import pdfmaker as pm
import scanner as sc
import texts
from ocr import Ocr
from texts import tr

log = logging.getLogger("bot")

MAX_DOWNLOAD = 20 * 1024 * 1024  # больше Telegram ботам не отдаёт
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff")
COMMANDS = ("pdf", "new", "id", "language", "help", "myid")


class PageCB(CallbackData, prefix="p"):
    doc: int
    page: int
    act: str


class DocCB(CallbackData, prefix="d"):
    doc: int
    act: str


class IdCB(CallbackData, prefix="i"):
    act: str


class LangCB(CallbackData, prefix="l"):
    lang: str


class App:
    """Общее состояние бота."""

    def __init__(self, settings: config.Settings, ocr: Ocr):
        self.settings = settings
        self.ocr = ocr
        self.users: dict[int, dm.UserState] = {}
        self.prefs = config.load_prefs()
        self.ocr_lock = asyncio.Semaphore(1)

    def saved_lang(self, user_id: int) -> str | None:
        lang = self.prefs.get(str(user_id), {}).get("lang")
        return lang if lang in texts.LANGS else None

    def lang_of(self, user: User) -> str:
        st = self.users.get(user.id)
        if st is not None:
            return st.lang
        return self.saved_lang(user.id) or texts.detect_lang(user.language_code)

    def user(self, u: User) -> dm.UserState:
        st = self.users.get(u.id)
        if st is None:
            st = dm.UserState(lang=self.lang_of(u), default_filter=self.settings.default_filter)
            self.users[u.id] = st
            log.info("Новый пользователь: %s (id %s, язык %s)", user_name(u), u.id, st.lang)
        st.last_active = time.time()
        return st

    def save_lang(self, user_id: int, lang: str) -> None:
        self.prefs.setdefault(str(user_id), {})["lang"] = lang
        try:
            config.save_prefs(self.prefs)
        except OSError as e:
            log.warning("Не удалось сохранить prefs.json: %s", e)


APP: App  # создаётся в main()
router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)


# ─────────────────────────────── помощники ───────────────────────────────

async def tg(call: Callable[[], Awaitable[Any]], tries: int = 4) -> Any:
    """Вызов Telegram API с ожиданием при «flood control»."""
    for _ in range(tries - 1):
        try:
            return await call()
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
    return await call()


def user_name(u) -> str:
    name = " ".join(x for x in (u.first_name, u.last_name) if x)
    return f"{name} (@{u.username})" if u.username else name


def main_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=tr(lang, "btn_pdf")), KeyboardButton(text=tr(lang, "btn_new"))],
                  [KeyboardButton(text=tr(lang, "btn_id")), KeyboardButton(text=tr(lang, "btn_help"))]],
        resize_keyboard=True, is_persistent=True, input_field_placeholder=tr(lang, "placeholder"),
    )


def lang_kb(current: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for lang in texts.LANGS:
        name = texts.LANG_NAMES[lang]
        kb.button(text=f"✓ {name}" if lang == current else name, callback_data=LangCB(lang=lang))
    kb.adjust(len(texts.LANGS))
    return kb.as_markup()


def commands(lang: str) -> list[BotCommand]:
    return [BotCommand(command=c, description=tr(lang, f"cmd_{c}")) for c in COMMANDS]


def info(lang: str, key: str) -> str:
    return texts.with_ocr(tr(lang, key), APP.ocr.cmd is not None)


def page_caption(doc: dm.Doc, page: dm.Page, lang: str) -> str:
    n = doc.number(page)
    name = tr(lang, "f_" + page.filter)
    if page.kind == "id":
        cap = tr(lang, "cap_id", n=n, filter=name)
        if not all(page.exact):
            cap += "\n" + tr(lang, "cap_asis")
        return cap
    cap = tr(lang, "cap_page", n=n, filter=name)
    if not page.detected:
        cap += "\n" + tr(lang, "cap_nodetect")
    elif not page.crop:
        cap += " · " + tr(lang, "cap_nocrop")
    return cap


def page_kb(doc: dm.Doc, page: dm.Page, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    cb = lambda act: PageCB(doc=doc.id, page=page.id, act=act)  # noqa: E731
    for f in sc.FILTERS:
        label = tr(lang, "f_" + f)
        kb.button(text=f"✓ {label}" if page.filter == f else label, callback_data=cb("f" + f))
    second = 0
    if page.kind == "doc":
        kb.button(text=tr(lang, "btn_rotate"), callback_data=cb("rot"))
        second += 1
        if page.detected:
            kb.button(text=tr(lang, "btn_nocrop" if page.crop else "btn_crop"), callback_data=cb("crop"))
            second += 1
    else:
        kb.button(text=tr(lang, "btn_front_rot"), callback_data=cb("flip0"))
        second += 1
        if len(page.sides) > 1:
            kb.button(text=tr(lang, "btn_back_rot"), callback_data=cb("flip1"))
            second += 1
    kb.button(text=tr(lang, "btn_delete"), callback_data=cb("del"))
    second += 1
    kb.button(text=tr(lang, "btn_pdf"), callback_data=cb("pdf"))
    kb.adjust(4, second, 1)
    return kb.as_markup()


def doc_kb(doc: dm.Doc, lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if APP.ocr.ready:
        kb.button(text=tr(lang, "btn_ocr"), callback_data=DocCB(doc=doc.id, act="ocr"))
    kb.button(text=tr(lang, "btn_add"), callback_data=DocCB(doc=doc.id, act="add"))
    kb.adjust(1)
    return kb.as_markup()


def id_kb(lang: str, *, skip: bool = False, asis: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if asis:
        kb.button(text=tr(lang, "btn_asis"), callback_data=IdCB(act="asis"))
    if skip:
        kb.button(text=tr(lang, "btn_skip_back"), callback_data=IdCB(act="skip"))
    kb.button(text=tr(lang, "btn_cancel"), callback_data=IdCB(act="cancel"))
    kb.adjust(1)
    return kb.as_markup()


def is_image_document(doc) -> bool:
    mime = (doc.mime_type or "").lower()
    name = (doc.file_name or "").lower()
    return mime.startswith("image/") or name.endswith(IMAGE_EXT)


# ─────────────────────────────── команды ─────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    first_time = APP.saved_lang(message.from_user.id) is None
    st = APP.user(message.from_user)
    await message.answer(info(st.lang, "start"), reply_markup=main_kb(st.lang))
    if first_time:  # при первом знакомстве сразу предлагаем выбрать язык
        APP.save_lang(message.from_user.id, st.lang)
        await message.answer(tr(st.lang, "lang_prompt"), reply_markup=lang_kb(st.lang))


@router.message(Command("help"))
@router.message(F.text.in_(texts.all_variants("btn_help")))
async def cmd_help(message: Message) -> None:
    st = APP.user(message.from_user)
    await message.answer(info(st.lang, "help"), reply_markup=main_kb(st.lang))


@router.message(Command("language", "lang"))
async def cmd_language(message: Message) -> None:
    st = APP.user(message.from_user)
    await message.answer(tr(st.lang, "lang_prompt"), reply_markup=lang_kb(st.lang))


@router.callback_query(LangCB.filter())
async def on_lang(cb: CallbackQuery, callback_data: LangCB, bot: Bot) -> None:
    lang = callback_data.lang
    if lang not in texts.LANGS:
        await cb.answer()
        return
    st = APP.user(cb.from_user)
    st.lang = lang
    APP.save_lang(cb.from_user.id, lang)
    await cb.answer()
    try:
        await cb.message.edit_reply_markup(reply_markup=lang_kb(lang))
    except (TelegramBadRequest, AttributeError):  # сообщение слишком старое для изменения
        pass
    await bot.send_message(cb.message.chat.id, tr(lang, "lang_set"), reply_markup=main_kb(lang))
    try:  # меню команд этого чата — на выбранном языке
        await bot.set_my_commands(commands(lang), scope=BotCommandScopeChat(chat_id=cb.message.chat.id))
    except TelegramBadRequest:
        pass


@router.message(Command("myid"))
async def cmd_myid(message: Message) -> None:
    st = APP.user(message.from_user)
    await message.answer(tr(st.lang, "myid", id=message.from_user.id))


@router.message(Command("new"))
@router.message(F.text.in_(texts.all_variants("btn_new")))
async def cmd_new(message: Message) -> None:
    st = APP.user(message.from_user)
    async with st.lock:
        st.reset_id_flow()
        st.new_doc()
    await message.answer(tr(st.lang, "new_doc"), reply_markup=main_kb(st.lang))


@router.message(Command("pdf"))
@router.message(F.text.in_(texts.all_variants("btn_pdf")))
async def cmd_pdf(message: Message, bot: Bot) -> None:
    st = APP.user(message.from_user)
    doc = st.current_doc
    if doc is None or not doc.pages:
        await message.answer(tr(st.lang, "empty_doc"), reply_markup=main_kb(st.lang))
        return
    await send_pdf(bot, message.chat.id, st, doc)


@router.message(Command("id"))
@router.message(F.text.in_(texts.all_variants("btn_id")))
async def cmd_id(message: Message) -> None:
    st = APP.user(message.from_user)
    async with st.lock:
        st.start_id_flow()
    await message.answer(tr(st.lang, "id_prompt"), reply_markup=id_kb(st.lang))


# ───────────────────────────── фото и файлы ──────────────────────────────

@router.message(F.photo)
async def on_photo(message: Message, bot: Bot) -> None:
    photo = message.photo[-1]
    await handle_image(message, bot, photo, photo.file_size)


@router.message(F.document)
async def on_document(message: Message, bot: Bot) -> None:
    d = message.document
    if not is_image_document(d):
        st = APP.user(message.from_user)
        await message.answer(tr(st.lang, "not_image"))
        return
    await handle_image(message, bot, d, d.file_size)


@router.message()
async def on_other(message: Message) -> None:
    st = APP.user(message.from_user)
    await message.answer(tr(st.lang, "fallback"), reply_markup=main_kb(st.lang))


async def handle_image(message: Message, bot: Bot, file, size: int | None) -> None:
    st = APP.user(message.from_user)
    if size and size > MAX_DOWNLOAD:
        await message.answer(tr(st.lang, "too_big"))
        return
    async with st.lock:
        async with ChatActionSender.upload_photo(chat_id=message.chat.id, bot=bot):
            buf = io.BytesIO()
            await bot.download(file, destination=buf, timeout=180)
            data = buf.getvalue()
            try:
                if st.id_step:
                    await id_photo(message, bot, st, data)
                else:
                    await add_doc_page(message, st, data)
            except (UnidentifiedImageError, ValueError, OSError) as e:
                log.warning("Не удалось открыть картинку: %s", e)
                await message.answer(tr(st.lang, "bad_image"))


async def add_doc_page(message: Message, st: dm.UserState, data: bytes) -> None:
    side = await asyncio.to_thread(dm.Side.from_upload, data, card=False)
    had_docs = bool(st.docs)
    doc, is_new = st.doc_for_new_page()
    page = doc.add_page(kind="doc", sides=[side], filter=st.default_filter, crop=side.quad is not None)
    preview = await asyncio.to_thread(page.preview_jpeg)
    caption = page_caption(doc, page, st.lang)
    if is_new and had_docs:
        caption = tr(st.lang, "new_doc_prefix") + "\n" + caption
    msg = await tg(lambda: message.answer_photo(BufferedInputFile(preview, "page.jpg"), caption=caption,
                                                reply_markup=page_kb(doc, page, st.lang)))
    page.msg_id = msg.message_id


# ─────────────────────────── удостоверение на A4 ─────────────────────────

async def id_photo(message: Message, bot: Bot, st: dm.UserState, data: bytes) -> None:
    side = await asyncio.to_thread(dm.Side.from_upload, data, card=True)
    if side.quad is None:
        st.id_pending = side
        which = tr(st.lang, "side_front" if st.id_step == "front" else "side_back")
        await message.answer(tr(st.lang, "card_not_found", side=which),
                             reply_markup=id_kb(st.lang, asis=True, skip=st.id_step == "back"))
        return
    await id_accept(bot, message.chat.id, st, side, exact=True)


async def id_accept(bot: Bot, chat_id: int, st: dm.UserState, side: dm.Side, exact: bool) -> None:
    st.id_sides.append((side, exact))
    st.id_pending = None
    if st.id_step == "front":
        st.id_step = "back"
        await bot.send_message(chat_id, tr(st.lang, "front_ok"), reply_markup=id_kb(st.lang, skip=True))
    else:
        await id_finish(bot, chat_id, st)


async def id_finish(bot: Bot, chat_id: int, st: dm.UserState) -> None:
    sides = list(st.id_sides)
    st.reset_id_flow()
    had_docs = bool(st.docs)
    doc, is_new = st.doc_for_new_page()
    page = doc.add_page(kind="id", sides=[s for s, _ in sides], exact=[e for _, e in sides],
                        filter=st.id_filter)
    async with ChatActionSender.upload_photo(chat_id=chat_id, bot=bot):
        preview = await asyncio.to_thread(page.preview_jpeg)
    caption = page_caption(doc, page, st.lang)
    if is_new and had_docs:
        caption = tr(st.lang, "new_doc_prefix") + "\n" + caption
    msg = await tg(lambda: bot.send_photo(chat_id, BufferedInputFile(preview, "id.jpg"), caption=caption,
                                          reply_markup=page_kb(doc, page, st.lang)))
    page.msg_id = msg.message_id


@router.callback_query(IdCB.filter())
async def on_id_action(cb: CallbackQuery, callback_data: IdCB, bot: Bot) -> None:
    st = APP.user(cb.from_user)
    chat_id = cb.message.chat.id
    act = callback_data.act
    async with st.lock:
        if act == "cancel":
            st.reset_id_flow()
            await cb.answer(tr(st.lang, "cancelled"))
            await bot.send_message(chat_id, tr(st.lang, "id_cancelled"), reply_markup=main_kb(st.lang))
            return
        if not st.id_step:
            await cb.answer(tr(st.lang, "id_finished"), show_alert=True)
            return
        if act == "asis":
            if st.id_pending is None:
                await cb.answer(tr(st.lang, "resend_photo"), show_alert=True)
                return
            await cb.answer()
            await id_accept(bot, chat_id, st, st.id_pending, exact=False)
        elif act == "skip":
            if not st.id_sides:
                await cb.answer(tr(st.lang, "front_first"), show_alert=True)
                return
            await cb.answer()
            await id_finish(bot, chat_id, st)


# ───────────────────────────── кнопки страниц ────────────────────────────

@router.callback_query(PageCB.filter())
async def on_page_action(cb: CallbackQuery, callback_data: PageCB, bot: Bot) -> None:
    st = APP.user(cb.from_user)
    doc = st.docs.get(callback_data.doc)
    page = doc.page(callback_data.page) if doc else None
    if page is None:
        await cb.answer(tr(st.lang, "page_gone"), show_alert=True)
        return
    chat_id = cb.message.chat.id
    act = callback_data.act
    if act == "pdf":
        await cb.answer()
        await send_pdf(bot, chat_id, st, doc)
        return
    async with st.lock:
        if page not in doc.pages:  # пока ждали очередь, страницу удалили
            await cb.answer(tr(st.lang, "page_already_deleted"))
            return
        if act.startswith("f") and act[1:] in sc.FILTERS:
            if page.filter == act[1:]:
                await cb.answer()
                return
            page.filter = act[1:]
            if page.kind == "doc":
                st.default_filter = page.filter
            else:
                st.id_filter = page.filter
        elif act == "rot":
            page.sides[0].rotation = (page.sides[0].rotation + 1) % 4
        elif act == "crop":
            page.crop = not page.crop
        elif act in ("flip0", "flip1") and int(act[-1]) < len(page.sides):
            i = int(act[-1])
            page.sides[i].rotation = (page.sides[i].rotation + (2 if page.exact[i] else 1)) % 4
        elif act == "del":
            doc.remove(page)
            await cb.answer(tr(st.lang, "page_deleted"))
            try:
                await bot.delete_message(chat_id, cb.message.message_id)
            except TelegramBadRequest:
                try:
                    await bot.edit_message_caption(chat_id=chat_id, message_id=cb.message.message_id,
                                                   caption=tr(st.lang, "page_deleted"), reply_markup=None)
                except TelegramBadRequest:
                    pass
            return
        else:
            await cb.answer()
            return
        doc.touch()
        await cb.answer()
        lang = st.lang
        async with ChatActionSender.upload_photo(chat_id=chat_id, bot=bot):
            preview = await asyncio.to_thread(page.preview_jpeg)
            media = InputMediaPhoto(media=BufferedInputFile(preview, "page.jpg"),
                                    caption=page_caption(doc, page, lang))
            try:
                await tg(lambda: bot.edit_message_media(chat_id=chat_id, message_id=cb.message.message_id,
                                                        media=media, reply_markup=page_kb(doc, page, lang)))
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                # старое сообщение нельзя изменить — присылаем страницу заново
                msg = await tg(lambda: bot.send_photo(chat_id, BufferedInputFile(preview, "page.jpg"),
                                                      caption=page_caption(doc, page, lang),
                                                      reply_markup=page_kb(doc, page, lang)))
                page.msg_id = msg.message_id


# ───────────────────────────────── PDF ───────────────────────────────────

async def send_pdf(bot: Bot, chat_id: int, st: dm.UserState, doc: dm.Doc) -> None:
    async with st.lock:
        lang = st.lang
        if not doc.pages:
            await bot.send_message(chat_id, tr(lang, "doc_no_pages"))
            return
        async with ChatActionSender.upload_document(chat_id=chat_id, bot=bot):
            pdf = await asyncio.to_thread(doc.build_pdf, lang)
            n = len(doc.pages)
            caption = tr(lang, "pdf_caption", pages=texts.pages(lang, n), size=texts.size(lang, len(pdf)))
            if not doc.exported:
                caption += "\n" + tr(lang, "pdf_next_new")
            await tg(lambda: bot.send_document(chat_id, BufferedInputFile(pdf, doc.filename(lang)),
                                               caption=caption, reply_markup=doc_kb(doc, lang)))
        doc.exported = True
        doc.touch()
        log.info("PDF: %s стр., %s (пользователь %s)", n, texts.size("ru", len(pdf)), chat_id)


@router.callback_query(DocCB.filter(F.act == "add"))
async def on_doc_add(cb: CallbackQuery, callback_data: DocCB, bot: Bot) -> None:
    st = APP.user(cb.from_user)
    async with st.lock:
        doc = st.docs.get(callback_data.doc)
        if doc is None:
            await cb.answer(tr(st.lang, "doc_unavailable"), show_alert=True)
            return
        st.current = doc.id
        doc.exported = False
        doc.touch()
    await cb.answer()
    await bot.send_message(cb.message.chat.id,
                           tr(st.lang, "add_pages", pages=texts.pages(st.lang, len(doc.pages)),
                              btn=tr(st.lang, "btn_pdf")),
                           reply_markup=main_kb(st.lang))


@router.callback_query(DocCB.filter(F.act == "ocr"))
async def on_doc_ocr(cb: CallbackQuery, callback_data: DocCB, bot: Bot) -> None:
    st = APP.user(cb.from_user)
    lang = st.lang
    doc = st.docs.get(callback_data.doc)
    if doc is None or not doc.pages:
        await cb.answer(tr(lang, "doc_unavailable_resend"), show_alert=True)
        return
    if not APP.ocr.ready:
        await cb.answer(tr(lang, "ocr_off"), show_alert=True)
        return
    if doc.ocr_busy:
        await cb.answer(tr(lang, "ocr_busy"))
        return
    await cb.answer(tr(lang, "ocr_toast"))
    chat_id = cb.message.chat.id
    async with st.lock:
        pages = doc.snapshot()
        title = doc.title(lang)
    total = len(pages)
    status = await bot.send_message(chat_id, tr(lang, "ocr_progress", i=0, n=total))
    found: list[str] = []
    shown: list[pm.PdfPage] = []
    layers: list[bytes | None] = []
    doc.ocr_busy = True
    try:
        async with APP.ocr_lock:
            async with ChatActionSender.typing(chat_id=chat_id, bot=bot):
                for i, page in enumerate(pages, 1):
                    display, for_ocr = await asyncio.to_thread(page.render_for_ocr)
                    res = await asyncio.to_thread(APP.ocr.recognize, for_ocr.image, for_ocr.dpi)
                    shown.append(display)
                    layers.append(res.pdf_layer)
                    found.append(res.text)
                    if i < total:
                        try:
                            await bot.edit_message_text(tr(lang, "ocr_progress", i=i, n=total),
                                                        chat_id=chat_id, message_id=status.message_id)
                        except TelegramBadRequest:
                            pass
                pdf = await asyncio.to_thread(lambda: pm.add_text_layers(pm.build_pdf(shown, title=title), layers))
    except Exception:
        log.exception("Ошибка распознавания")
        await bot.edit_message_text(tr(lang, "ocr_failed"), chat_id=chat_id, message_id=status.message_id)
        return
    finally:
        doc.ocr_busy = False
    try:
        await bot.delete_message(chat_id, status.message_id)
    except TelegramBadRequest:
        pass
    suffix = "_" + tr(lang, "file_text")
    await tg(lambda: bot.send_document(chat_id, BufferedInputFile(pdf, f"{title}{suffix}.pdf"),
                                       caption=tr(lang, "ocr_pdf_caption")))
    if len(found) > 1:
        full = "\n\n".join(f"{tr(lang, 'ocr_page_sep', i=i)}\n{t or tr(lang, 'ocr_page_empty')}"
                           for i, t in enumerate(found, 1))
    else:
        full = found[0] if found else ""
    if not any(t.strip() for t in found):
        await bot.send_message(chat_id, tr(lang, "ocr_none"))
    elif len(full) <= 3900:
        await bot.send_message(chat_id, full, parse_mode=None)
    else:
        await tg(lambda: bot.send_document(chat_id, BufferedInputFile(full.encode("utf-8"), f"{title}{suffix}.txt"),
                                           caption=tr(lang, "ocr_file_caption")))


@router.callback_query()
async def on_unknown_callback(cb: CallbackQuery) -> None:
    await cb.answer(tr(APP.lang_of(cb.from_user), "button_stale"))


# ─────────────────────────────── запуск ──────────────────────────────────

class AccessMiddleware(BaseMiddleware):
    """Пускаем только разрешённых пользователей (если список задан в .env)."""

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is not None and not APP.settings.is_allowed(user.id, user.username):
            log.info("Нет доступа: %s (id %s)", user_name(user), user.id)
            lang = APP.lang_of(user)
            if isinstance(event, Message) and event.chat.type == ChatType.PRIVATE:
                await event.answer(tr(lang, "access_denied", id=user.id))
            elif isinstance(event, CallbackQuery):
                await event.answer(tr(lang, "access_denied_toast"), show_alert=True)
            return None
        return await handler(event, data)


class ErrorsMiddleware(BaseMiddleware):
    """Любая ошибка: пишем в лог и коротко сообщаем пользователю."""

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except TelegramForbiddenError:
            return None  # пользователь заблокировал бота
        except Exception:
            log.exception("Ошибка при обработке")
            user = data.get("event_from_user")
            lang = APP.lang_of(user) if user else texts.DEFAULT_LANG
            try:
                if isinstance(event, Message):
                    await event.answer(tr(lang, "error"))
                elif isinstance(event, CallbackQuery):
                    await event.answer(tr(lang, "error"), show_alert=True)
            except Exception:
                pass
            return None


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(1800)
        now = time.time()
        for uid in list(APP.users):
            st = APP.users[uid]
            if st.lock.locked():
                continue
            st.trim()
            if now - st.last_active > dm.DOC_TTL:
                del APP.users[uid]


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)
    fh = RotatingFileHandler(config.BASE_DIR / "bot.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def single_instance(token: str):
    """Не даём запустить второй экземпляр того же бота (Telegram не разрешает два сразу)."""
    path = config.BASE_DIR / f".bot-{zlib.crc32(token.encode()):08x}.lock"
    f = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit("Бот уже запущен в другом окне. Закройте лишнее окно.")
    return f  # блокировка держится, пока файл открыт (снимается и при аварийном выходе)


def disable_quick_edit() -> None:
    """
    В консоли Windows клик мышью по окну включает «выделение» и замораживает
    программу, пока не нажмёшь Enter. Для бота, который работает сутками, это
    ловушка — отключаем этот режим.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


def make_bot(token: str) -> Bot:
    api = os.environ.get("TELEGRAM_API_BASE")  # для своего Bot API сервера (обычно не нужно)
    session = AiohttpSession(api=TelegramAPIServer.from_base(api)) if api else AiohttpSession()
    return Bot(token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


async def main() -> None:
    global APP
    settings = config.load_settings()
    lock = single_instance(settings.token)  # держим до выхода из бота
    ocr = Ocr(settings.ocr_lang, settings.tesseract_cmd)
    APP = App(settings, ocr)
    ocr_task = asyncio.create_task(asyncio.to_thread(ocr.prepare))  # не задерживаем запуск бота

    bot = make_bot(settings.token)
    while True:
        try:
            me = await bot.get_me()
            break
        except TelegramUnauthorizedError:
            await bot.session.close()
            lock.close()
            settings.token = await asyncio.to_thread(
                config.ask_token, "Telegram не принял токен: он неверный или был отозван.")
            lock = single_instance(settings.token)
            bot = make_bot(settings.token)
        except Exception as e:
            log.warning("Нет связи с Telegram (%s). Повтор через 10 секунд…", e)
            await asyncio.sleep(10)
    disable_quick_edit()

    # меню команд: русское по умолчанию, английское — для Telegram на английском
    await bot.set_my_commands(commands("ru"))
    await bot.set_my_commands(commands("en"), language_code="en")
    dp = Dispatcher()
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.message.middleware(ErrorsMiddleware())
    dp.callback_query.middleware(ErrorsMiddleware())
    dp.include_router(router)
    cleaner = asyncio.create_task(cleanup_loop())

    access = "все" if not (settings.allowed_ids or settings.allowed_names) else "только из ALLOWED_USERS"
    log.info("Бот @%s запущен. Откройте в Telegram: https://t.me/%s (доступ: %s)", me.username, me.username, access)
    log.info("Остановка: Ctrl+C или закрытие окна.")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        cleaner.cancel()
        ocr_task.cancel()
        lock.close()


if __name__ == "__main__":
    setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Бот остановлен.")
    except SystemExit as e:
        if e.code not in (None, 0):
            log.error("%s", e.code)
            sys.exit(2)  # ошибка настройки — перезапуск не поможет
