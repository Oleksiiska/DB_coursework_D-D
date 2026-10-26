"""Початкові датасети та їх завантаження (таблиця 2.1, п. 2.9).

Джерела зафіксовано: коміт репозиторію 5e-database і конкретний архів
FIREBALL, тож повторна підготовка на будь-якому комп'ютері дає ті самі CSV.
Завантаження докачує перерваний файл (заголовок Range) і перевіряє розмір.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

import httpx

log = logging.getLogger("prepare")

#: Знімок 5e-database, на якому виконано виміри розділу 2 (каталог src/2014/en)
SRD_COMMIT = "a6212beb4b278917c2cff41c2b04c4ef4f3e0d6f"
SRD_BASE_URL = "https://raw.githubusercontent.com/5e-bits/5e-database/{commit}/src/2014/en/{name}"
SRD_FILES = (
    "5e-SRD-Monsters.json", "5e-SRD-Spells.json", "5e-SRD-Magic-Items.json", "5e-SRD-Alignments.json",
    "5e-SRD-Races.json", "5e-SRD-Subraces.json", "5e-SRD-Classes.json", "5e-SRD-Languages.json",
    "5e-SRD-Damage-Types.json", "5e-SRD-Conditions.json", "5e-SRD-Skills.json",
)

#: Повний архів FIREBALL за посиланням авторів датасету (github.com/zhudotexe/FIREBALL:
#: «7.7GB; last updated Nov 28, 2022») — каталоги anonymized/filtered і anonymized/data.
#: Копія на Hugging Face містить лише filtered під іменами-хешами: без імені бою
#: {unix-час}-{uuid} і без сирих подій зустрічі з неї не відтворити
FIREBALL_URL = "https://datasets.mechanus.zhu.codes/fireball-anonymized-nov-28-2022-kfdjl.tar.gz"
#: Запас вільного місця понад розмір файлу
DISK_MARGIN_BYTES = 200 * 1024 * 1024

USER_AGENT = "dnd-adventures-prepare/0.1 (+https://github.com/Oleksiiska/DB_coursework_D-D)"


class DownloadError(RuntimeError):
    """Файл не вдалося завантажити. ``retryable`` — чи має сенс повторити спробу."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class NotEnoughSpace(DownloadError):
    """На диску менше місця, ніж потрібно для файлу."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


def fireball_url() -> str:
    """Адреса архіву: змінна середовища FIREBALL_URL або зафіксована."""
    return os.environ.get("FIREBALL_URL") or FIREBALL_URL


def file_name(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0] or "download"


def download(url: str, target: Path, *, retries: int = 5, client: httpx.Client | None = None,
             backoff: float = 5.0) -> Path:
    """Завантаження у ``target`` з докачуванням.

    Частина файлу пишеться в ``<target>.part``; після обриву наступна спроба
    продовжує з того самого байта, якщо сервер підтримує Range, інакше
    починає спочатку. Готовий файл перевіряється за розміром і лише тоді
    отримує остаточну назву, тож незавершене завантаження не сплутати з готовим.
    """
    if target.exists():
        log.info("%s: уже завантажено (%s)", target.name, _size(target.stat().st_size))
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, connect=30.0),
                                    headers={"User-Agent": USER_AGENT})
    try:
        for attempt in range(1, retries + 1):
            try:
                size = _attempt(client, url, part, target.name)
                part.replace(target)
                log.info("%s: завантажено %s", target.name, _size(size))
                return target
            except (httpx.HTTPError, DownloadError) as error:
                if isinstance(error, NotEnoughSpace):
                    raise
                if isinstance(error, DownloadError) and not error.retryable:
                    raise DownloadError(f"{url}: {error}", retryable=False) from error
                if attempt == retries:
                    raise DownloadError(f"{url}: {error}") from error
                delay = min(60.0, backoff * attempt)
                log.warning("%s: %s — повтор %d з %d через %.0f с", target.name, error or type(error).__name__,
                            attempt, retries, delay)
                time.sleep(delay)
    finally:
        if own_client:
            client.close()
    raise DownloadError(url)


def _attempt(client: httpx.Client, url: str, part: Path, name: str) -> int:
    """Одна спроба: з початку або з байта, на якому обірвалася попередня."""
    done = part.stat().st_size if part.exists() else 0
    # identity: файл зберігається байт у байт, а Content-Length — його справжній розмір
    # (стиснена сервером відповідь мала б інший розмір, ніж записаний файл)
    headers = {"Accept-Encoding": "identity"}
    if done:
        headers["Range"] = f"bytes={done}-"
    with client.stream("GET", url, headers=headers) as response:
        status = response.status_code
        if status == 416 and done:
            # частина вже має повний розмір — або більша за файл, тоді спочатку
            total = _total_from_range(response.headers.get("Content-Range"))
            if total == done:
                return done
            part.unlink(missing_ok=True)
            raise DownloadError("збережена частина не відповідає файлу — завантаження спочатку")
        if status not in (200, 206):
            raise DownloadError(f"HTTP {status}", retryable=status >= 500 or status in (408, 429))
        if status == 200:
            done = 0  # Range не підтримано — файл пишеться заново
        total = _total_size(response, done)
        _check_space(part.parent, (total or 0) - done, name)
        _copy(response, part, append=status == 206, done=done, total=total, name=name)
    size = part.stat().st_size
    if total is not None and size != total:
        raise DownloadError(f"отримано {size} з {total} байтів")
    return size


def _total_from_range(value: str | None) -> int | None:
    """«bytes */1234» або «bytes 0-99/1234» → 1234."""
    if value and "/" in value and not value.endswith("/*"):
        try:
            return int(value.rsplit("/", 1)[1])
        except ValueError:
            return None
    return None


def _check_space(folder: Path, needed: int, name: str) -> None:
    free = shutil.disk_usage(folder).free
    if needed > 0 and free < needed + DISK_MARGIN_BYTES:
        raise NotEnoughSpace(f"{name}: недостатньо місця на диску — потрібно {_size(needed + DISK_MARGIN_BYTES)}, "
                             f"вільно {_size(free)}")


def _total_size(response: httpx.Response, done: int) -> int | None:
    if response.headers.get("Content-Encoding", "identity").lower() not in ("identity", ""):
        return None  # сервер стиснув відповідь попри identity — розмір файлу заздалегідь невідомий
    total = _total_from_range(response.headers.get("Content-Range"))  # bytes 100-199/200
    if total is not None:
        return total
    length = response.headers.get("Content-Length")
    if length is None:
        return None
    return int(length) + (done if response.status_code == 206 else 0)


def _copy(response: httpx.Response, part: Path, *, append: bool, done: int, total: int | None, name: str) -> None:
    """Запис байтів у міру надходження: після обриву на диску лишається все отримане."""
    started = last = time.monotonic()
    written = done if append else 0
    with part.open("ab" if append else "wb") as stream:
        for chunk in response.iter_bytes():
            stream.write(chunk)
            written += len(chunk)
            now = time.monotonic()
            if now - last >= 15:
                last = now
                speed = (written - (done if append else 0)) / max(now - started, 1e-6)
                left = f", лишилося ≈ {int((total - written) / speed / 60)} хв" if total and speed > 0 else ""
                share = f" ({written * 100 // total} %)" if total else ""
                log.info("%s: %s%s, %s/с%s", name, _size(written), share, _size(speed), left)


def _size(value: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.1f} {unit}".replace(".", ",") if unit != "Б" else f"{int(value)} Б"
        value /= 1024
    return f"{value:.1f} ГБ".replace(".", ",")
