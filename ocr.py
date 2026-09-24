"""
Распознавание текста через Tesseract (бесплатный OCR-движок от Google).

Бот работает и без него — просто не будет кнопки «Распознать текст».
Языковые модели (rus, kaz, eng…) при первом запуске докачиваются сами
в папку tessdata рядом с ботом.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("ocr")

IS_WINDOWS = sys.platform == "win32"
MODEL_URLS = [
    "https://github.com/tesseract-ocr/tessdata/raw/main/{name}",
    "https://github.com/tesseract-ocr/tessdata_best/raw/main/{name}",
    "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{name}",
]
PDF_FONT_URLS = [
    "https://github.com/tesseract-ocr/tesseract/raw/main/tessdata/pdf.ttf",
]


@dataclass
class OcrResult:
    text: str
    pdf_layer: bytes | None  # одностраничный PDF только с невидимым текстом


def _is_ascii(p: Path) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def find_tesseract(explicit: str | None = None) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    found = shutil.which("tesseract")
    if found:
        candidates.append(found)
    if IS_WINDOWS:
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "ProgramW6432"):
            base = os.environ.get(env)
            if base:
                candidates.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
                candidates.append(os.path.join(base, "Programs", "Tesseract-OCR", "tesseract.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def _download(urls: list[str], name: str, dest: Path) -> bool:
    for tpl in urls:
        url = tpl.format(name=name)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            log.info("Скачиваю %s …", url)
            req = urllib.request.Request(url, headers={"User-Agent": "ScanBot"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            if tmp.stat().st_size < 1000:
                raise ValueError("слишком маленький файл")
            tmp.replace(dest)
            return True
        except Exception as e:  # пробуем следующее зеркало
            log.warning("Не получилось скачать %s: %s", url, e)
            tmp.unlink(missing_ok=True)
    return False


class Ocr:
    def __init__(self, langs: str = "rus+kaz+eng", cmd: str | None = None, base_dir: Path | None = None):
        self.langs = "+".join(x for x in re.split(r"[+,\s]+", langs.strip()) if x) or "eng"
        self.cmd = find_tesseract(cmd)
        base_dir = Path(base_dir or Path(__file__).resolve().parent)
        if IS_WINDOWS and not _is_ascii(base_dir):
            # Tesseract на Windows не открывает пути с кириллицей — берём «безопасную» папку
            base_dir = Path(os.environ.get("PROGRAMDATA", "C:\\ProgramData")) / "ScanBot"
        self.data_dir = base_dir / "tessdata"
        self.tmp_dir = base_dir / "tmp"
        self.use_data_dir = True
        self.ready = False
        self.problem = "Tesseract не найден" if not self.cmd else None

    # ── подготовка (выполняется один раз при старте, в отдельном потоке) ──
    def prepare(self) -> bool:
        if not self.cmd:
            log.warning("OCR выключен: Tesseract не установлен (запустите install_ocr.bat)")
            return False
        try:
            out = subprocess.run([self.cmd, "--list-langs"], capture_output=True, timeout=30,
                                 creationflags=_no_window())
            listing = (out.stdout + out.stderr).decode("utf-8", "replace")
        except Exception as e:
            self.problem = f"Tesseract не запускается: {e}"
            log.warning("OCR выключен: %s", self.problem)
            return False
        m = re.search(r'"([^"]+)"', listing)
        sys_dir = Path(m.group(1)) if m else None
        sys_langs = set(re.findall(r"^\s*([A-Za-z_]+)\s*$", listing, re.M))
        need = self.langs.split("+")

        if sys_dir and all(l in sys_langs for l in need) and (sys_dir / "pdf.ttf").is_file():
            self.use_data_dir = False  # всё уже есть в самом Tesseract
            log.info("OCR: использую языки Tesseract из %s", sys_dir)
        else:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            got: list[str] = []
            for name in [f"{l}.traineddata" for l in need] + ["pdf.ttf"]:
                dest = self.data_dir / name
                ok = dest.is_file() and dest.stat().st_size > 1000
                if not ok and sys_dir and (sys_dir / name).is_file():
                    shutil.copyfile(sys_dir / name, dest)
                    ok = True
                if not ok:
                    ok = _download(PDF_FONT_URLS if name == "pdf.ttf" else MODEL_URLS, name, dest)
                if ok and name != "pdf.ttf":
                    got.append(name.split(".")[0])
                elif not ok:
                    log.warning("Не удалось получить %s. Можно положить файл вручную в %s", name, self.data_dir)
                    if name == "pdf.ttf":
                        self.problem = "нет файла pdf.ttf"
                        return False
            if not got:
                self.problem = "не удалось скачать языковые модели"
                log.warning("OCR выключен: %s", self.problem)
                return False
            if len(got) < len(need):  # работаем с тем, что есть
                self.langs = "+".join(got)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        # пробный прогон — чтобы сразу увидеть проблемы в логе, а не у пользователя
        try:
            test = np.full((60, 200), 255, np.uint8)
            cv2.putText(test, "Test 123", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
            self.ready = True
            self.recognize(test, 150)
        except Exception as e:
            self.ready = False
            self.problem = f"пробное распознавание не удалось: {e}"
            log.warning("OCR выключен: %s", self.problem)
            return False
        log.info("OCR готов: %s (%s)", self.cmd, self.langs)
        return True

    # ── распознавание одной страницы ──
    def recognize(self, img: np.ndarray, dpi: int) -> OcrResult:
        if not self.ready:
            raise RuntimeError(self.problem or "OCR не готов")
        with tempfile.TemporaryDirectory(dir=self.tmp_dir) as td:
            inp = Path(td) / "page.png"
            ok, buf = cv2.imencode(".png", img)
            if not ok:
                raise ValueError("не удалось подготовить картинку")
            inp.write_bytes(buf.tobytes())
            outbase = Path(td) / "out"
            cmd = [self.cmd, str(inp), str(outbase)]
            if self.use_data_dir:
                cmd += ["--tessdata-dir", str(self.data_dir)]
            cmd += ["-l", self.langs, "--dpi", str(int(dpi)), "--oem", "1", "--psm", "3",
                    "-c", "tessedit_create_txt=1", "-c", "tessedit_create_pdf=1", "-c", "textonly_pdf=1"]
            env = dict(os.environ)
            if self.use_data_dir:
                env.pop("TESSDATA_PREFIX", None)
            res = subprocess.run(cmd, capture_output=True, timeout=300, env=env, creationflags=_no_window())
            txt = outbase.with_suffix(".txt")
            pdf = outbase.with_suffix(".pdf")
            if not txt.is_file():
                err = res.stderr.decode("utf-8", "replace").strip()
                raise RuntimeError(f"tesseract: {err[-300:] or res.returncode}")
            text = txt.read_text("utf-8", errors="replace")
            layer = pdf.read_bytes() if pdf.is_file() and pdf.stat().st_size > 0 else None
        return OcrResult(_clean(text), layer)


def _clean(text: str) -> str:
    text = text.replace("\x0c", "").replace("\r\n", "\n")
    lines = [l.rstrip() for l in text.split("\n")]
    out: list[str] = []
    for l in lines:  # не больше одной пустой строки подряд
        if not l and (not out or not out[-1]):
            continue
        out.append(l)
    return "\n".join(out).strip()
