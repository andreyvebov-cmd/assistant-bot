import os
import io
import asyncio
import base64
import json
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButtonCommands
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.constants import ParseMode

load_dotenv()

# Устойчивое к перезапускам хранилище текстов для кнопки «Сохранить в PDF»
PDF_STORE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_store.json")
pdf_seq = 0
pdf_store = {}
try:
    with open(PDF_STORE_PATH, "r", encoding="utf-8") as _f:
        pdf_store = json.load(_f)
    if isinstance(pdf_store, dict):
        for _k in list(pdf_store.keys()):
            if str(_k).isdigit():
                pdf_seq = max(pdf_seq, int(_k))
    else:
        pdf_store = {}
except Exception:
    pdf_store = {}

def _save_pdf_store():
    try:
        with open(PDF_STORE_PATH, "w", encoding="utf-8") as _f:
            json.dump(pdf_store, _f, ensure_ascii=False)
    except Exception:
        pass

# Устойчивое к перезапускам хранилище последнего ответа (для /pdf, /txt, /md без аргументов)
LAST_ANSWERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_answers.json")
last_answers = {}
try:
    with open(LAST_ANSWERS_PATH, "r", encoding="utf-8") as _f:
        last_answers = json.load(_f)
    if not isinstance(last_answers, dict):
        last_answers = {}
except Exception:
    last_answers = {}

def _save_last_answers():
    try:
        with open(LAST_ANSWERS_PATH, "w", encoding="utf-8") as _f:
            json.dump(last_answers, _f, ensure_ascii=False)
    except Exception:
        pass

TELEGRAM_TOKEN = os.getenv("ASSISTANT_TELEGRAM_TOKEN", "")

# Шрифт для кириллических PDF (лежит рядом с ботом, чтобы работать и на Linux/Render)
FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arial.ttf")

CF_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CF_EMAIL = os.getenv("CLOUDFLARE_EMAIL", "")
CF_GLOBAL_KEY = os.getenv("CLOUDFLARE_GLOBAL_KEY", "")
CF_MODEL = os.getenv("CLOUDFLARE_MODEL", "@cf/stabilityai/stable-diffusion-xl-base-1.0")
# Текстовая модель Cloudflare Workers AI
CF_TEXT_MODEL = os.getenv("CLOUDFLARE_TEXT_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
CF_VISION_MODEL = os.getenv("CLOUDFLARE_VISION_MODEL", "@cf/meta/llama-3.2-11b-vision-instruct")

SYSTEM_PROMPT = """Ты — интеллектуальный ассистент в Telegram. Твоя задача: давать точные, полезные и структурированные ответы.
ПРАВИЛА ОБЩЕНИЯ:
1. Язык: отвечай строго на том языке, на котором задан вопрос. Если язык неясен — используй русский.
2. Стиль: дружелюбный, но профессиональный. Без канцелярита и воды.
3. Формат: используй Markdown для Telegram (*жирный*, _курсив_, `код`, ```блоки кода```, • списки).
4. Длина: отвечай полно и по существу. Если тема сложная — разбей на пункты. Не обрывай ответ; давай полный ответ целиком, а не кусками по частям.
5. Неуверенность: если не знаешь точный ответ — честно скажи: «Я не уверен, но могу предположить...». Не выдумывай факты.
6. Контекст: помни историю диалога. Если пользователь уточняет — отвечай с учётом предыдущих сообщений.
7. Эмодзи: используй умеренно, для визуального разделения (📌, ✅, ⚠️, 💡), но не переборщи.
8. Инструменты: у тебя есть web_search (поиск актуальной информации в интернете), generate_image (сгенерировать картинку), get_current_datetime (узнать текущую дату и время) и make_pdf (сохранить текст в PDF-файл и отправить его).
- web_search вызывай, когда нужны АКТУАЛЬНЫЕ или проверяемые факты: выставки, новости, события, цены, биографии, «что сейчас», «какие актуальные» и т.п. Сначала найди информацию через web_search, затем сформулируй ответ на её основе.
- generate_image вызывай ТОЛЬКО если пользователь прямо попросил нарисовать/создать картинку. НИКОГДА не генерируй картинки по своей инициативе и не для приветствия.
- get_current_datetime вызывай только когда явно спрашивают время или дату.
- make_pdf вызывай, если просят сохранить ответ или текст в PDF/файл. ВАЖНО: в аргумент content ты ДОЛЖЕН сам написать полный текст (сводку/ответ), который нужно поместить в PDF — не ссылайся на «этот ответ» и не оставляй content пустым.
9. При запросах на актуальную/фактическую информацию (выставки, события, цены, новости) обязательно сначала вызови web_search. web_search возвращает СЫРЫЕ результаты поиска (заголовки+фрагменты) — это ЧЕРНОВИК, а не готовый ответ. Ты ОБЯЗАН переработать их в связный ответ на русском: назвать конкретные выставки/музеи/события и указать сайт-источник в скобках. ЗАПРЕЩЕНО выдавать сырой список заголовков поиска как ответ — это считается ошибкой. Если для точных дат/билетов данных мало — честно посоветуй проверить сайт музея/афиши. Не выдумывай фактов, которых нет в результатах. При необходимости сделай уточняющий поиск по конкретному музею.
ПРИМЕР плохого ответа: "* Культурная программа на зиму: 5 выставок... * Художественная выставка: как искусство..." (просто скопированные заголовки).
ПРИМЕР хорошего ответа: "Вот 5 значимых площадок с текущими/ближайшими выставками (по данным поиска): 1) Третьяковская галерея — ... (источник: ...). 2) Пушкинский музей — ... Точные названия и даты уточняйте на сайтах музеев, т.к. афиша обновляется."

ЗАПРЕЩЕНО:
- Приветствовать пользователя в каждом сообщении (только в первом).
- Писать сплошные стены текста без абзацев и списков (всегда разбивай на абзацы/списки).
- Использовать сложные термины без объяснения.
- Отвечать на английском, если вопрос был на русском.

Если пользователь просит код — давай рабочий пример с комментариями на русском."""

MAX_MSG = 4000
MAX_HISTORY = 24  # сколько последних сообщений хранить в контексте


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


def call_cloudflare_text(messages):
    if not CF_ACCOUNT_ID or not ((CF_GLOBAL_KEY and CF_EMAIL) or CF_TOKEN):
        return "⚠️ Cloudflare не настроен в .env (нужны CLOUDFLARE_API_TOKEN и CLOUDFLARE_ACCOUNT_ID)."
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_TEXT_MODEL}"
    headers = make_cf_headers()
    payload = {"messages": messages, "max_tokens": int(os.getenv("CF_MAX_TOKENS", "4096"))}
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


