"""Все тексты бота на двух языках: русский (ru) и английский (en)."""
from __future__ import annotations

LANGS = ("ru", "en")
LANG_NAMES = {"ru": "Русский", "en": "English"}
DEFAULT_LANG = "ru"
OCR_MARK = "[ocr]"  # строки, которые показываем только при установленном Tesseract

# языки Telegram, для которых по умолчанию включаем русский интерфейс
RU_DEFAULT = {"ru", "kk", "uk", "be", "ky", "uz", "tg", "tk", "az", "hy", "ka"}


def detect_lang(language_code: str | None) -> str:
    """Язык интерфейса по настройке Telegram у пользователя."""
    if not language_code:
        return DEFAULT_LANG
    code = language_code.lower().split("-")[0]
    return "ru" if code in RU_DEFAULT else "en"


T: dict[str, dict[str, str]] = {
    "ru": {
        # кнопки основной клавиатуры
        "btn_pdf": "Сформировать PDF",
        "btn_new": "Новый документ",
        "btn_id": "Удостоверение на A4",
        "btn_help": "Справка",
        "placeholder": "Отправьте фотографию документа",
        # кнопки страниц
        "f_bw": "Ч/Б",
        "f_gray": "Серый",
        "f_color": "Цвет",
        "f_orig": "Оригинал",
        "btn_rotate": "Повернуть",
        "btn_nocrop": "Без обрезки",
        "btn_crop": "Обрезать",
        "btn_front_rot": "Лицевая ↻",
        "btn_back_rot": "Оборотная ↻",
        "btn_delete": "Удалить",
        "btn_ocr": "Распознать текст",
        "btn_add": "Добавить страницы",
        "btn_asis": "Использовать это фото",
        "btn_skip_back": "Без оборотной стороны",
        "btn_cancel": "Отмена",
        # сообщения
        "start": (
            "Отправьте фотографию документа. Бот определит границы листа, выровняет изображение "
            "и удалит тени. Фотографии, отправленные подряд или альбомом, объединяются в один документ.\n"
            "\n"
            "• Под каждой страницей: фильтр, поворот, обрезка, удаление.\n"
            "• «Сформировать PDF» — все страницы одним файлом.\n"
            "[ocr]• Под готовым PDF: «Распознать текст».\n"
            "• «Удостоверение на A4» — обе стороны удостоверения на одном листе.\n"
            "\n"
            "Справка: /help · Язык: /language"
        ),
        "help": (
            "<b>Справка</b>\n"
            "\n"
            "<b>Съёмка.</b> Документ на тёмной однотонной поверхности, все четыре угла в кадре, без бликов. "
            "Наилучшее качество — при отправке фотографии файлом.\n"
            "\n"
            "<b>Фильтры.</b> Ч/Б — копия с белым фоном. Серый — с полутонами. Цвет — очищенный цветной скан. "
            "Оригинал — без обработки. Выбранный фильтр сохраняется для следующих страниц.\n"
            "\n"
            "<b>Обрезка.</b> Если граница листа определена неверно, нажмите «Без обрезки».\n"
            "\n"
            "<b>PDF.</b> «Сформировать PDF» отправляет все страницы одним файлом, после чего следующая "
            "фотография начинает новый документ. Чтобы дополнить отправленный документ, нажмите "
            "«Добавить страницы» под PDF.\n"
            "[ocr]\n"
            "[ocr]<b>Распознавание текста.</b> Кнопка под PDF: бот пришлёт текст и PDF с возможностью поиска.\n"
            "\n"
            "<b>Удостоверение на A4.</b> Отправьте лицевую, затем оборотную сторону. Обе стороны "
            "размещаются на листе A4 в натуральную величину.\n"
            "\n"
            "<b>Команды</b>\n"
            "/pdf — сформировать PDF\n"
            "/new — новый документ\n"
            "/id — удостоверение на A4\n"
            "/language — язык интерфейса\n"
            "/myid — ваш Telegram ID"
        ),
        "myid": "Ваш Telegram ID: <code>{id}</code>",
        "lang_prompt": "Язык интерфейса / Interface language:",
        "lang_set": "Язык интерфейса: русский.",
        "new_doc": "Создан новый документ. Отправьте фотографии страниц.",
        "empty_doc": "Документ пуст. Отправьте фотографию документа.",
        "id_prompt": "<b>Удостоверение на A4</b>\nОтправьте фотографию лицевой стороны. "
                     "Карта — на тёмной поверхности, все четыре угла в кадре.",
        "not_image": "Файл не является изображением. Поддерживаются JPG, PNG, HEIC.",
        "fallback": "Отправьте фотографию документа. Справка: /help",
        "too_big": "Файл превышает 20 МБ: Telegram не передаёт ботам файлы такого размера. "
                   "Отправьте изображение как фото или уменьшите его.",
        "bad_image": "Не удалось открыть изображение. Отправьте другой файл.",
        "new_doc_prefix": "Новый документ",
        "cap_page": "Страница {n} · {filter}",
        "cap_id": "Страница {n} · Удостоверение · {filter}",
        "cap_asis": "Одна из сторон добавлена без выравнивания.",
        "cap_nodetect": "Границы листа не определены, изображение сохранено целиком.",
        "cap_nocrop": "без обрезки",
        "side_front": "лицевой",
        "side_back": "оборотной",
        "card_not_found": "Карта на фотографии {side} стороны не найдена. Переснимите её на тёмной "
                          "поверхности так, чтобы все четыре угла были в кадре, или используйте это "
                          "фото без выравнивания.",
        "front_ok": "Лицевая сторона принята. Отправьте оборотную сторону.",
        "cancelled": "Отменено",
        "id_cancelled": "Режим «Удостоверение на A4» отменён.",
        "id_finished": "Режим «Удостоверение на A4» уже завершён.",
        "resend_photo": "Отправьте фотографию повторно.",
        "front_first": "Сначала отправьте лицевую сторону.",
        "page_gone": "Страница недоступна: бот был перезапущен или документ устарел. "
                     "Отправьте фотографию повторно.",
        "page_already_deleted": "Страница уже удалена.",
        "page_deleted": "Страница удалена.",
        "doc_no_pages": "В документе нет страниц.",
        "pdf_caption": "PDF: {pages}, {size}",
        "pdf_next_new": "Следующая фотография начнёт новый документ.",
        "doc_unavailable": "Документ недоступен.",
        "add_pages": "Отправьте фотографии: они будут добавлены в этот документ (сейчас {pages}). "
                     "Затем нажмите «{btn}».",
        "doc_unavailable_resend": "Документ недоступен. Отправьте фотографии повторно.",
        "ocr_off": "Распознавание текста не настроено: не установлен Tesseract (install_ocr.bat).",
        "ocr_busy": "Распознавание уже выполняется.",
        "ocr_toast": "Распознавание текста…",
        "ocr_progress": "Распознавание текста: {i}/{n}",
        "ocr_failed": "Не удалось распознать текст. Подробности в журнале bot.log.",
        "ocr_pdf_caption": "PDF с текстовым слоем: поиск и копирование текста.",
        "ocr_page_sep": "— Страница {i} —",
        "ocr_page_empty": "(текст не обнаружен)",
        "ocr_none": "Текст не обнаружен.",
        "ocr_file_caption": "Распознанный текст.",
        "button_stale": "Кнопка устарела.",
        "access_denied": "Доступ ограничен. Ваш Telegram ID: <code>{id}</code>.\n"
                         "Для получения доступа обратитесь к владельцу бота.",
        "access_denied_toast": "Доступ ограничен.",
        "error": "Произошла ошибка. Повторите попытку.",
        # имена файлов и единицы
        "file_scan": "Скан",
        "file_id": "Удостоверение",
        "file_text": "текст",
        "kb": "КБ",
        "mb": "МБ",
        # меню команд
        "cmd_pdf": "Сформировать PDF",
        "cmd_new": "Новый документ",
        "cmd_id": "Удостоверение на A4",
        "cmd_language": "Язык интерфейса",
        "cmd_help": "Справка",
        "cmd_myid": "Ваш Telegram ID",
    },
    "en": {
        "btn_pdf": "Create PDF",
        "btn_new": "New document",
        "btn_id": "ID card on A4",
        "btn_help": "Help",
        "placeholder": "Send a photo of a document",
        "f_bw": "B/W",
        "f_gray": "Gray",
        "f_color": "Color",
        "f_orig": "Original",
        "btn_rotate": "Rotate",
        "btn_nocrop": "No crop",
        "btn_crop": "Crop",
        "btn_front_rot": "Front ↻",
        "btn_back_rot": "Back ↻",
        "btn_delete": "Delete",
        "btn_ocr": "Recognize text",
        "btn_add": "Add pages",
        "btn_asis": "Use this photo",
        "btn_skip_back": "Without back side",
        "btn_cancel": "Cancel",
        "start": (
            "Send a photo of a document. The bot detects the page borders, straightens the image "
            "and removes shadows. Photos sent in a row or as an album are combined into one document.\n"
            "\n"
            "• Under each page: filter, rotation, cropping, deletion.\n"
            "• “Create PDF” — all pages in one file.\n"
            "[ocr]• Under the finished PDF: “Recognize text”.\n"
            "• “ID card on A4” — both sides of an ID card on one sheet.\n"
            "\n"
            "Help: /help · Language: /language"
        ),
        "help": (
            "<b>Help</b>\n"
            "\n"
            "<b>Shooting.</b> Place the document on a dark plain surface with all four corners in the "
            "frame and no glare. Best quality: send the photo as a file.\n"
            "\n"
            "<b>Filters.</b> B/W — copy with a white background. Gray — with halftones. Color — cleaned "
            "color scan. Original — no processing. The selected filter is kept for the following pages.\n"
            "\n"
            "<b>Cropping.</b> If the page border is detected incorrectly, press “No crop”.\n"
            "\n"
            "<b>PDF.</b> “Create PDF” sends all pages as one file; the next photo then starts a new "
            "document. To extend a sent document, press “Add pages” under the PDF.\n"
            "[ocr]\n"
            "[ocr]<b>Text recognition.</b> Button under the PDF: the bot sends the text and a searchable PDF.\n"
            "\n"
            "<b>ID card on A4.</b> Send the front side, then the back side. Both sides are placed "
            "on an A4 sheet at actual size.\n"
            "\n"
            "<b>Commands</b>\n"
            "/pdf — create PDF\n"
            "/new — new document\n"
            "/id — ID card on A4\n"
            "/language — interface language\n"
            "/myid — your Telegram ID"
        ),
        "myid": "Your Telegram ID: <code>{id}</code>",
        "lang_prompt": "Язык интерфейса / Interface language:",
        "lang_set": "Interface language: English.",
        "new_doc": "New document created. Send photos of the pages.",
        "empty_doc": "The document is empty. Send a photo of a document.",
        "id_prompt": "<b>ID card on A4</b>\nSend a photo of the front side. "
                     "Place the card on a dark surface with all four corners in the frame.",
        "not_image": "The file is not an image. Supported formats: JPG, PNG, HEIC.",
        "fallback": "Send a photo of a document. Help: /help",
        "too_big": "The file exceeds 20 MB: Telegram does not deliver files of this size to bots. "
                   "Send the image as a photo or reduce its size.",
        "bad_image": "Could not open the image. Send another file.",
        "new_doc_prefix": "New document",
        "cap_page": "Page {n} · {filter}",
        "cap_id": "Page {n} · ID card · {filter}",
        "cap_asis": "One of the sides was added without alignment.",
        "cap_nodetect": "Page borders not detected; the image is kept in full.",
        "cap_nocrop": "no crop",
        "side_front": "front",
        "side_back": "back",
        "card_not_found": "No card found on the photo of the {side} side. Retake it on a dark surface "
                          "with all four corners in the frame, or use this photo without alignment.",
        "front_ok": "Front side accepted. Send the back side.",
        "cancelled": "Cancelled",
        "id_cancelled": "“ID card on A4” mode cancelled.",
        "id_finished": "“ID card on A4” mode has already finished.",
        "resend_photo": "Send the photo again.",
        "front_first": "Send the front side first.",
        "page_gone": "The page is unavailable: the bot was restarted or the document has expired. "
                     "Send the photo again.",
        "page_already_deleted": "The page has already been deleted.",
        "page_deleted": "Page deleted.",
        "doc_no_pages": "The document has no pages.",
        "pdf_caption": "PDF: {pages}, {size}",
        "pdf_next_new": "The next photo will start a new document.",
        "doc_unavailable": "The document is unavailable.",
        "add_pages": "Send photos: they will be added to this document (currently {pages}). "
                     "Then press “{btn}”.",
        "doc_unavailable_resend": "The document is unavailable. Send the photos again.",
        "ocr_off": "Text recognition is not configured: Tesseract is not installed (install_ocr.bat).",
        "ocr_busy": "Recognition is already in progress.",
        "ocr_toast": "Recognizing text…",
        "ocr_progress": "Recognizing text: {i}/{n}",
        "ocr_failed": "Text recognition failed. See bot.log for details.",
        "ocr_pdf_caption": "PDF with a text layer: search and copy text.",
        "ocr_page_sep": "— Page {i} —",
        "ocr_page_empty": "(no text found)",
        "ocr_none": "No text found.",
        "ocr_file_caption": "Recognized text.",
        "button_stale": "This button is no longer valid.",
        "access_denied": "Access restricted. Your Telegram ID: <code>{id}</code>.\n"
                         "Contact the bot owner to request access.",
        "access_denied_toast": "Access restricted.",
        "error": "An error occurred. Please try again.",
        "file_scan": "Scan",
        "file_id": "ID_card",
        "file_text": "text",
        "kb": "KB",
        "mb": "MB",
        "cmd_pdf": "Create PDF",
        "cmd_new": "New document",
        "cmd_id": "ID card on A4",
        "cmd_language": "Interface language",
        "cmd_help": "Help",
        "cmd_myid": "Your Telegram ID",
    },
}


def tr(lang: str, key: str, **kw) -> str:
    text = T.get(lang, T[DEFAULT_LANG]).get(key) or T[DEFAULT_LANG][key]
    return text.format(**kw) if kw else text


def pages(lang: str, n: int) -> str:
    if lang == "en":
        return f"{n} page" if n == 1 else f"{n} pages"
    return f"{n} стр."


def size(lang: str, n: int) -> str:
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} {tr(lang, 'kb')}"
    return f"{n / 1024 / 1024:.1f} {tr(lang, 'mb')}"


def with_ocr(text: str, ocr_available: bool) -> str:
    """Убирает строки про распознавание, если Tesseract не установлен."""
    out = []
    for line in text.split("\n"):
        if line.startswith(OCR_MARK):
            if not ocr_available:
                continue
            line = line[len(OCR_MARK):]
        out.append(line)
    return "\n".join(out)


def all_variants(key: str) -> set[str]:
    """Текст кнопки на всех языках — чтобы кнопки работали при любом выбранном языке."""
    return {T[lang][key] for lang in LANGS}


# проверка при импорте: у обоих языков одинаковый набор ключей
assert set(T["ru"]) == set(T["en"]), set(T["ru"]) ^ set(T["en"])
