import os
import io
import asyncio
import base64
import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
MINIMAX_MODEL = os.getenv("MINIMAX_MODEL", "minimax-m3:cloud")

CF_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CF_EMAIL = os.getenv("CLOUDFLARE_EMAIL", "")
CF_GLOBAL_KEY = os.getenv("CLOUDFLARE_GLOBAL_KEY", "")
CF_MODEL = os.getenv("CLOUDFLARE_MODEL", "@cf/stabilityai/stable-diffusion-xl-base-1.0")
# Текстовая модель Cloudflare Workers AI (быстрый режим /mode cloud)
CF_TEXT_MODEL = os.getenv("CLOUDFLARE_TEXT_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
# Режим текста по умолчанию: local (Ollama) или cloud (Cloudflare)
DEFAULT_TEXT_MODE = os.getenv("DEFAULT_TEXT_MODE", "local").lower()

PROMPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "system_strategy.md")
with open(PROMPT_PATH, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

MAX_MSG = 4000


def split_text(text, limit=MAX_MSG):
    chunks = []
    for para in text.split("\n"):
        if len(para) <= limit:
            chunks.append(para)
        else:
            for i in range(0, len(para), limit):
                chunks.append(para[i:i + limit])
    merged = []
    buf = ""
    for c in chunks:
        if len(buf) + len(c) + 1 <= limit:
            buf = (buf + "\n" + c) if buf else c
        else:
            if buf:
                merged.append(buf)
            buf = c
    if buf:
        merged.append(buf)
    return merged


def build_review_message(docs, instruction):
    blocks = [f"=== {name} ===\n{content}" for name, content in docs]
    files = "\n\n".join(blocks)
    instr = instruction or "Проведи полный стратегический разбор по твоему шаблону (Шаги 1-2 и требования к финальному ответу)."
    return ("Ниже приложены материалы. Прочитай их целиком и проведи анализ согласно своим правилам.\n\n"
            "ИСХОДНЫЕ МАТЕРИАЛЫ:\n" + files + "\n\nЗАДАНИЕ: " + instr)


def call_ollama(system_prompt, user_message, model=None):
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": model or OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=900)
        r.raise_for_status()
        return r.json()["message"]["content"]
    except requests.exceptions.ConnectionError:
        return ("❌ Не могу подключиться к Ollama. Убедись, что Ollama запущена "
                "(открой приложение Ollama или выполни `ollama serve` в терминале).")
    except Exception as e:
        return f"❌ Ошибка обращения к модели: {e}"


def call_cloudflare_text(system_prompt, user_message):
    if not CF_ACCOUNT_ID or not ((CF_GLOBAL_KEY and CF_EMAIL) or CF_TOKEN):
        return "⚠️ Cloudflare не настроен в .env (нужны CLOUDFLARE_API_TOKEN и CLOUDFLARE_ACCOUNT_ID)."
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_TEXT_MODEL}"
    headers = make_cf_headers()
    payload = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=300)
        if not r.ok:
            raise RuntimeError(f"Cloudflare вернул {r.status_code}: {r.text[:300]}")
        data = r.json()
        result = data.get("result", {})
        text = result.get("response") or result.get("content") or ""
        if not text:
            raise RuntimeError(f"Пустой ответ Cloudflare: {data}")
        return text
    except Exception as e:
        return f"❌ Ошибка Cloudflare: {e}"


def generate_text(system_prompt, user_message, mode):
    if mode == "cloud":
        return call_cloudflare_text(system_prompt, user_message)
    if mode == "minimax":
        return call_ollama(system_prompt, user_message, MINIMAX_MODEL)
    return call_ollama(system_prompt, user_message)


async def _send_long(update, text):
    for part in split_text(text):
        await update.message.reply_text(part)


async def _typing_loop(bot, chat_id, stop):
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        await asyncio.sleep(4)


def make_cf_headers():
    if CF_GLOBAL_KEY and CF_EMAIL:
        return {
            "X-Auth-Email": CF_EMAIL,
            "X-Auth-Key": CF_GLOBAL_KEY,
            "Content-Type": "application/json",
        }
    if CF_TOKEN:
        return {
            "Authorization": f"Bearer {CF_TOKEN}",
            "Content-Type": "application/json",
        }
    raise RuntimeError("Cloudflare не настроен в .env")