def generate_text(messages):
    return call_cloudflare_text(messages)


def call_cloudflare_vision(image_bytes, caption):
    if not CF_ACCOUNT_ID or not ((CF_GLOBAL_KEY and CF_EMAIL) or CF_TOKEN):
        return "⚠️ Cloudflare не настроен в .env (нужны CLOUDFLARE_ACCOUNT_ID и токен)."
    b64 = base64.b64encode(image_bytes).decode()
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_VISION_MODEL}"
    headers = make_cf_headers()
    headers["cf-aig-gateway-id"] = "default"
    prompt = caption.strip() if caption and caption.strip() else "Опиши кратко по-русски, что на фото."
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": int(os.getenv("CF_MAX_TOKENS", "4096")),
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=300)
        if not r.ok and r.status_code == 403 and "agree" in r.text:
            try:
                requests.post(
                    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_VISION_MODEL}",
                    headers=headers, json={"prompt": "agree"}, timeout=120,
                )
                r = requests.post(url, headers=headers, json=payload, timeout=300)
            except Exception:
                pass
        if not r.ok:
            raise RuntimeError(f"Cloudflare Vision вернул {r.status_code}: {r.text[:300]}")
        data = r.json()
        if not data.get("success", True):
            raise RuntimeError(f"Cloudflare Vision success=false: {data}")
        result = data.get("result", {})
        text = result.get("response") or result.get("content") or ""
        if not text:
            raise RuntimeError(f"Пустой ответ Cloudflare Vision: {data}")
        return text
    except Exception as e:
        return f"❌ Ошибка Cloudflare Vision: {e}"


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


# ========== РЕЕСТР ИНСТРУМЕНТОВ (SKILLS) ==========
# Чтобы добавить новый навык: напиши функцию tool_xxx(args, ctx) -> str
# и зарегистрируй её в TOOL_SCHEMAS (JSON-схема) и TOOL_FUNCS (имя -> функция).

