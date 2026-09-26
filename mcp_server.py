"""
Личный MCP-коннектор для транскрибации видео — бесплатная замена платному
WhisperAI. Один инструмент: transcribe_video(url) — скачивает аудио из
YouTube/Instagram/TikTok и распознаёт речь локальным Whisper.

Публикуется как удалённый MCP-сервер (Streamable HTTP), чтобы Claude мог
подключиться к нему как к Custom Connector через публичный HTTPS-адрес.

Запуск: python mcp_server.py
"""
import os
import logging

import uvicorn
from starlette.responses import JSONResponse
from mcp.server.mcpserver import MCPServer

import config
from downloader import download_audio
from transcriber import transcribe_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

mcp = MCPServer(
    name="whisper-transcriber",
    instructions=(
        "Транскрибирует видео по ссылке (YouTube, Instagram Reels, TikTok) "
        "в текст. Используй это, когда пользователь просит расшифровать, "
        "переписать текстом или пересказать содержимое видео по ссылке."
    ),
)


@mcp.tool()
async def transcribe_video(url: str) -> str:
    """Скачивает видео по ссылке (YouTube, Instagram Reels, TikTok) и
    возвращает полный текст того, что там сказано.

    Args:
        url: Прямая ссылка на видео.
    """
    logger.info(f"Запрос на транскрибацию: {url}")
    audio_path, title = await download_audio(url)
    logger.info(f"Аудио скачано: {title}")
    transcript = await transcribe_audio(audio_path)
    logger.info(f"Распознано {len(transcript)} символов")
    return f"[{title}]\n\n{transcript}"


def _build_app():
    """Собирает Starlette-приложение и оборачивает его простой проверкой
    ключа в заголовке — иначе твой публичный URL сможет дёргать кто угодно."""
    app = mcp.streamable_http_app()

    original_app_call = app

    # Открытые адреса без ключа — для проверок "живости" со стороны хостинга.
    public_paths = {"/", "/health"}

    # Защита ключом В АДРЕСЕ: https://домен/mcp/<API_KEY>
    # Claude пока требует одобрения Anthropic для своих заголовков, поэтому
    # ключ передаётся прямо в пути. Сервер переписывает путь на /mcp и
    # передаёт запрос дальше. Старый вариант (заголовок Authorization: Bearer)
    # тоже продолжает работать.
    secret_path = f"/mcp/{config.API_KEY}"

    async def authed_app(scope, receive, send):
        if scope["type"] != "http":
            await original_app_call(scope, receive, send)
            return

        path = scope.get("path", "")

        if path in public_paths:
            response = JSONResponse({"status": "ok", "service": "whisper-transcriber"})
            await response(scope, receive, send)
            return

        # Вариант 1: ключ в адресе
        if path == secret_path or path == secret_path + "/":
            scope = dict(scope)
            scope["path"] = "/mcp"
            scope["raw_path"] = b"/mcp"
            await original_app_call(scope, receive, send)
            return

        # Вариант 2: ключ в заголовке
        if path in ("/mcp", "/mcp/"):
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization", b"").decode()
            if auth_header == f"Bearer {config.API_KEY}":
                await original_app_call(scope, receive, send)
                return

        # Всё остальное — 404, без подсказок. Важно отдавать именно 404, а не
        # 401: на 401 Claude решил бы, что сервер требует OAuth, и пытался бы
        # пройти авторизацию.
        response = JSONResponse({"error": "Not found"}, status_code=404)
        await response(scope, receive, send)

    return authed_app


if __name__ == "__main__":
    config.validate()
    app = _build_app()
    logger.info(
        f"MCP-сервер запускается на порту {config.PORT} "
        f"(WHISPER_MODEL_SIZE={config.WHISPER_MODEL_SIZE!r})"
    )
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