def generate_image(prompt):
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_MODEL}"
    headers = make_cf_headers()
    r = requests.post(url, headers=headers, json={"prompt": prompt}, timeout=180)
    if not r.ok:
        raise RuntimeError(f"Cloudflare вернул {r.status_code}: {r.text[:300]}")
    ctype = r.headers.get("Content-Type", "")
    if "application/json" in ctype or r.text.lstrip().startswith("{"):
        data = r.json()
        images = data.get("result", {}).get("images", [])
        if not images:
            raise RuntimeError(f"Нет изображения в ответе: {data}")
        img_b64 = images[0]
        if img_b64.startswith("data:"):
            img_b64 = img_b64.split(",", 1)[1]
        return base64.b64decode(img_b64)
    return r.content


async def _photo_action_loop(bot, chat_id, stop):
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action="upload_photo")
        except Exception:
            pass
        await asyncio.sleep(4)


async def img_command(update, context):
    if not CF_ACCOUNT_ID or not ((CF_GLOBAL_KEY and CF_EMAIL) or CF_TOKEN):
        await update.message.reply_text(
            "⚠️ Cloudflare не настроен в .env. Нужны CLOUDFLARE_API_TOKEN "
            "(или CLOUDFLARE_EMAIL + CLOUDFLARE_GLOBAL_KEY) и CLOUDFLARE_ACCOUNT_ID."
        )
        return
    prompt = update.message.text.replace("/img", "").strip()
    if not prompt:
        await update.message.reply_text(
            "Напиши промпт после команды, например:\n/img красивый кот в шляпе, масло, детально"
        )
        return
    await update.message.reply_text("⏳ Генерирую изображение…")
    bot = update.get_bot()
    stop = asyncio.Event()
    task = asyncio.create_task(_photo_action_loop(bot, update.message.chat_id, stop))
    try:
        img_bytes = await asyncio.to_thread(generate_image, prompt)
    except Exception as e:
        stop.set()
        task.cancel()
        await update.message.reply_text(f"❌ Ошибка генерации: {e}")
        return
    stop.set()
    task.cancel()
    await bot.send_photo(
        chat_id=update.message.chat_id,
        photo=io.BytesIO(img_bytes),
        caption=f"🎨 {prompt}",
    )


async def start(update, context):
    await update.message.reply_text(
        "Привет. Я твой стратегический ревизор.\n\n"
        "• Пришли .md / .txt файлы — я соберу их как материалы.\n"
        "• Потом напиши задание (или «разбери») — проведу полный разбор.\n"
        "• Просто текст без файлов — быстрый совет.\n"
        "• /img <промпт> — сгенерирую изображение по тексту (Cloudflare).\n"
        "• /mode minimax — текст через MiniMax M3 (облако Ollama); /mode cloud — Cloudflare; /mode local — приватно через Ollama.\n"
        "Команды: /new — сбросить материалы, /help — справка."
    )


async def help_command(update, context):
    await update.message.reply_text(
        "Как пользоваться:\n"
        "1) Отправь один или несколько файлов (.md, .txt) с материалами.\n"
        "2) Отправь текст-задание или слово «разбери» (можно прямо в подписи к файлу).\n"
        "Я проанализирую всё вместе по шаблону стратегического разбора.\n"
        "/new — забыть загруженные файлы.\n"
        "/review — принудительно разобрать загруженные файлы.\n"
        "/img <промпт> — сгенерировать изображение по тексту (бесплатно, Cloudflare Workers AI).\n"
        "/mode minimax — текст через MiniMax M3 (облако Ollama, без ключа).\n"
        "/mode cloud — переключить текст на быстрый режим Cloudflare (Llama-3.3-70B).\n"
        "/mode local — вернуть текст на локальный Ollama (приватно, медленно)."
    )


