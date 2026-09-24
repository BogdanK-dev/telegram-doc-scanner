"""Настройки бота: файл .env рядом с ботом (создаётся при первом запуске)."""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
PREFS_FILE = BASE_DIR / "prefs.json"  # выбранный язык интерфейса пользователей
TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,50}$")

ENV_TEMPLATE = """\
# Настройки бота. После изменения перезапустите бота.

# Токен от @BotFather
BOT_TOKEN={token}

# Пользователи с доступом: Telegram ID или @username через запятую.
# Пустое значение: доступ открыт всем. ID можно узнать командой /myid
ALLOWED_USERS=

# Языки распознавания текста через «+»: rus, kaz, eng, ukr, deu ...
OCR_LANG=rus+kaz+eng

# Путь к tesseract.exe, если он не найден автоматически
TESSERACT_CMD=

# Фильтр для новых страниц: bw (ч/б), gray, color, orig
DEFAULT_FILTER=bw
"""


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key.strip()] = val
    return values


def save_token(token: str, path: Path = ENV_FILE) -> None:
    if path.is_file():
        text = path.read_text("utf-8-sig")
        if re.search(r"^\s*BOT_TOKEN\s*=.*$", text, re.M):
            text = re.sub(r"^\s*BOT_TOKEN\s*=.*$", f"BOT_TOKEN={token}", text, count=1, flags=re.M)
        else:
            text = f"BOT_TOKEN={token}\n" + text
    else:
        text = ENV_TEMPLATE.format(token=token)
    path.write_text(text, "utf-8")


def ask_token(reason: str = "") -> str:
    """Спрашиваем токен в консоли (первый запуск или неверный токен)."""
    if not sys.stdin or not sys.stdin.isatty():
        raise SystemExit("Не задан BOT_TOKEN. Впишите токен от @BotFather в файл .env рядом с ботом.")
    if reason:
        print(f"\n{reason}")
    print("\nТребуется токен бота:\n"
          "  1) в Telegram откройте @BotFather и отправьте /newbot;\n"
          "  2) задайте имя и username бота (username должен оканчиваться на bot);\n"
          "  3) BotFather пришлёт токен вида 1234567890:AAH...\n")
    while True:
        token = input("Вставьте токен и нажмите Enter: ").strip().strip('"').strip("'")
        if TOKEN_RE.match(token):
            save_token(token)
            print("Токен сохранён в файле .env\n")
            return token
        print("Неверный формат токена. Пример: 1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw\n")


@dataclass
class Settings:
    token: str
    allowed_ids: set[int] = field(default_factory=set)
    allowed_names: set[str] = field(default_factory=set)
    ocr_lang: str = "rus+kaz+eng"
    tesseract_cmd: str | None = None
    default_filter: str = "bw"

    def is_allowed(self, user_id: int, username: str | None) -> bool:
        if not self.allowed_ids and not self.allowed_names:
            return True
        return user_id in self.allowed_ids or (username or "").lower() in self.allowed_names


def load_settings() -> Settings:
    env = _parse_env(ENV_FILE)
    get = lambda k, d="": os.environ.get(k) or env.get(k) or d  # noqa: E731
    token = get("BOT_TOKEN").strip()
    if not TOKEN_RE.match(token):
        token = ask_token("Токен не задан." if not token else "Токен в файле .env имеет неверный формат.")
    ids, names = set(), set()
    for item in re.split(r"[,;\s]+", get("ALLOWED_USERS")):
        item = item.strip()
        if not item:
            continue
        if item.lstrip("-").isdigit():
            ids.add(int(item))
        else:
            names.add(item.lstrip("@").lower())
    flt = get("DEFAULT_FILTER", "bw").strip().lower()
    return Settings(
        token=token,
        allowed_ids=ids,
        allowed_names=names,
        ocr_lang=get("OCR_LANG", "rus+kaz+eng").strip() or "rus+kaz+eng",
        tesseract_cmd=get("TESSERACT_CMD").strip() or None,
        default_filter=flt if flt in ("bw", "gray", "color", "orig") else "bw",
    )


def load_prefs() -> dict[str, dict]:
    try:
        data = json.loads(PREFS_FILE.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(prefs: dict[str, dict]) -> None:
    tmp = PREFS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, PREFS_FILE)
