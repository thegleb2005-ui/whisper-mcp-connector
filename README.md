# Whisper MCP Connector — личный бесплатный сервис транскрибации

Свой MCP-коннектор для Claude: транскрибирует видео по ссылке (YouTube,
Instagram Reels, TikTok) через локальный Whisper. Бесплатная замена
платному WhisperAI — весь код уже проверен на проекте `threads-agent`,
здесь просто собран в отдельный MCP-сервер.

## Как это работает

```
Claude → HTTPS-запрос → твой сервер на Bothost
                              │
                    проверка ключа (API_KEY)
                              │
                    tool: transcribe_video(url)
                              │
              downloader.py (yt-dlp, YouTube/Instagram/TikTok)
                              │
              transcriber.py (Whisper в отдельном процессе)
                              │
                    текст возвращается в Claude
```

## Структура файлов

| Файл | Что делает |
|---|---|
| `mcp_server.py` | Точка входа — MCP-сервер + проверка ключа |
| `downloader.py` | Скачивание аудио (тот же код, что в threads-agent) |
| `transcriber.py` | Локальный Whisper с изоляцией по процессам |
| `config.py` | Загрузка настроек из `config.env` |
| `config.env.example` | Шаблон — скопируй в `config.env` и заполни |

## 1. Требования

Python 3.11+. ffmpeg ставить отдельно не нужно (idem imageio-ffmpeg).

## 2. Настройка

```bash
cd whisper-mcp-connector
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp config.env.example config.env
```

Сгенерируй секретный ключ и впиши его в `config.env`:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```
Впиши результат в `API_KEY=` внутри `config.env`. Это твой личный пароль —
без него сервер никого не пустит, даже зная адрес.

## 3. Локальный тест

```bash
python mcp_server.py
```

В логах появится `MCP-сервер запускается на порту 8000...`. Проверить, что
защита работает:
```bash
curl http://127.0.0.1:8000/mcp
# должно вернуть 401 Unauthorized — это правильно, ключ не передан
```

## 4. Деплой на Bothost

Заводишь это как **отдельный, новый бот/проект** на Bothost (не смешивай
с `threads-agent` — это разные сервисы, разная логика запуска: тот
слушает Telegram, этот — HTTP).

1. Залей код в отдельный репозиторий на GitHub (`whisper-mcp-connector`).
2. В панели Bothost создай новый проект, подключи автодеплой из репозитория.
3. Пропиши переменные окружения: `API_KEY` (твой сгенерированный ключ),
   при желании `WHISPER_MODEL_SIZE`.
4. Команда запуска: `python mcp_server.py`.
5. Bothost сам выдаёт публичный URL вида `https://bot-XXX.bothost.ru` с
   готовым SSL. Это и есть твой адрес для подключения к Claude — полный
   путь будет `https://bot-XXX.bothost.ru/mcp`.

**Если Bothost прокидывает порт через свою переменную `PORT`** — код уже
её подхватит сам, ничего дополнительно делать не надо. Если увидишь в
логах, что сервер не отвечает по адресу, который даёт Bothost — сверься
с их панелью, на каком порту они ожидают входящие подключения, и
поправь `PORT` в `config.env` под это значение.

## 5. Подключение к Claude

1. В Claude (claude.ai или приложение) → **Settings → Connectors → Add → Add custom connector**
2. **Name**: любое, например "Мой Whisper"
3. **Remote MCP server URL**: `https://bot-XXX.bothost.ru/mcp` (свой адрес с шага 4)
4. Дальше должна появиться секция **Request headers** (если её нет —
   значит, для твоего аккаунта эта функция ещё не включена, тогда
   единственный вариант — сделать сервер без авторизации совсем, что
   небезопасно, либо написать в поддержку Anthropic)
5. Добавь заголовок: **Authorization** → **Bearer <твой API_KEY>**
6. Save → Connect

Дальше просто в любом чате пиши: *"Расшифруй это видео: <ссылка>"* — Claude
сам вызовет инструмент.

## 6. Ограничения (как и в threads-agent)

- **Один бесплатный кастомный коннектор** на бесплатном тарифе Claude —
  если уже используешь WhisperAI как коннектор, возможно, придётся выбрать
  между ними или проверить лимиты своего тарифа.
- **1 ГБ RAM (тариф Basic на Bothost)** — должно хватать для модели `base`
  с текущей изоляцией по процессам, но если увидишь падения на длинных
  видео — попробуй `WHISPER_MODEL_SIZE=tiny` в `config.env`.
- **YouTube может блокировать скачивание** — те же грабли, что в
  threads-agent. Если увидишь "Sign in to confirm you're not a bot" —
  see раздел про cookies в README проекта `threads-agent`, логика
  идентична (`COOKIES_FILE` в `config.env`).
- **Один и тот же сервер, если запускать оба проекта одновременно на
  тарифе Basic** — 1 ГБ RAM делится между всеми ботами на тарифе, так что
  если threads-agent тоже активно транскрибирует в этот момент — может
  быть тесно. Последи за логами обоих в первые дни.