def make_english_prompt(text):
    """Переводит запрос пользователя в детальный английский SDXL-промпт."""
    sys = ("You are an image-prompt translator. Convert the user's image request into a detailed "
           "English Stable Diffusion XL prompt. Name the main subject explicitly, add concise style, "
           "lighting and medium details. Respond with ONLY the prompt text, no quotes, no extra commentary.")
    out = call_cloudflare_chat(sys, text, 300)
    if not out:
        return text
    out = out.strip().strip('"').strip("'").strip()
    # если перевод не сработал и осталась кириллица — пробуем ещё раз жёсткой инструкцией
    if any('\u0400' <= ch <= '\u04ff' for ch in out):
        out2 = call_cloudflare_chat(
            "Translate the following request into English only. Output nothing but the English text.",
            text, 300)
        if out2 and not any('\u0400' <= ch <= '\u04ff' for ch in out2):
            out = out2.strip().strip('"').strip("'").strip()
    return out or text


def tool_generate_image(args, ctx):
    if ctx.get("_img_done"):
        return "Изображение уже сгенерировано в этом ответе; повторно не генерирую. Заверши ответ."
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return "Не задан промпт для изображения."
    text = (ctx.get("last_user_text") or "").lower()
    keys = ["нарисуй", "рисун", "картинк", "изображ", "draw", "image", "picture",
            "painting", "сгенерируй изображ", "создай картин", "аватар", "иллюстрац", "эскиз"]
    if not any(k in text for k in keys):
        return "Инструмент generate_image пропущен: в сообщении нет явной просьбы нарисовать. Ответь обычным текстом."
    # гарантируем английский SDXL-промпт: переводим, если в запросе есть кириллица
    if any('\u0400' <= ch <= '\u04ff' for ch in prompt):
        try:
            prompt = make_english_prompt(prompt)
        except Exception:
            pass
        # если после перевода осталась кириллица — SDXL её не поймёт: не генерируем ерунду
        if any('\u0400' <= ch <= '\u04ff' for ch in prompt):
            return ("Не удалось перевести промпт на английский для генерации изображения. "
                    "Напиши описание картинки по-английски или повтори позже.")
    try:
        img_bytes = generate_image(prompt)
        ctx["images"].append(("🎨 " + prompt, img_bytes))
        ctx["_img_done"] = True
        return "Изображение сгенерировано и отправлено пользователю. Больше не вызывай generate_image."
    except Exception as e:
        return f"Ошибка генерации изображения: {e}"


def tool_current_datetime(args, ctx):
    from datetime import datetime
    return "Текущие дата и время (локальные): " + datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def web_search_raw(query):
    """Возвращает список словарей {title, url, snippet} по запросу (DuckDuckGo Lite + Wikipedia RU)."""
    import re as _re
    from urllib.parse import unquote
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
    def clean(s):
        s = _re.sub(r"<[^>]+>", "", s)
        s = _re.sub(r"&[a-z]+;", " ", s)
        return s.strip()
    results = []
    try:
        r = requests.post("https://lite.duckduckgo.com/lite/", data={"q": query}, headers=headers, timeout=20)
        if r.status_code == 202:
            r = requests.post("https://lite.duckduckgo.com/lite/", data={"q": query}, headers=headers, timeout=20)
        anchors = _re.findall(r'class=[\'"]result-link[\'"]\s+href="([^"]+)"[^>]*>(.*?)</a>', r.text, _re.DOTALL)
        snips = _re.findall(r'class=[\'"]result-snippet[\'"]\s*[^>]*>(.*?)</td>', r.text, _re.DOTALL)
        for i, (href, title_html) in enumerate(anchors[:6]):
            title = clean(title_html)
            sn = clean(snips[i]) if i < len(snips) else ""
            m = _re.search(r"uddg=([^&]+)", href)
            url = unquote(m.group(1)) if m else ""
            results.append({"title": title, "url": url, "snippet": sn})
        if results:
            return results
    except Exception:
        pass
    try:
        r = requests.get("https://ru.wikipedia.org/w/api.php",
                         params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 5},
                         headers={"User-Agent": "AssistantBot/1.0"}, timeout=15)
        if r.headers.get("content-type", "").startswith("application/json"):
            d = r.json()
            for h in d.get("query", {}).get("search", [])[:5]:
                results.append({"title": h["title"], "url": "https://ru.wikipedia.org/wiki/" + h["title"].replace(" ", "_"), "snippet": clean(h["snippet"])})
            if results:
                return results
    except Exception:
        pass
    return []


