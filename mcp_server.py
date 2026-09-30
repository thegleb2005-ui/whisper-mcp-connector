"""
Личный MCP-коннектор для транскрибации видео — бесплатная замена платному
WhisperAI. Один инструмент: transcribe_video(url) — скачивает аудио по ссылке
(YouTube, Instagram Reels, TikTok, VK Видео, Rutube, Vimeo и др.) и
распознаёт речь локальным Whisper.

Работает как удалённый MCP-сервер (Streamable HTTP), к которому Claude
подключается как к Custom Connector по публичному HTTPS-адресу:
    https://<домен>/mcp/<API_KEY>

Что здесь есть:
  - защита секретным ключом в адресе (и запасной вариант — заголовок
    Authorization: Bearer <API_KEY>);
  - открытые адреса / и /health для проверок со стороны хостинга;
  - режим без сессий — перезапуски сервера не ломают подключение в Claude;
  - понятный текст ошибки прямо в чат вместо общего "Error executing tool";
  - фоновые задачи для длинных видео (transcribe_video + get_transcription)
    и очередь «одно видео за раз», чтобы сервер не падал по памяти.

Запуск: python mcp_server.py
"""
import os
import time
import uuid
import asyncio
import logging

import uvicorn
from starlette.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

import config
from downloader import download_audio
from transcriber import transcribe_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="whisper-transcriber",
    instructions=(
        "Транскрибирует видео по ссылке (YouTube, Instagram Reels, TikTok, "
        "VK Видео, Rutube, Vimeo и другие видеохостинги, а также прямые ссылки "
        "на аудио/видеофайлы) в текст. Используй это, когда пользователь просит "
        "расшифровать, переписать текстом или пересказать содержимое видео по ссылке."
    ),
)


# --- Фоновые задачи ---------------------------------------------------------
# Распознавание идёт примерно вдвое быстрее длительности видео, а Claude ждёт
# ответа от инструмента ограниченное время. Поэтому работа идёт в фоне:
# transcribe_video ждёт до WAIT_SECONDS и, если не успел, возвращает номер
# задачи, а get_transcription потом забирает результат.
WAIT_SECONDS = 45
JOB_TTL_SECONDS = 24 * 3600

JOBS: dict[str, dict] = {}       # номер задачи -> состояние
URL_TO_JOB: dict[str, str] = {}  # ссылка -> номер задачи (повторный запрос не качает заново)
_BACKGROUND_TASKS: set = set()   # держим ссылки, чтобы задачи не собрал сборщик мусора
_ONE_AT_A_TIME = asyncio.Semaphore(1)  # очередь: одно видео за раз, иначе не хватит памяти

STATUS_RU = {
    "queued": "в очереди",
    "downloading": "скачиваю аудио",
    "transcribing": "распознаю речь",
}


def _cleanup_old_jobs():
    now = time.time()
    for job_id in [j for j, d in JOBS.items() if now - d["created"] > JOB_TTL_SECONDS]:
        URL_TO_JOB.pop(JOBS[job_id]["url"], None)
        JOBS.pop(job_id, None)


async def _run_job(job_id: str):
    job = JOBS[job_id]
    async with _ONE_AT_A_TIME:
        job["status"] = "downloading"
        try:
            audio_path, title = await download_audio(job["url"])
        except Exception as e:
            logger.exception(f"[{job_id}] Не удалось скачать видео")
            job.update(status="error", error=(
                f"ОШИБКА СКАЧИВАНИЯ: {type(e).__name__}: {e}\n\n"
                f"Если в тексте есть 'Sign in to confirm you're not a bot' — YouTube "
                f"блокирует IP сервера, нужны свежие cookies (переменная COOKIES_B64)."
            ))
            return

        job.update(status="transcribing", title=title)
        logger.info(f"[{job_id}] Аудио скачано: {title}")
        try:
            text = await transcribe_audio(audio_path)
        except Exception as e:
            logger.exception(f"[{job_id}] Не удалось распознать речь")
            job.update(status="error", error=f"ОШИБКА РАСПОЗНАВАНИЯ: {type(e).__name__}: {e}")
            return
        finally:
            try:
                os.remove(audio_path)  # аудио больше не нужно — не забиваем диск
            except OSError:
                pass

        job.update(status="done", text=text, finished=time.time())
        logger.info(f"[{job_id}] Готово: {len(text)} символов")


def _job_report(job_id: str) -> str:
    job = JOBS[job_id]
    if job["status"] == "done":
        return f"[{job.get('title', '')}]\n\n{job['text']}"
    if job["status"] == "error":
        return job["error"]

    elapsed = int(time.time() - job["created"])
    waiting_ahead = sum(
        1 for d in JOBS.values()
        if d["status"] in ("queued", "downloading", "transcribing") and d["created"] < job["created"]
    )
    queue_note = f" Перед ней в очереди: {waiting_ahead}." if waiting_ahead else ""
    title = f" «{job['title']}»" if job.get("title") else ""
    return (
        f"ЗАДАЧА В РАБОТЕ. Номер задачи: {job_id}\n"
        f"Видео{title}: {STATUS_RU.get(job['status'], job['status'])}, "
        f"прошло {elapsed // 60} мин {elapsed % 60} сек.{queue_note}\n"
        f"Распознавание занимает примерно половину длительности видео. "
        f"Вызови get_transcription с этим номером через минуту-две, чтобы забрать результат."
    )


