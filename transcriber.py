"""
Распознавание речи через локальный Whisper (faster-whisper). Без единого
внешнего API — бесплатно, всё крутится на своём сервере.

Каждый кусок аудио распознаётся в ОТДЕЛЬНОМ ПРОЦЕССЕ: на слабых серверах
память между последовательными вызовами Whisper освобождается не до конца
(похоже на утечку на уровне ctranslate2), и без изоляции по процессам
через пару кусков подряд процесс падает по нехватке памяти. Отдельный
процесс на каждый кусок гарантирует, что ОС вернёт всю память при его
завершении, независимо от утечек внутри библиотеки.

Длинные файлы (дольше CHUNK_SECONDS) режутся на куски перед распознаванием
— и по той же причине с памятью, и чтобы не держать в одном процессе
слишком длинный кусок аудио разом.
"""
import os
import re
import math
import time
import logging
import asyncio
import subprocess
import tempfile
import multiprocessing as mp

import imageio_ffmpeg

from config import WHISPER_MODEL_SIZE, WHISPER_SUBPROCESS_TIMEOUT

logger = logging.getLogger(__name__)

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
CHUNK_SECONDS = 600  # 10 минут на кусок


def _get_duration_seconds(file_path: str) -> float:
    # ffprobe отдельно не ставим (imageio-ffmpeg даёт только ffmpeg), поэтому
    # достаём длительность из служебного вывода самого ffmpeg.
    result = subprocess.run(
        [FFMPEG_PATH, "-i", file_path],
        capture_output=True, text=True,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not match:
        raise RuntimeError(f"Не удалось определить длительность файла {file_path}: {result.stderr[-500:]}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _split_audio(file_path: str, tmp_dir: str) -> list[str]:
    duration = _get_duration_seconds(file_path)
    n_chunks = max(1, math.ceil(duration / CHUNK_SECONDS))
    chunk_paths = []
    for i in range(n_chunks):
        start = i * CHUNK_SECONDS
        chunk_path = os.path.join(tmp_dir, f"chunk_{i}.mp3")
        subprocess.run(
            [FFMPEG_PATH, "-y", "-i", file_path, "-ss", str(start),
             "-t", str(CHUNK_SECONDS), "-c", "copy", chunk_path],
            capture_output=True, check=True,
        )
        chunk_paths.append(chunk_path)
    return chunk_paths


def _whisper_subprocess_worker(file_path: str, model_size: str, queue) -> None:
    """Выполняется В ОТДЕЛЬНОМ ПРОЦЕССЕ. Загружает модель, распознаёт файл,
    кладёт результат в очередь и завершается."""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        segments, info = model.transcribe(file_path, beam_size=5)
        text = " ".join(segment.text.strip() for segment in segments)
        queue.put(("ok", text))
    except Exception as e:
        queue.put(("error", f"{type(e).__name__}: {e}"))


def _transcribe_file_sync(file_path: str) -> str:
    """Транскрибирует один (уже короткий) файл в отдельном процессе."""
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_whisper_subprocess_worker,
        args=(file_path, WHISPER_MODEL_SIZE, queue),
    )
    process.start()
    process.join(timeout=WHISPER_SUBPROCESS_TIMEOUT)

    if process.is_alive():
        process.terminate()
        process.join()
        raise TimeoutError(
            f"Распознавание не уложилось в {WHISPER_SUBPROCESS_TIMEOUT} сек "
            f"для {file_path} — процесс принудительно остановлен"
        )

    if process.exitcode != 0:
        raise RuntimeError(
            f"Процесс распознавания упал (код выхода {process.exitcode}) при "
            f"обработке {file_path} — похоже на нехватку памяти на сервере"
        )

    if queue.empty():
        raise RuntimeError(f"Процесс распознавания завершился без результата для {file_path}")

    status, payload = queue.get()
    if status == "error":
        raise RuntimeError(f"Ошибка распознавания в дочернем процессе: {payload}")
    if not payload.strip():
        raise RuntimeError(f"Whisper вернул пустой транскрипт для {file_path}")
    return payload.strip()


def _transcribe_sync(file_path: str) -> str:
    duration = _get_duration_seconds(file_path)
    logger.info(f"Длительность файла {file_path}: {duration:.0f} сек")

    if duration <= CHUNK_SECONDS:
        return _transcribe_file_sync(file_path)

    logger.info(f"Файл длиннее {CHUNK_SECONDS} сек — режу на куски перед распознаванием")
    with tempfile.TemporaryDirectory() as tmp_dir:
        chunks = _split_audio(file_path, tmp_dir)
        texts = []
        for i, chunk in enumerate(chunks, start=1):
            logger.info(f"Распознаю кусок {i}/{len(chunks)}")
            texts.append(_transcribe_file_sync(chunk))
    return " ".join(texts)


async def transcribe_audio(file_path: str) -> str:
    """Распознаёт речь в аудиофайле. Возвращает распознанный текст."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _transcribe_sync, file_path)