def tool_web_search(args, ctx):
    query = (args.get("query") or "").strip()
    if not query:
        return "Не задан поисковый запрос."
    try:
        res = web_search_raw(query)
        if not res:
            return "По запросу ничего не найдено (попробуй переформулировать)."
        out = []
        for i, r in enumerate(res, 1):
            line = f"{i}. {r['title']}"
            if r.get("url"):
                line += f" — {r['url']}"
            if r.get("snippet"):
                line += f"\n   {r['snippet']}"
            out.append(line)
        return "\n".join(out)
    except Exception as e:
        return f"Ошибка поиска: {e}"


def tool_make_pdf(args, ctx):
    content = (args.get("content") or "").strip()
    if not content:
        content = (ctx.get("last_assistant_text") or "").strip()
    title = (args.get("title") or "document").strip() or "document"
    if not content:
        return "Не передан текст для PDF. Укажи текст в запросе или попроси бота ответить, затем напиши «сохрани это в PDF»."
    try:
        from fpdf import FPDF
        import io as _io
        import re as _re
        pdf = FPDF()
        pdf.add_font("ArialCyr", "", FONT_PATH)
        pdf.add_page()
        pdf.set_font("ArialCyr", size=12)
        pdf.multi_cell(0, 6, content)
        buf = _io.BytesIO()
        pdf.output(buf)
        data = buf.getvalue()
        safe = _re.sub(r"[^A-Za-z0-9 _-]", "", title).strip().replace(" ", "_") or "document"
        ctx["files"].append((safe + ".pdf", data))
        return "PDF-файл сформирован и отправлен пользователю."
    except Exception as e:
        return f"Ошибка создания PDF: {e}"


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Сгенерировать ОДНО изображение по запросу пользователя. В аргумент prompt запиши ТО ЖЕ, что попросил нарисовать пользователь, переведя на английский (если было не по-английски). СТРОГО сохраняй главный объект/сюжет из запроса пользователя: попросили черепашку -> prompt='a turtle' (допустимо 'a turtle on a beach'), НЕ подменяй другим предметом (не заменяй на цветы/натюрморт и т.п.). Можно добавить краткий стиль/освещение, но не меняй объект. Инструмент сам переведёт кириллицу в английский. Не вызывай повторно.",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "string", "description": "сюжет из запроса пользователя на английском, например 'a turtle on a beach'. Не придумывай другой объект."}},
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_datetime",
            "description": "Узнать текущие дату и время на устройстве пользователя. Используй, если спрашивают «который час» или «какое сегодня число».",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_pdf",
            "description": "Сформировать PDF-файл из текста и отправить пользователю. Используй, если просят «сохрани в PDF», «сделай PDF», «выгрузи ответ файлом».",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "название файла без расширения"},
                    "content": {"type": "string", "description": "текст, который нужно поместить в PDF"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Поиск актуальной информации в интернете (факты, новости, события, выставки и т.п.). Возвращает СЫРЫЕ результаты поиска (заголовки и фрагменты). Ты ОБЯЗАН переработать их в связный ответ, а не копировать заголовки как есть. Вызывай, когда нужны свежие или проверяемые данные, которых нет в твоей памяти.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "поисковый запрос на русском или английском"}},
                "required": ["query"],
            },
        },
    },
]

TOOL_FUNCS = {
    "generate_image": tool_generate_image,
    "get_current_datetime": tool_current_datetime,
    "make_pdf": tool_make_pdf,
    "web_search": tool_web_search,
}


