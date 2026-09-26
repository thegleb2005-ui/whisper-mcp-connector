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

# --- YouTube cookies (нужны, если YouTube блокирует скачивание) ---
# Два способа:
#  1) COOKIES_B64 — содержимое cookies.txt, закодированное в base64, прямо в
#     переменной окружения. Рекомендуется: файл не попадает в git и не
#     теряется при пересборке. При запуске сервер сам развернёт его в файл.
#  2) COOKIES_FILE — путь к уже лежащему на сервере cookies.txt.
COOKIES_FILE = os.getenv("COOKIES_FILE", "")
_COOKIES_B64 = os.getenv("COOKIES_B64", "").strip()

if _COOKIES_B64:
    import base64
    _cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
    try:
        with open(_cookies_path, "wb") as _f:
            _f.write(base64.b64decode(_COOKIES_B64))
        COOKIES_FILE = _cookies_path
    except Exception as _e:
        print(f"ВНИМАНИЕ: не удалось раскодировать COOKIES_B64: {_e}")

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