async def mode_command(update, context):
    arg = update.message.text.replace("/mode", "").strip().lower()
    norm = arg.replace("x", "х").replace("ё", "е")
    if norm in ("cloud", "cf", "cloudflare", "облако"):
        if not CF_ACCOUNT_ID or not ((CF_GLOBAL_KEY and CF_EMAIL) or CF_TOKEN):
            await update.message.reply_text("⚠️ Cloudflare не настроен в .env — облачный режим недоступен.")
            return
        context.chat_data["text_mode"] = "cloud"
        await update.message.reply_text(
            "☁️ Текст теперь через Cloudflare (быстро, в рамках 10 000 нейронов/день).\n"
            "Модель: " + CF_TEXT_MODEL
        )
    elif norm in ("local", "ollama", "локально", "приватно"):
        context.chat_data["text_mode"] = "local"
        await update.message.reply_text("🖥️ Текст теперь через локальный Ollama (приватно, медленно).")
    elif "minimax" in norm or "мини" in norm or norm in ("m3", "max"):
        context.chat_data["text_mode"] = "minimax"
        await update.message.reply_text("🤖 Текст теперь через MiniMax M3 (облако Ollama, без ключа).")
    else:
        cur = context.chat_data.get("text_mode", DEFAULT_TEXT_MODE)
        await update.message.reply_text(
            f"Текущий режим текста: {cur}.\n"
            "Используй /mode minimax — MiniMax M3 (облако Ollama), /mode cloud — Cloudflare, или /mode local — локальный Ollama."
        )


async def new_command(update, context):
    context.chat_data["docs"] = []
    await update.message.reply_text("🧹 Материалы сброшены.")


async def review_command(update, context):
    docs = context.chat_data.get("docs", [])
    if not docs:
        await update.message.reply_text("Нет загруженных файлов. Сначала пришли .md / .txt материалы.")
        return
    instruction = update.message.text.replace("/review", "").strip()
    await run_review(update, context, docs, instruction)


async def run_review(update, context, docs, instruction):
    await update.message.reply_text("⏳ Анализирую материалы… (несколько минут, пожалуйста, подожди)")
    user_msg = build_review_message(docs, instruction)
    bot = update.get_bot()
    stop = asyncio.Event()
    task = asyncio.create_task(_typing_loop(bot, update.message.chat_id, stop))
    mode = context.chat_data.get("text_mode", DEFAULT_TEXT_MODE)
    answer = await asyncio.to_thread(generate_text, SYSTEM_PROMPT, user_msg, mode)
    stop.set()
    task.cancel()
    await _send_long(update, answer)


async def run_consult(update, context, text):
    await update.message.reply_text("⏳ Думаю… (несколько минут, подожди)")
    bot = update.get_bot()
    stop = asyncio.Event()
    task = asyncio.create_task(_typing_loop(bot, update.message.chat_id, stop))
    mode = context.chat_data.get("text_mode", DEFAULT_TEXT_MODE)
    answer = await asyncio.to_thread(generate_text, SYSTEM_PROMPT, text, mode)
    stop.set()
    task.cancel()
    await _send_long(update, answer)


async def handle_document(update, context):
    doc = update.message.document
    fname = (doc.file_name or "").lower()
    if not (fname.endswith(".txt") or fname.endswith(".md")):
        await update.message.reply_text("Принимаю только .txt и .md файлы.")
        return
    file = await doc.get_file()
    data = await file.download_as_bytearray()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="ignore")
    context.chat_data.setdefault("docs", []).append((doc.file_name, text))
    count = len(context.chat_data["docs"])
    if update.message.caption:
        await update.message.reply_text(f"📄 «{doc.file_name}» добавлен (всего: {count}). Анализирую с подписью…")
        await run_review(update, context, context.chat_data["docs"], update.message.caption.strip())
    else:
        await update.message.reply_text(f"📄 Добавил «{doc.file_name}» (всего материалов: {count}). Пришли задание или «разбери».")


async def handle_text(update, context):
    docs = context.chat_data.get("docs", [])
    if docs:
        await run_review(update, context, docs, update.message.text.strip())
    else:
        await run_consult(update, context, update.message.text.strip())


def main():
    if not TELEGRAM_TOKEN:
        print("❌ Не задан TELEGRAM_TOKEN в .env")
        return
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("review", review_command))
    app.add_handler(CommandHandler("img", img_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Бот запущен. Ctrl+C для остановки.")
    app.run_polling()


if __name__ == "__main__":
    main()