def call_cloudflare_agent(messages, tool_ctx):
    if not CF_ACCOUNT_ID or not ((CF_GLOBAL_KEY and CF_EMAIL) or CF_TOKEN):
        return "⚠️ Cloudflare не настроен в .env (нужны CLOUDFLARE_API_TOKEN и CLOUDFLARE_ACCOUNT_ID)."
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1/chat/completions"
    headers = make_cf_headers()
    headers["cf-aig-gateway-id"] = "default"
    work = [dict(m) for m in messages]
    last_content = ""
    for _ in range(5):
        payload = {
            "model": CF_TEXT_MODEL,
            "messages": work,
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
            "max_tokens": int(os.getenv("CF_MAX_TOKENS", "4096")),
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=300)
            if not r.ok:
                raise RuntimeError(f"Cloudflare вернул {r.status_code}: {r.text[:300]}")
            data = r.json()
            msg = data["choices"][0]["message"]
        except Exception as e:
            return f"❌ Ошибка Cloudflare: {e}"
        if not msg.get("tool_calls"):
            return msg.get("content") or ""
        last_content = msg.get("content") or ""
        work.append({
            "role": "assistant",
            "content": last_content,
            "tool_calls": msg["tool_calls"],
        })
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except Exception:
                args = {}
            func = TOOL_FUNCS.get(name)
            try:
                result = func(args, tool_ctx) if func else f"Неизвестный инструмент: {name}"
            except Exception as e:
                result = f"Ошибка инструмента {name}: {e}"
            work.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
        if tool_ctx.get("images"):
            return last_content
    return last_content or "Извини, не смог завершить цепочку инструментов за отведённое число шагов. Попробуй переформулировать запрос."


def call_cloudflare_chat(system, user, max_tokens=2000):
    """Прямой запрос к Cloudflare chat-completions без инструментов (для синтеза)."""
    if not CF_ACCOUNT_ID or not ((CF_GLOBAL_KEY and CF_EMAIL) or CF_TOKEN):
        return None
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1/chat/completions"
    headers = make_cf_headers()
    payload = {
        "model": CF_TEXT_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.4,
        "stream": False,
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=180)
        if r.ok:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return None


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
        context.chat_data["pending_img"] = True
        await update.message.reply_text(
            "Напиши описание для картинки СЛЕДУЮЩИМ сообщением — сгенерирую по нему.\n"
            "Или сразу пиши вместе с командой: /img <описание>"
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
        "Привет! Я твой ИИ-ассистент в Telegram (работаю через Cloudflare).\n\n"
        "• Пиши мне что угодно — отвечу на твоём языке.\n"
        "• Я помню контекст нашего диалога.\n"
        "• Напиши «нарисуй ...» — пришлю ОДНУ картинку (Cloudflare SDXL).\n"
        "• /img <промпт> — сгенерирую изображение по тексту.\n"
        "• /pdf — соберу ПОСЛЕДНИЙ ответ в PDF. /pdf <текст> — сохраню указанный текст.\n"
        "• Отправь фото — опишу его через Cloudflare Vision.\n"
        "• Я агент: сам вызову инструменты (поиск в интернете, картинка, время, PDF), если нужно.\n"
        "• /new — забыть историю диалога.\n"
        "• /help — справка."
    )
    try:
        await context.bot.set_chat_menu_button(chat_id=update.effective_chat.id, menu_button=MenuButtonCommands())
    except Exception:
        pass


async def help_command(update, context):
    await update.message.reply_text(
        "Как пользоваться:\n"
        "• Просто пришли сообщение — отвечу с учётом истории (через Cloudflare).\n"
        "• «нарисуй <что>» или /img <промпт> — сгенерирую картинку (одну за раз).\n"
        "• /pdf — сохранит последний ответ. Ответь (цитатой) на ЛЮБОЕ сообщение командой /pdf — сохранит именно его. /pdf <текст> — сохранит указанный текст. Под каждым ответом есть кнопка «📄 Сохранить в PDF».\n"
        "• /txt и /md — сохранят последний ответ (или ответ на сообщение, или свой текст) в файл .txt/.md, который можно переслать другому боту (например, для анализа).\n"
        "• 📷 Отправь фото — опишу через Cloudflare Vision.\n"
        "• 🤖 Я агент: сам вызываю инструменты (web_search, generate_image, get_current_datetime, make_pdf).\n"
        "• /new — забыть историю переписки.\n"
        "Модель текста: " + CF_TEXT_MODEL
    )


async def new_command(update, context):
    context.chat_data["history"] = []
    context.chat_data.pop("pending_img", None)
    context.chat_data.pop("pending_research", None)
    await update.message.reply_text("🧹 История диалога сброшена.")


