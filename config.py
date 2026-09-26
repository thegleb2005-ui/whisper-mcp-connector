"""
Централизованная конфигурация. Все значения берутся из файла config.env
(см. config.env.example).
"""
import os
from dotenv import load_dotenv

load_dotenv("config.env")

# --- Свой секретный ключ для защиты MCP-сервера. Без него любой человек с
#     твоим публичным URL мог бы дёргать распознавание за твой счёт.
#     Придумай любую длинную случайную строку. ---
API_KEY = os.getenv("API_KEY", "")

# --- На каком порту слушать HTTP. Bothost может прокидывать порт через
#     переменную окружения PORT — если так, она перекроет значение по
#     умолчанию ниже. ---
PORT = int(os.getenv("PORT", "8000"))

# --- Whisper (распознавание речи) ---
# Размер модели: tiny, base, small, medium, large-v3 — от быстрого/грубого
# к медленному/точному. "base" — разумный баланс для CPU-сервера с 1 ГБ RAM.
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")

# Таймаут на распознавание одного 10-минутного куска в отдельном процессе.
WHISPER_SUBPROCESS_TIMEOUT = int(os.getenv("WHISPER_SUBPROCESS_TIMEOUT", "900"))

# --- YouTube cookies (запасной путь, если блокирует скачивание) ---
COOKIES_FILE = os.getenv("COOKIES_FILE", "")

# --- Пути ---
DOWNLOADS_DIR = os.getenv("DOWNLOADS_DIR", "downloads")


def validate():
    missing = []
    if not API_KEY:
        missing.append("API_KEY")
    if missing:
        raise RuntimeError(
            f"Не заданы переменные окружения: {', '.join(missing)}. "
            f"Проверь файл config.env (см. config.env.example)."
        )
