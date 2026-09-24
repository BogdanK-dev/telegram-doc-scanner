"""
Запускатель бота: один раз готовит окружение Python и библиотеки,
потом держит бота запущенным и перезапускает его, если он упал.

Обычно его запускает start.bat, вручную ничего делать не нужно.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
import venv
from pathlib import Path

BASE = Path(__file__).resolve().parent
VENV = BASE / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQ = BASE / "requirements.txt"
STAMP = VENV / "requirements.sha1"


def ensure_environment() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit(f"Нужен Python 3.10 или новее, а установлен {sys.version.split()[0]}. "
                         "Скачайте свежий: https://www.python.org/downloads/")
    if not PY.exists():
        print("[1/2] Создание окружения Python (выполняется один раз)…", flush=True)
        venv.EnvBuilder(with_pip=True).create(VENV)
    digest = hashlib.sha1(REQ.read_bytes()).hexdigest()
    if not STAMP.exists() or STAMP.read_text().strip() != digest:
        print("[2/2] Установка библиотек (выполняется один раз, 1–3 минуты)…", flush=True)
        subprocess.call([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                         "--upgrade", "pip"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.check_call([str(PY), "-m", "pip", "install", "--disable-pip-version-check",
                               "-r", str(REQ)])
        # необязательно: фото iPhone в формате HEIC, если отправлены файлом
        subprocess.call([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-q",
                         "pillow-heif"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        STAMP.write_text(digest)
        print("Установка завершена.\n", flush=True)


def main() -> int:
    try:
        ensure_environment()
    except subprocess.CalledProcessError:
        print("\nОшибка установки библиотек. Проверьте подключение к интернету и запустите start.bat повторно.")
        return 1
    fails = 0
    while True:
        started = time.time()
        try:
            code = subprocess.call([str(PY), str(BASE / "bot.py")], cwd=str(BASE))
        except KeyboardInterrupt:
            return 0
        if code in (0, 2):  # 0 — остановили вручную, 2 — ошибка настройки (см. сообщение выше)
            return code
        fails = fails + 1 if time.time() - started < 60 else 1
        wait = min(300, 10 * fails)
        print(f"\nБот остановлен (код {code}). Перезапуск через {wait} с. "
              f"Для полной остановки закройте это окно.", flush=True)
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