async def pdf_command(update, context):
    chat_id = update.message.chat_id
    replied = update.message.reply_to_message
    if replied and getattr(replied, "text", None):
        text = replied.text.strip()
    else:
        text = " ".join(context.args).strip()
        if text:
            low = text.lower()
            if any(w in low for w in ["последн", "прошл", "предыдущ"]):
                text = last_answers.get(str(chat_id), "").strip()
                if not text:
                    await update.message.reply_text(
                        "Нет сохранённого ответа. Сначала попроси бота ответить на что-нибудь, "
                        "затем отправь /pdf (без текста) — сохраню последний ответ."
                    )
                    return
        else:
            text = last_answers.get(str(chat_id), "").strip()
    if not text:
        await update.message.reply_text(
            "Нечего сохранять в PDF. Варианты:\n"
            "• Ответь (цитатой) на нужное сообщение командой /pdf — сохраню именно его.\n"
            "• /pdf — сохраню последний ответ бота.\n"
            "• /pdf <текст> — сохраню указанный текст."
        )
        return
    await update.message.reply_text("⏳ Собираю PDF…")
    try:
        data = make_pdf_bytes(text)
        await update.get_bot().send_document(chat_id=chat_id, document=io.BytesIO(data), filename=safe_pdf_filename(text), caption="📄 PDF готов")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка создания PDF: {e}")


def _transliterate(s):
    table = {
        'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'i',
        'к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f',
        'х':'h','ц':'c','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya',
    }
    return "".join(table.get(ch, ch) for ch in s.lower())


def safe_text_filename(text, ext):
    import re as _re
    base = _transliterate(text[:30] or "document")
    return (_re.sub(r"[^A-Za-z0-9 _-]", "", base).strip().replace(" ", "_") or "document") + "." + ext


async def _resolve_save_text(update, context):
    chat_id = update.message.chat_id
    replied = update.message.reply_to_message
    if replied and getattr(replied, "text", None):
        text = replied.text.strip()
    else:
        text = " ".join(context.args).strip()
        if text:
            low = text.lower()
            if any(w in low for w in ["последн", "прошл", "предыдущ"]):
                text = last_answers.get(str(chat_id), "").strip()
                if not text:
                    await update.message.reply_text(
                        "Нет сохранённого ответа. Сначала попроси бота ответить на что-нибудь, "
                        "затем отправь /txt (без текста) — сохраню последний ответ."
                    )
                    return None
        else:
            text = last_answers.get(str(chat_id), "").strip()
    if not text:
        await update.message.reply_text(
            "Нечего сохранять. Варианты:\n"
            "• Ответь (цитатой) на нужное сообщение командой /txt — сохраню именно его.\n"
            "• /txt — сохраню последний ответ бота.\n"
            "• /txt <текст> — сохраню указанный текст."
        )
        return None
    return text