@mcp.tool()
async def transcribe_video(url: str) -> str:
    """Скачивает видео по ссылке и возвращает полный текст того, что там
    сказано. Поддерживает YouTube (включая Shorts), Instagram Reels, TikTok,
    VK Видео, Rutube, Vimeo, X/Twitter и сотни других сайтов, а также прямые
    ссылки на аудио- и видеофайлы.

    Короткие видео возвращаются сразу. Для длинных инструмент вернёт
    "ЗАДАЧА В РАБОТЕ" и номер задачи — тогда через минуту-две вызови
    get_transcription с этим номером, повторяя, пока не придёт текст.

    Args:
        url: Ссылка на видео.
    """
    _cleanup_old_jobs()
    logger.info(f"Запрос на транскрибацию: {url}")

    job_id = URL_TO_JOB.get(url)
    if job_id and job_id in JOBS and JOBS[job_id]["status"] != "error":
        logger.info(f"Ссылка уже в работе или готова: задача {job_id}")
    else:
        job_id = uuid.uuid4().hex[:8]
        JOBS[job_id] = {"url": url, "status": "queued", "created": time.time()}
        URL_TO_JOB[url] = job_id
        task = asyncio.create_task(_run_job(job_id))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline and JOBS[job_id]["status"] not in ("done", "error"):
        await asyncio.sleep(1)
    return _job_report(job_id)


@mcp.tool()
async def get_transcription(job_id: str) -> str:
    """Забирает результат фоновой расшифровки по номеру задачи, который
    вернул transcribe_video. Если задача ещё идёт — вернёт текущий этап;
    тогда подожди минуту-две и вызови снова.

    Args:
        job_id: Номер задачи из ответа transcribe_video.
    """
    job_id = job_id.strip()
    if job_id not in JOBS:
        return (
            f"Задача {job_id} не найдена. Возможно, сервер перезапускался — "
            f"тогда просто вызови transcribe_video с той же ссылкой ещё раз."
        )
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline and JOBS[job_id]["status"] not in ("done", "error"):
        await asyncio.sleep(1)
    return _job_report(job_id)


def _build_app():
    """Собирает веб-приложение MCP и оборачивает его проверкой ключа —
    иначе твой публичный адрес сможет использовать кто угодно."""

    # stateless_http=True — режим без сессий: каждый запрос самостоятельный.
    #   Иначе после каждого перезапуска (деплой на Bothost) Claude приходит со
    #   старым номером сессии, получает "сессия не найдена" и просит
    #   переподключить коннектор.
    # enable_dns_rebinding_protection=False — по умолчанию библиотека MCP
    #   принимает запросы только на localhost. Наш сервер публичный, стоит за
    #   прокси хостинга и защищён ключом, поэтому эту проверку отключаем —
    #   иначе любой запрос на домен Bothost получает 421 Misdirected Request.
    app = mcp.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    # Открытые адреса без ключа — для проверок "живости" со стороны хостинга.
    # Никаких данных не отдают, только статус.
    public_paths = {"/", "/health"}

    # Ключ В АДРЕСЕ: https://домен/mcp/<API_KEY>. Свои заголовки в Claude
    # требуют одобрения Anthropic, поэтому ключ передаётся прямо в пути.
    # Сервер переписывает путь на /mcp и передаёт запрос дальше.
    secret_path = f"/mcp/{config.API_KEY}"

    async def authed_app(scope, receive, send):
        # Служебные события запуска/остановки (lifespan) пропускаем как есть.
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path in public_paths:
            response = JSONResponse({"status": "ok", "service": "whisper-transcriber"})
            await response(scope, receive, send)
            return

        # Вариант 1: ключ в адресе (так подключается Claude).
        if path in (secret_path, secret_path + "/"):
            scope = dict(scope)
            scope["path"] = "/mcp"
            scope["raw_path"] = b"/mcp"
            await app(scope, receive, send)
            return

        # Вариант 2: ключ в заголовке Authorization: Bearer <API_KEY>.
        if path in ("/mcp", "/mcp/"):
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()
            if auth_header == f"Bearer {config.API_KEY}":
                await app(scope, receive, send)
                return

        # Всё остальное — 404. Именно 404, а не 401: на 401 Claude решил бы,
        # что сервер требует OAuth, и пытался бы пройти вход.
        response = JSONResponse({"error": "Not found"}, status_code=404)
        await response(scope, receive, send)

    return authed_app


if __name__ == "__main__":
    config.validate()
    app = _build_app()
    cookies_ok = bool(config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE))
    logger.info(
        f"MCP-сервер запускается на порту {config.PORT} "
        f"(WHISPER_MODEL_SIZE={config.WHISPER_MODEL_SIZE!r}, "
        f"cookies={'ДА' if cookies_ok else 'НЕТ'})"
    )
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