async def _send_text_file(update, text, ext, caption):
    chat_id = update.message.chat_id
    await update.message.reply_text("⏳ Собираю файл…")
    try:
        data = text.encode("utf-8")
        await update.get_bot().send_document(
            chat_id=chat_id,
            document=io.BytesIO(data),
            filename=safe_text_filename(text, ext),
            caption=caption,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка создания файла: {e}")


async def txt_command(update, context):
    text = await _resolve_save_text(update, context)
    if text is None:
        return
    await _send_text_file(update, text, "txt", "📄 TXT готов")


async def md_command(update, context):
    text = await _resolve_save_text(update, context)
    if text is None:
        return
    await _send_text_file(update, text, "md", "📄 MD готов")


async def research_command(update, context):
    replied = update.message.reply_to_message
    if replied and getattr(replied, "text", None):
        topic = replied.text.strip()
    else:
        topic = " ".join(context.args).strip()
    if not topic:
        context.chat_data["pending_research"] = True
        await update.message.reply_text("🔎 Напиши тему для исследования — я найду информацию, синтезирую и пришлю .md-файл.")
        return
    await _do_research(update, context, topic)


async def _do_research(update, context, topic):
    import io as _io
    chat_id = update.message.chat_id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await update.message.reply_text(f"🔎 Ищу информацию по теме: *{topic}* …")
    queries = [topic, f"{topic} обзор", f"{topic} что важно знать"]
    results = []
    seen = set()
    for q in queries:
        for r in web_search_raw(q):
            u = r.get("url", "")
            if u and u not in seen:
                seen.add(u)
                results.append(r)
        if len(results) >= 8:
            break
    if not results:
        await update.message.reply_text("Не удалось ничего найти в сети. Попробуй переформулировать тему.")
        return
    src = "\n\n".join(
        f"[{i+1}] {r['title']} ({r.get('url','')})\n{r.get('snippet','')}"
        for i, r in enumerate(results)
    )
    system = (
        "Ты — аналитик-исследователь. На основе предоставленных результатов поиска составь "
        "структурированный Markdown-документ на русском языке по теме. Обязательно используй заголовки (##), "
        "списки и выдели блок TL;DR в начале. В конце обязательно добавь раздел '## Источники' и перечисли "
        "все ссылки из результатов поиска. НЕ выдумывай факты, которых нет в результатах поиска; если данных "
        "мало — честно укажи это. Формат — чистый Markdown без обёртки ```."
    )
    user = f"Тема исследования: {topic}\n\nРезультаты поиска:\n{src}\n\nСоставь исследовательский пакет."
    md = call_cloudflare_chat(system, user, max_tokens=3000)
    if not md:
        await update.message.reply_text("❌ Не удалось синтезировать результат (ошибка Cloudflare). Попробуй позже.")
        return
    bio = _io.BytesIO(md.encode("utf-8"))
    bio.seek(0)
    await context.bot.send_document(chat_id=chat_id, document=bio, filename=safe_text_filename(topic, "md"), caption="📄 Исследовательский пакет готов")


def _pdf_bytes_from(text):
    from fpdf import FPDF
    import io as _io
    pdf = FPDF()
    pdf.add_font("ArialCyr", "", r"C:\Windows\Fonts\arial.ttf")
    pdf.add_page()
    pdf.set_font("ArialCyr", size=12)
    pdf.multi_cell(0, 6, text)
    buf = _io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def make_pdf_bytes(text):
    text = text or ""
    try:
        return _pdf_bytes_from(text)
    except Exception:
        # убираем символы вне BMP и непечатные управляющие — пробуем ещё раз
        cleaned = "".join(
            c for c in text
            if ord(c) < 0xFFFF and (c in "\n\r\t " or ord(c) >= 32)
        )
        return _pdf_bytes_from(cleaned)


def safe_pdf_filename(text):
    import re as _re
    base = _transliterate(text[:30] or "document")
    return (_re.sub(r"[^A-Za-z0-9 _-]", "", base).strip().replace(" ", "_") or "document") + ".pdf"


async def send_with_pdf_button(update, text):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    global pdf_seq
    parts = split_text(text)
    n = len(parts)
    for i, part in enumerate(parts):
        if i == n - 1:
            pdf_seq += 1
            seq = pdf_seq
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📄 Сохранить в PDF", callback_data=f"pdf:{seq}")]])
            await update.message.reply_text(part, reply_markup=kb)
            pdf_store[str(seq)] = text
            _save_pdf_store()
        else:
            await update.message.reply_text(part)


async def pdf_button_callback(update, context):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if not data.startswith("pdf:"):
        return
    mid = data[4:]
    chat_id = q.message.chat_id
    import sys as _sys
    _sys.stderr.write(f"[BTN] data={data!r} chat={chat_id} has_store={mid in pdf_store} la_len={len(last_answers.get(str(chat_id), ''))}\n")
    _sys.stderr.flush()
    text = pdf_store.get(mid, "").strip()
    if not text:
        text = last_answers.get(str(chat_id), "").strip()
    if not text:
        await q.edit_message_text(q.message.text + "\n\n(не удалось найти текст для PDF — возможно, бот перезапускался)")
        return
    try:
        data_bytes = make_pdf_bytes(text)
        bot = q.get_bot()
        await bot.send_document(chat_id=chat_id, document=io.BytesIO(data_bytes), filename=safe_pdf_filename(text), caption="📄 PDF готов")
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        await q.edit_message_text(q.message.text + f"\n\n❌ Ошибка PDF: {e}")


async def handle_text(update, context):
    user_text = update.message.text.strip()
    if not user_text:
        return
    if user_text.startswith("/"):
        context.chat_data.pop("pending_img", None)
        context.chat_data.pop("pending_research", None)
        return
    if context.chat_data.get("pending_img"):
        context.chat_data.pop("pending_img", None)
        if not CF_ACCOUNT_ID or not ((CF_GLOBAL_KEY and CF_EMAIL) or CF_TOKEN):
            await update.message.reply_text(
                "⚠️ Cloudflare не настроен в .env. Нужны CLOUDFLARE_API_TOKEN "
                "(или CLOUDFLARE_EMAIL + CLOUDFLARE_GLOBAL_KEY) и CLOUDFLARE_ACCOUNT_ID."
            )
            return
        await update.message.reply_text("⏳ Генерирую изображение…")
        bot = update.get_bot()
        stop = asyncio.Event()
        task = asyncio.create_task(_photo_action_loop(bot, update.message.chat_id, stop))
        try:
            img_bytes = await asyncio.to_thread(generate_image, user_text)
        except Exception as e:
            stop.set()
            task.cancel()
            await update.message.reply_text(f"❌ Ошибка генерации: {e}")
            return
        stop.set()
        task.cancel()
        await bot.send_photo(chat_id=update.message.chat_id, photo=io.BytesIO(img_bytes), caption=f"🎨 {user_text}")
        return
    if context.chat_data.get("pending_research"):
        context.chat_data.pop("pending_research", None)
        await _do_research(update, context, user_text)
        return
    history = context.chat_data.get("history", [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_text}]
    last_assistant = ""
    for m in reversed(history):
        if m.get("role") == "assistant":
            last_assistant = m.get("content", "")
            break
    await update.message.reply_text("⏳ Думаю…")
    bot = update.get_bot()
    stop = asyncio.Event()
    task = asyncio.create_task(_typing_loop(bot, update.message.chat_id, stop))
    tool_ctx = {"images": [], "files": [], "last_user_text": user_text, "last_assistant_text": last_assistant}
    answer = await asyncio.to_thread(call_cloudflare_agent, messages, tool_ctx)
    stop.set()
    task.cancel()
    for cap, img in tool_ctx.get("images", []):
        try:
            await bot.send_photo(chat_id=update.message.chat_id, photo=io.BytesIO(img), caption=cap)
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось отправить изображение: {e}")
    for fname, fdata in tool_ctx.get("files", []):
        try:
            await bot.send_document(chat_id=update.message.chat_id, document=io.BytesIO(fdata), filename=fname)
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось отправить файл: {e}")
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": answer})
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    context.chat_data["history"] = history
    last_answers[str(update.message.chat_id)] = answer
    _save_last_answers()
    if answer:
        await send_with_pdf_button(update, answer)
    elif not tool_ctx.get("images") and not tool_ctx.get("files"):
        await update.message.reply_text("Извини, не получил ответ. Попробуй переформулировать запрос.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photos = update.message.photo
        photo = photos[-1]
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()
        caption = update.message.caption or ""
        await update.message.reply_text("👁 Распознаю изображение…")
        description = await asyncio.to_thread(call_cloudflare_vision, bytes(image_bytes), caption)
        for part in split_text(description):
            await update.message.reply_text(part)
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось обработать фото: {e}")


async def _post_init(application):
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "начать работу"),
            BotCommand("help", "справка по командам"),
            BotCommand("new", "сбросить историю диалога"),
            BotCommand("img", "сгенерировать картинку по описанию"),
            BotCommand("pdf", "сохранить последний ответ в PDF"),
            BotCommand("txt", "сохранить последний ответ в .txt"),
            BotCommand("md", "сохранить последний ответ в .md"),
            BotCommand("research", "исследовать тему и прислать .md-файл"),
        ])
    except Exception:
        pass


def _start_health_server():
    # Мини-HTTP-сервер для Render (Web Service ждёт ответ на $PORT).
    # Запускается только если задана переменная PORT (на Render она есть, локально — нет).
    import http.server
    import socketserver
    port = int(os.getenv("PORT", "8080"))
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *a):
            pass
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), _H) as httpd:
        httpd.serve_forever()


def main():
    if not TELEGRAM_TOKEN:
        print("Не задан ASSISTANT_TELEGRAM_TOKEN в .env")
        return
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(_post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_command))
    app.add_handler(CommandHandler("img", img_command))
    app.add_handler(CommandHandler("pdf", pdf_command))
    app.add_handler(CommandHandler("txt", txt_command))
    app.add_handler(CommandHandler("md", md_command))
    app.add_handler(CommandHandler("research", research_command))
    app.add_handler(CallbackQueryHandler(pdf_button_callback, pattern="^pdf:"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    if os.getenv("PORT"):
        try:
            import threading
            threading.Thread(target=_start_health_server, daemon=True).start()
        except Exception:
            pass
    print("Бот запущен. Ctrl+C для остановки.")
    app.run_polling(allowed_updates=["message", "callback_query", "edited_message", "channel_post"])


if __name__ == "__main__":
    main()
