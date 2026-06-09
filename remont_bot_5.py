"""
remont_bot_5.py — Telegram бот учёта ТО и ремонтов
Автоматически записывает данные в Google Sheets

ТОЧНАЯ КАРТА КОЛОНОК (из реального файла реестра):
─────────────────────────────────────────────────────────
Строки 1–9  = шапка документа (НЕ ТРОГАЕМ)
Строка 10+  = данные (бот добавляет новые строки сюда)

A  = Дата выполнения                  ← из бота: дата (auto)
B  = Вид обслуживания (ТО, Р)         ← из бота: work_type
C  = Наименование/содержание работ    ← из бота: description (краткое)
D  = Исполнитель подрядчик            ← всегда "Подрядчик"
E  = Описание работ (подробно)        ← из бота: description (полное)
F  = Текущий показатель счётчика      ← из бота: moto
G  = Дата начала работ                ← из бота: дата (auto)
H  = Дата конец работ                 ← из бота: дата (auto)
I  = Продолжительность ремонта        ← всегда "1 день"
J  = Тех. характеристики / замечания  ← пусто (заполняют вручную)
K  = Запчасти план (P/N)              ← пусто (заполняют вручную)
L  = Запчасти факт (P/N)              ← пусто (заполняют вручную)
M  = Место нахождения на складе       ← пусто (заполняют вручную)
N  = Место хранения запчастей         ← "Склад у подрядчика"
O  = Примечания                       ← пусто
P  = ФИО, подписавший исполнитель     ← из бота: fio
Q  = ФИО подписавший заказчик         ← из бота: fio
─────────────────────────────────────────────────────────
"""

# ══════════════════════════════════════════════════════════════════
# ИМПОРТЫ
# ══════════════════════════════════════════════════════════════════
import os
import json
import logging
import requests
from datetime import datetime

import telebot
from telebot import types

import gspread
from google.oauth2.service_account import Credentials


# ══════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ — сообщения видны в панели Railway → Logs
# ══════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# СЕКРЕТЫ — читаем из Railway Variables, не хардкодим в код
# ══════════════════════════════════════════════════════════════════
BOT_TOKEN               = os.environ.get("BOT_TOKEN")
GOOGLE_SHEET_ID         = os.environ.get("GOOGLE_SHEET_ID")
NOTIFY_CHAT_ID          = os.environ.get("NOTIFY_CHAT_ID")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
ANTHROPIC_API_KEY       = os.environ.get("ANTHROPIC_API_KEY")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан в переменных окружения")
if not GOOGLE_SHEET_ID:
    raise ValueError("❌ GOOGLE_SHEET_ID не задан в переменных окружения")
if not GOOGLE_CREDENTIALS_JSON:
    raise ValueError("❌ GOOGLE_CREDENTIALS_JSON не задан в переменных окружения")


# ══════════════════════════════════════════════════════════════════
# СПИСОК ТЕХНИКИ
# ══════════════════════════════════════════════════════════════════
# Names match EXACTLY the real Google Sheet tab names (including spaces)
EQUIPMENT_LIST = [
    "Блендер",
    "№1 Насос 882AHDE ",
    "№2 Насос 12ABN ",
    "№3 Насос 883AHDE",
    "№4 Насос  876AHDE",
    "№5 Насос  877AHDE",
    "№7 Насос MTU",
    "№6 Насос  11ADJ06",
    "Гидроционка  861AHDE",
    "Mercedes Actros 032YO",
    "Mercedes Actros 043YO",
    "УРАЛ ПЦА 162BG",
    "DongFeng 062BG",
    "МАЗ 030YO",
    "Mercedes Actros 016YO",
    "Mercedes Actros 017YO",
    "Mercedes Actros 040YO",
    "Mercedes Actros 045YO",
    "Mercedes Actros 046YO",
    "SHACMAN 650ADK",
    "SHACMAN 019AES",
    "SHACMAN 307ВН06",
    "SHACMAN 301ВН06",
    "УРАЛ 199ADQ06",
    "Водогрейная установка ",
    "АЦН 15CYA06",
    "АЦН 16CYA06 ",
    "АЦН 05ADF06",
    "Трал 59ADV06",
    "Трал 28ABY06",
    "Полуприцеп 91ACS06",
    "Полуприцеп 25ACR06",
    "Тракторный прицеп УЗПТ",
]


# ══════════════════════════════════════════════════════════════════
# GOOGLE SHEETS — ПОДКЛЮЧЕНИЕ И ЗАПИСЬ
# ══════════════════════════════════════════════════════════════════

def get_google_client():
    """Создаёт авторизованное подключение к Google Sheets API."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def find_sheet_for_equipment(spreadsheet, equipment_name: str):
    """
    Ищет нужный лист (вкладку) в таблице по имени техники.

    Логика поиска (3 уровня, от точного к приблизительному):

    1. ТОЧНОЕ совпадение — название листа == название техники
       "№3 Насос 883AHDE" == "№3 Насос 883AHDE" ✅

    2. УНИКАЛЬНЫЙ КОД — последнее слово имени техники (самое уникальное)
       "№2 Насос 12ABN" → ищем "12ABN" в названии листа
       Это решает проблему когда несколько техник содержат слово "Насос"

    3. ЗАПАСНОЙ — первый лист если ничего не нашли
    """
    all_sheets = spreadsheet.worksheets()
    sheet_names = [s.title for s in all_sheets]
    logger.info(f"Доступные листы: {sheet_names}")

    # ── Уровень 1: точное совпадение ──────────────────────────────────────
    for sheet in all_sheets:
        if sheet.title.strip() == equipment_name.strip():
            logger.info(f"✅ Точное совпадение: '{sheet.title}'")
            return sheet

    # ── Уровень 2: поиск по уникальному коду (последнее слово) ────────────
    # Например "№2 Насос 12ABN" → уникальный код = "12ABN"
    # Это самое специфичное слово в имени техники
    words = equipment_name.split()
    # Берём последнее слово — оно обычно самое уникальное (код модели)
    unique_code = words[-1] if words else ""

    if unique_code and len(unique_code) > 2:
        for sheet in all_sheets:
            if unique_code.lower() in sheet.title.lower():
                logger.info(
                    f"✅ Совпадение по уникальному коду '{unique_code}': '{sheet.title}'"
                )
                return sheet

    # Также пробуем предпоследнее слово если последнее не дало результата
    if len(words) >= 2:
        second_code = words[-2]
        if len(second_code) > 2:
            for sheet in all_sheets:
                if second_code.lower() in sheet.title.lower():
                    logger.info(
                        f"✅ Совпадение по коду '{second_code}': '{sheet.title}'"
                    )
                    return sheet

    # ── Уровень 3: запасной вариант ────────────────────────────────────────
    logger.warning(
        f"⚠️ Лист для '{equipment_name}' не найден среди {sheet_names}. "
        f"Используется первый лист: '{all_sheets[0].title}'"
    )
    return all_sheets[0]


def write_to_google_sheets(data: dict) -> tuple:
    """
    Записывает одну строку данных в Google Sheets.

    СТРУКТУРА РЕЕСТРА (из реального файла компании):
      Строки 1-9  = шапка документа (НЕ ТРОГАЕМ НИКОГДА)
      Строка 10+  = данные (добавляем сюда)

    ЛОГИКА:
      Находим последнюю заполненную строку в колонке A,
      добавляем новую строку сразу после неё.
      Минимум — строка 10 (никогда не выше).

    Возвращает (True, имя_листа) или (False, текст_ошибки)
    """
    try:
        client = get_google_client()
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        sheet = find_sheet_for_equipment(spreadsheet, data["equipment"])

        today = datetime.now().strftime("%d.%m.%Y")

        # ── Найти первую пустую строку после шапки ───────────────────────
        # Шапка = строки 1-9. Данные начинаются с строки 10.
        HEADER_ROWS = 9

        # Читаем все значения колонки A
        col_a = sheet.col_values(1)

        # Последняя заполненная строка = длина списка
        # Новая строка = следующая после последней, но не раньше строки 10
        last_filled = len(col_a)
        next_row = max(last_filled + 1, HEADER_ROWS + 1)

        logger.info(
            f"Последняя строка с данными: {last_filled}, "
            f"Новая запись пойдёт в строку: {next_row}"
        )

        # ── Точная карта колонок A→Q (реестр компании) ───────────────────
        # A  = Дата выполнения
        # B  = Вид обслуживания (ТО / Р)
        # C  = Наименование/содержание работ
        # D  = Исполнитель (Подрядчик)
        # E  = Описание выполненных работ (подробно)
        # F  = Текущий показатель счётчика (моточасы)
        # G  = Дата начала
        # H  = Дата конец
        # I  = Продолжительность ремонта
        # J  = Тех. хар., рекомендации (заполняют вручную)
        # K  = Запчасти план P/N (заполняют вручную)
        # L  = Запчасти факт P/N (заполняют вручную)
        # M  = Место нахождения на складе (заполняют вручную)
        # N  = Место хранения запчастей
        # O  = Примечания (заполняют вручную)
        # P  = ФИО подписавший исполнитель
        # Q  = ФИО подписавший заказчик
        row = [
            today,                        # A — Дата выполнения
            data.get("work_type", ""),    # B — Вид обслуживания (ТО / Р)
            data.get("description", ""),  # C — Наименование/содержание работ
            "Подрядчик",                  # D — Исполнитель
            data.get("description", ""),  # E — Описание выполненных работ
            data.get("moto", "НП"),       # F — Текущий показатель счётчика
            today,                        # G — Дата начала
            today,                        # H — Дата конец
            "1 день",                     # I — Продолжительность
            "",                           # J — Тех. хар. (вручную)
            data.get('parts_old', 'НП'),   # K — Запчасти план P/N (снята)
            data.get('parts_new', 'НП'),   # L — Запчасти факт P/N (установлена)
            "",                           # M — Место на складе (вручную)
            "Склад у подрядчика",         # N — Место хранения
            "",                           # O — Примечания (вручную)
            data.get("fio", ""),          # P — ФИО исполнитель
            data.get("fio", ""),          # Q — ФИО заказчик
        ]

        # Записываем в конкретную строку — не сдвигает существующие данные
        sheet.update(
            f"A{next_row}:Q{next_row}",
            [row],
            value_input_option="USER_ENTERED"
        )

        logger.info(
            f"✅ Записано в '{sheet.title}' строка {next_row}: "
            f"дата={today}, вид={data.get('work_type')}, "
            f"техника={data.get('equipment')}, мото={data.get('moto')}"
        )
        return True, sheet.title

    except gspread.exceptions.SpreadsheetNotFound:
        msg = "Таблица не найдена. Проверь GOOGLE_SHEET_ID."
        logger.error(f"❌ {msg}")
        return False, msg

    except gspread.exceptions.APIError as e:
        msg = f"Ошибка Google API: {e}"
        logger.error(f"❌ {msg}")
        return False, msg

    except Exception as e:
        msg = str(e)
        logger.error(f"❌ Неожиданная ошибка при записи: {e}", exc_info=True)
        return False, msg



# ══════════════════════════════════════════════════════════════════
# СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ (в памяти)
# ══════════════════════════════════════════════════════════════════
user_data = {}

def get_state(user_id):   return user_data.get(user_id, {})
def set_state(user_id, d): user_data[user_id] = d
def clear_state(user_id): user_data.pop(user_id, None)



# ══════════════════════════════════════════════════════════════════
# Q&A FEATURE — AI-POWERED SPREADSHEET ASSISTANT
# ══════════════════════════════════════════════════════════════════

# Track which users are in Q&A mode
qa_mode_users = set()


def get_all_sheet_data(equipment_hint=None):
    """
    Reads data from Google Sheets.
    If equipment_hint is provided, reads just that tab.
    Otherwise reads all tabs (for broad questions).
    Returns a formatted string of the data.
    """
    try:
        client = get_google_client()
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        all_sheets = spreadsheet.worksheets()

        # Skip non-equipment sheets
        skip_sheets = ['sheet1', 'пустая форма']
        equipment_sheets = [
            s for s in all_sheets
            if s.title.lower() not in skip_sheets
        ]

        # If user mentioned specific equipment, find that tab
        if equipment_hint:
            hint_lower = equipment_hint.lower()
            for sheet in equipment_sheets:
                if hint_lower in sheet.title.lower():
                    return read_single_sheet(sheet)
            # Try word by word
            for word in equipment_hint.split():
                if len(word) > 2:
                    for sheet in equipment_sheets:
                        if word.lower() in sheet.title.lower():
                            return read_single_sheet(sheet)

        # No specific equipment — read last 5 entries from each sheet
        # (to avoid sending too much data to AI)
        all_data = []
        for sheet in equipment_sheets[:10]:  # limit to 10 sheets max
            try:
                rows = sheet.get_all_values()
                # Data starts at row 10 (index 9), get last 3 entries
                data_rows = [r for r in rows[9:] if any(v.strip() for v in r)]
                if data_rows:
                    last_rows = data_rows[-3:]
                    all_data.append(f"\n=== {sheet.title} ===")
                    for row in last_rows:
                        # Format: date | work_type | description | moto | fio
                        date = row[0] if len(row) > 0 else ""
                        wtype = row[1] if len(row) > 1 else ""
                        desc = row[4] if len(row) > 4 else ""
                        moto = row[5] if len(row) > 5 else ""
                        fio = row[15] if len(row) > 15 else ""
                        status = row[13] if len(row) > 13 else ""
                        all_data.append(
                            f"  Дата: {date} | Вид: {wtype} | "
                            f"Работы: {desc} | Мото: {moto} | "
                            f"Исполнитель: {fio} | Состояние: {status}"
                        )
            except Exception:
                continue

        return "\n".join(all_data) if all_data else "Данные не найдены"

    except Exception as e:
        logger.error(f"Error reading sheet for QA: {e}")
        return f"Ошибка чтения таблицы: {e}"


def read_single_sheet(sheet):
    """Read all data rows from one sheet tab."""
    try:
        rows = sheet.get_all_values()
        # Data starts at row 10 (index 9)
        data_rows = [r for r in rows[9:] if any(v.strip() for v in r)]

        if not data_rows:
            return f"В листе {sheet.title} нет записей."

        formatted = [f"=== {sheet.title} (все записи) ==="]
        for i, row in enumerate(data_rows, 1):
            date  = row[0]  if len(row) > 0  else ""
            wtype = row[1]  if len(row) > 1  else ""
            basis = row[2]  if len(row) > 2  else ""
            cond  = row[3]  if len(row) > 3  else ""
            desc  = row[4]  if len(row) > 4  else ""
            moto  = row[5]  if len(row) > 5  else ""
            dstart= row[6]  if len(row) > 6  else ""
            dend  = row[7]  if len(row) > 7  else ""
            dur   = row[8]  if len(row) > 8  else ""
            parts_old = row[10] if len(row) > 10 else ""
            parts_new = row[11] if len(row) > 11 else ""
            status = row[13] if len(row) > 13 else ""
            fio   = row[15] if len(row) > 15 else ""
            fio2  = row[16] if len(row) > 16 else ""

            formatted.append(
                f"Запись {i}: Дата={date} | Вид={wtype} | "
                f"Основание={basis} | Состояние до={cond} | "
                f"Описание={desc} | Моточасы={moto} | "
                f"Начало={dstart} | Конец={dend} | Длит={dur} | "
                f"Запчасти план={parts_old} | Запчасти факт={parts_new} | "
                f"Состояние после={status} | Исполнитель={fio} | "
                f"Контролирующий={fio2}"
            )
        return "\n".join(formatted)
    except Exception as e:
        return f"Ошибка чтения листа {sheet.title}: {e}"


def detect_equipment_in_question(question):
    """
    Try to detect which equipment the user is asking about.
    Returns a search hint or None if the question is general.
    """
    question_lower = question.lower()

    # Map of common keywords/shorthand to equipment identifiers
    keywords = {
        "блендер": "Блендер",
        "blender": "Блендер",
        "насос 1": "882AHDE",
        "насос1": "882AHDE",
        "pump 1": "882AHDE",
        "882": "882AHDE",
        "насос 2": "12ABN",
        "насос2": "12ABN",
        "12abn": "12ABN",
        "насос 3": "883AHDE",
        "насос3": "883AHDE",
        "883": "883AHDE",
        "pump 3": "883AHDE",
        "насос 4": "876AHDE",
        "876": "876AHDE",
        "насос 4": "876AHDE",
        "насос 5": "877AHDE",
        "877": "877AHDE",
        "насос 6": "11ADJ06",
        "11adj": "11ADJ06",
        "насос 7": "MTU",
        "mtu": "MTU",
        "гидро": "861AHDE",
        "гидрационка": "861AHDE",
        "гидроционка": "861AHDE",
        "032": "032YO",
        "мерс 032": "032YO",
        "043": "043YO",
        "мерс 043": "043YO",
        "016": "016YO",
        "мерс 016": "016YO",
        "017": "017YO",
        "мерс 017": "017YO",
        "040": "040YO",
        "045": "045YO",
        "046": "046YO",
        "урал пца": "162BG",
        "162": "162BG",
        "dongfeng": "DongFeng",
        "донгфенг": "DongFeng",
        "062": "062BG",
        "маз": "МАЗ",
        "maz": "МАЗ",
        "030": "030YO",
        "650adk": "650ADK",
        "шакман 650": "650ADK",
        "shacman 650": "650ADK",
        "019aes": "019AES",
        "шакман 019": "019AES",
        "307": "307ВН06",
        "301": "301ВН06",
        "урал 199": "199ADQ06",
        "199adq": "199ADQ06",
        "водогрей": "Водогрейная",
        "водогрейная": "Водогрейная",
        "ацн 15": "15CYA06",
        "15cya": "15CYA06",
        "ацн 16": "16CYA06",
        "16cya": "16CYA06",
        "ацн 05": "05ADF06",
        "05adf": "05ADF06",
        "трал 59": "59ADV06",
        "59adv": "59ADV06",
        "трал 28": "28ABY06",
        "28aby": "28ABY06",
        "полуприцеп 91": "91ACS06",
        "91acs": "91ACS06",
        "полуприцеп 25": "25ACR06",
        "25acr": "25ACR06",
        "тракторный": "УЗПТ",
        "узпт": "УЗПТ",
    }

    for keyword, hint in keywords.items():
        if keyword in question_lower:
            return hint

    return None


def ask_claude(question, spreadsheet_data):
    """
    Sends the user question + spreadsheet data to Claude AI.
    Returns Claude's answer as a string.
    """
    if not ANTHROPIC_API_KEY:
        return (
            "❌ AI не настроен. "
            "Добавьте ANTHROPIC_API_KEY в переменные окружения Railway."
        )

    system_prompt = """Ты — помощник по техническому обслуживанию оборудования компании ТОО «Caspian Integrated Services».

У тебя есть доступ к реестру технического обслуживания и ремонтных работ компании.
Твоя задача — отвечать на вопросы сотрудников о состоянии оборудования, истории обслуживания и ремонтов.

ПРАВИЛА:
1. Отвечай ТОЛЬКО на основе предоставленных данных из реестра. Не придумывай ничего.
2. Если данных нет — честно скажи что информация не найдена.
3. Отвечай на том же языке на котором задан вопрос (русский или английский).
4. Будь конкретным — называй даты, имена, типы работ из реальных данных.
5. Если вопрос непонятен — попроси уточнить.
6. Форматируй ответ кратко и понятно для работника на телефоне.
7. Понимай сокращения: ТО = техническое обслуживание, Р = ремонт, НП = нет показаний.
8. Понимай неточные названия: "насос 3" = "№3 Насос 883AHDE", "мерс 032" = "Mercedes Actros 032YO" и т.д."""

    user_message = f"""Данные из реестра технического обслуживания:

{spreadsheet_data}

Вопрос сотрудника: {question}

Ответь на вопрос на основе данных выше."""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_message}
                ],
            },
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        else:
            logger.error(f"Anthropic API error: {response.status_code} {response.text}")
            return f"❌ Ошибка AI ({response.status_code}). Попробуйте позже."

    except requests.Timeout:
        return "❌ AI не ответил вовремя. Попробуйте снова."
    except Exception as e:
        logger.error(f"Error calling Claude API: {e}")
        return f"❌ Ошибка: {e}"


# ── Q&A Bot Handlers ─────────────────────────────────────────────

bot = telebot.TeleBot(BOT_TOKEN)


# ══════════════════════════════════════════════════════════════════
# PERSISTENT MENU BUTTON (always visible at the bottom of the chat)
# This runs once when the bot starts and sets up the menu
# ══════════════════════════════════════════════════════════════════
def setup_bot_commands():
    """
    Sets up the bot menu commands visible when user taps the / button.
    Also sets a persistent keyboard button at the bottom of the chat.
    """
    try:
        commands = [
            types.BotCommand("/new",    "📋 Новая запись ТО/Ремонт"),
            types.BotCommand("/ask",    "🔍 Задать вопрос об оборудовании"),
            types.BotCommand("/help",   "❓ Помощь"),
            types.BotCommand("/cancel", "❌ Отменить текущую запись"),
        ]
        bot.set_my_commands(commands)
        logger.info("✅ Команды бота установлены")
    except Exception as e:
        logger.warning(f"Не удалось установить команды: {e}")


def get_main_menu_keyboard():
    """
    Persistent reply keyboard — always visible at the bottom.
    User sees this button every time they open the bot.
    """
    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    markup.row(
        types.KeyboardButton("📋 Новая запись ТО/Ремонт"),
        types.KeyboardButton("🔍 Задать вопрос")
    )
    return markup


# ══════════════════════════════════════════════════════════════════
# ШАГ 0: /start — приветствие с кнопкой
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start'])
def cmd_start(message):
    """
    Welcome message shown when user first opens the bot.
    Shows a friendly greeting + big inline button + persistent menu button.
    """
    clear_state(message.from_user.id)

    # Inline buttons in the message — both options clearly visible
    inline_markup = types.InlineKeyboardMarkup(row_width=1)
    inline_markup.add(
        types.InlineKeyboardButton(
            "📋 Начать новую запись",
            callback_data="start:new"
        ),
        types.InlineKeyboardButton(
            "🔍 Задать вопрос об оборудовании",
            callback_data="start:ask"
        )
    )

    welcome_text = (
        "👷 *Добро пожаловать в бот учёта ТО и ремонтов*\n"
        "🏢 ТОО «Caspian Integrated Services»\n\n"
        "Этот бот помогает быстро записать информацию "
        "о проведённом техническом обслуживании или ремонте.\n\n"
        "Данные автоматически сохраняются в реестр компании.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📋 *Новая запись* — добавить ТО или ремонт\n"
        "🔍 *Задать вопрос* — узнать историю обслуживания"
    )

    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard(),
    )
    bot.send_message(
        message.chat.id,
        "Выберите действие:",
        reply_markup=inline_markup,
    )


@bot.callback_query_handler(func=lambda c: c.data == "start:new")
def cb_start_new(call):
    """User tapped the New Entry button."""
    clear_state(call.from_user.id)
    bot.delete_message(call.message.chat.id, call.message.message_id)
    show_equipment_list(call.message.chat.id, call.from_user.id)


@bot.callback_query_handler(func=lambda c: c.data == "start:ask")
def cb_start_ask(call):
    """User tapped the Ask Question button."""
    bot.delete_message(call.message.chat.id, call.message.message_id)
    # Reuse the QA button handler
    qa_mode_users.add(call.from_user.id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("❌ Отменить", callback_data="qa:cancel"))
    bot.send_message(
        call.message.chat.id,
        "🤖 *Режим вопросов и ответов*\n\n"
        "Задайте любой вопрос об оборудовании.\n\n"
        "Например:\n"
        "• _Когда последний раз чинили насос 883?_\n"
        "• _Какое состояние Mercedes 032YO?_\n"
        "• _Кто обслуживал SHACMAN 650?_\n\n"
        "Напишите ваш вопрос:",
        parse_mode='Markdown',
        reply_markup=markup
    )


# ══════════════════════════════════════════════════════════════════
# /new command AND persistent button handler
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['new'])
def cmd_new(message):
    clear_state(message.from_user.id)
    show_equipment_list(message.chat.id, message.from_user.id)


@bot.message_handler(func=lambda m: m.text == "📋 Новая запись ТО/Ремонт")
def handle_menu_button(message):
    """Handles the persistent menu button tap."""
    clear_state(message.from_user.id)
    show_equipment_list(message.chat.id, message.from_user.id)


# ══════════════════════════════════════════════════════════════════
# /help command
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['help'])
def cmd_help(message):
    help_text = (
        "❓ *Помощь*\n\n"
        "Этот бот записывает данные о ТО и ремонтах в реестр компании.\n\n"
        "*Как использовать:*\n"
        "1. Нажмите 📋 *Новая запись ТО/Ремонт*\n"
        "2. Выберите технику из списка\n"
        "3. Выберите вид работ (ТО или Р)\n"
        "4. Опишите выполненные работы\n"
        "5. Введите моточасы или пробег\n"
        "6. Введите ваше ФИО\n"
        "7. Нажмите ✅ *Отправить* — данные сохранятся автоматически\n\n"
        "*Команды:*\n"
        "/new — начать новую запись\n"
        "/cancel — отменить текущую запись\n"
        "/help — показать эту справку\n\n"
        "*Проблемы?* Обратитесь к администратору."
    )
    bot.send_message(
        message.chat.id,
        help_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard(),
    )


# ══════════════════════════════════════════════════════════════════
# /cancel command
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "❌ Запись отменена.\n\nНажмите 📋 *Новая запись* чтобы начать заново.",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard(),
    )


@bot.message_handler(commands=['ask'])
def cmd_ask(message):
    """Handle /ask command."""
    handle_qa_button(message)


@bot.message_handler(func=lambda m: m.text == "🔍 Задать вопрос")
def handle_qa_button(message):
    """User pressed the Q&A button."""
    qa_mode_users.add(message.from_user.id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("❌ Отменить", callback_data="qa:cancel"))
    bot.send_message(
        message.chat.id,
        "🤖 *Режим вопросов и ответов*\n\n"
        "Задайте любой вопрос об оборудовании на русском или английском.\n\n"
        "Например:\n"
        "• _Когда последний раз чинили насос 883?_\n"
        "• _Какое состояние Mercedes 032YO?_\n"
        "• _Кто обслуживал SHACMAN 650ADK?_\n"
        "• _When was the last service on pump 3?_\n\n"
        "Напишите ваш вопрос:",
        parse_mode='Markdown',
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda c: c.data == "qa:cancel")
def cb_qa_cancel(call):
    """User cancelled Q&A mode."""
    qa_mode_users.discard(call.from_user.id)
    bot.edit_message_text(
        "❌ Режим вопросов отменён.",
        call.message.chat.id,
        call.message.message_id
    )


@bot.message_handler(func=lambda m: m.from_user.id in qa_mode_users)
def handle_qa_question(message):
    """
    This handles any text message from a user who is in Q&A mode.
    1. Detects which equipment they are asking about
    2. Reads relevant data from Google Sheets
    3. Asks Claude AI to answer the question
    4. Sends the answer back
    """
    user_id = message.from_user.id
    question = message.text

    # Show typing indicator
    bot.send_chat_action(message.chat.id, 'typing')

    # Send "searching..." message
    searching_msg = bot.send_message(
        message.chat.id,
        "🔍 Ищу информацию в реестре...",
    )

    try:
        # Step 1: Detect which equipment the question is about
        equipment_hint = detect_equipment_in_question(question)
        logger.info(f"QA question: '{question}' | Equipment hint: {equipment_hint}")

        # Step 2: Read relevant data from Google Sheets
        bot.edit_message_text(
            "📊 Читаю данные из таблицы...",
            message.chat.id,
            searching_msg.message_id
        )
        sheet_data = get_all_sheet_data(equipment_hint)

        # Step 3: Ask Claude AI
        bot.edit_message_text(
            "🤖 AI анализирует данные...",
            message.chat.id,
            searching_msg.message_id
        )
        answer = ask_claude(question, sheet_data)

        # Step 4: Send the answer with buttons
        answer_markup = types.InlineKeyboardMarkup(row_width=2)
        answer_markup.add(
            types.InlineKeyboardButton(
                "🔍 Задать ещё вопрос", callback_data="qa:another"
            ),
            types.InlineKeyboardButton(
                "📋 Новая запись", callback_data="start:new"
            )
        )

        # Delete the "searching" message
        bot.delete_message(message.chat.id, searching_msg.message_id)

        # Send the answer
        bot.send_message(
            message.chat.id,
            f"🤖 *Ответ:*\n\n{answer}",
            parse_mode='Markdown',
            reply_markup=answer_markup
        )

    except Exception as e:
        logger.error(f"QA error: {e}", exc_info=True)
        bot.edit_message_text(
            f"❌ Произошла ошибка: {e}\n\nПопробуйте ещё раз.",
            message.chat.id,
            searching_msg.message_id
        )

    finally:
        # Remove user from QA mode after answering
        qa_mode_users.discard(user_id)


@bot.callback_query_handler(func=lambda c: c.data == "qa:another")
def cb_qa_another(call):
    """User wants to ask another question."""
    qa_mode_users.add(call.from_user.id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("❌ Отменить", callback_data="qa:cancel"))
    bot.edit_message_text(
        "🤖 Задайте следующий вопрос:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup
    )

# ══════════════════════════════════════════════════════════════════
# СОЗДАНИЕ БОТА
# ══════════════════════════════════════════════════════════════════
def show_equipment_list(chat_id, user_id, edit_msg_id=None):
    set_state(user_id, {'step': 'equipment'})

    markup = types.InlineKeyboardMarkup(row_width=1)
    for eq in EQUIPMENT_LIST:
        markup.add(types.InlineKeyboardButton(eq, callback_data=f"eq:{eq}"))

    text = "🔧 *Новая запись ТО/Ремонт*\n\nШаг 1 из 5 — Выберите технику:"

    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id,
                              parse_mode='Markdown', reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=markup)


# ══════════════════════════════════════════════════════════════════
# ШАГ 1: Выбор техники
# ══════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("eq:"))
def cb_equipment(call):
    equipment = call.data[3:]
    state = get_state(call.from_user.id)
    state.update({'equipment': equipment, 'step': 'work_type'})
    set_state(call.from_user.id, state)

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📋 ТО — Плановое техобслуживание",
                                   callback_data="wt:ТО"),
        types.InlineKeyboardButton("🔴 Р — Внеплановый ремонт",
                                   callback_data="wt:Р"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back:equipment"),
    )
    bot.edit_message_text(
        f"✅ Техника: *{equipment}*\n\nШаг 2 из 5 — Вид обслуживания:",
        call.message.chat.id, call.message.message_id,
        parse_mode='Markdown', reply_markup=markup
    )


# ══════════════════════════════════════════════════════════════════
# ШАГ 2: Вид работ (ТО / Р)
# ══════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("wt:"))
def cb_work_type(call):
    work_type = call.data[3:]
    state = get_state(call.from_user.id)
    state.update({'work_type': work_type, 'step': 'description'})
    set_state(call.from_user.id, state)

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back:work_type"))

    bot.edit_message_text(
        f"✅ Техника: *{state['equipment']}*\n"
        f"✅ Вид: *{work_type}*\n\n"
        f"Шаг 3 из 5 — Опишите выполненные работы:\n"
        f"_(Например: Замена масляного фильтра, воздушного фильтра)_",
        call.message.chat.id, call.message.message_id,
        parse_mode='Markdown', reply_markup=markup
    )
    state['awaiting_msg_id'] = call.message.message_id
    set_state(call.from_user.id, state)


# ══════════════════════════════════════════════════════════════════
# ШАГ 3: Описание работ (текстовый ввод)
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'description')
def handle_description(message):
    state = get_state(message.from_user.id)
    state.update({'description': message.text, 'step': 'confirm_description'})
    set_state(message.from_user.id, state)

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Верно",    callback_data="desc:ok"),
        types.InlineKeyboardButton("✏️ Изменить", callback_data="desc:edit"),
        types.InlineKeyboardButton("⬅️ Назад",    callback_data="back:description"),
    )
    bot.send_message(
        message.chat.id,
        f"Вы написали:\n\n_{message.text}_\n\nВсё верно?",
        parse_mode='Markdown', reply_markup=markup
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("desc:"))
def cb_description_confirm(call):
    action = call.data[5:]
    state = get_state(call.from_user.id)

    if action == 'edit':
        state['step'] = 'description'
        set_state(call.from_user.id, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back:work_type"))
        bot.edit_message_text("Введите описание работ заново:",
                              call.message.chat.id, call.message.message_id,
                              reply_markup=markup)

    elif action == 'ok':
        state['step'] = 'moto'
        set_state(call.from_user.id, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("НП (нет показаний)", callback_data="moto:НП"),
            types.InlineKeyboardButton("⬅️ Назад",           callback_data="back:description"),
        )
        bot.edit_message_text(
            f"✅ Техника: *{state['equipment']}*\n"
            f"✅ Вид: *{state['work_type']}*\n"
            f"✅ Работы: _{state['description']}_\n\n"
            f"Шаг 4 из 5 — Введите моточас / пробег:\n"
            f"_(Напишите число, например: 16 500 — или нажмите «НП»)_",
            call.message.chat.id, call.message.message_id,
            parse_mode='Markdown', reply_markup=markup
        )


# ══════════════════════════════════════════════════════════════════
# ШАГ 4: Моточасы / пробег
# ══════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("moto:"))
def cb_moto_np(call):
    state = get_state(call.from_user.id)
    state.update({'moto': call.data[5:], 'step': 'parts'})
    set_state(call.from_user.id, state)
    ask_parts(call.message.chat.id, call.message.message_id, state)


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'moto')
def handle_moto(message):
    state = get_state(message.from_user.id)
    state.update({'moto': message.text, 'step': 'parts'})
    set_state(message.from_user.id, state)
    ask_parts(message.chat.id, None, state)


def ask_parts(chat_id, edit_msg_id, state):
    """Ask about spare parts used — fills columns K and L in the sheet."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(
            "НП — запчасти не использовались",
            callback_data="parts:np"
        ),
        types.InlineKeyboardButton(
            "✏️ Ввести P/N запчастей",
            callback_data="parts:enter"
        ),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back:parts"),
    )
    text = (
        f"✅ Техника: *{state['equipment']}*\n"
        f"✅ Вид: *{state['work_type']}*\n"
        f"✅ Работы: _{state['description']}_\n"
        f"✅ Моточас/Пробег: *{state['moto']}*\n\n"
        f"Шаг 5 из 6 — Запчасти / материалы:\n\n"
        f"Были ли использованы запчасти или материалы?"
    )
    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id,
                              parse_mode='Markdown', reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("parts:"))
def cb_parts(call):
    action = call.data[6:]
    state = get_state(call.from_user.id)

    if action == 'np':
        # No parts used — write НП to both K and L
        state.update({
            'parts_old': 'НП',
            'parts_new': 'НП',
            'step': 'fio'
        })
        set_state(call.from_user.id, state)
        ask_fio(call.message.chat.id, call.message.message_id, state)

    elif action == 'enter':
        # User wants to enter part numbers
        state['step'] = 'parts_old'
        set_state(call.from_user.id, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back:parts"))
        bot.edit_message_text(
            f"🔧 *Запчасти — старая деталь (снята)*\n\n"
            f"Введите P/N и серийный номер СНЯТОЙ детали.\n"
            f"_(Например: FF256 LF17473)_\n\n"
            f"Если номер неизвестен — напишите: `НП`",
            call.message.chat.id, call.message.message_id,
            parse_mode='Markdown', reply_markup=markup
        )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'parts_old')
def handle_parts_old(message):
    """User entered the old/removed part number."""
    state = get_state(message.from_user.id)
    state.update({'parts_old': message.text, 'step': 'parts_new'})
    set_state(message.from_user.id, state)

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            "Такой же номер (как старая)", callback_data="parts_new:same"
        ),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back:parts_old"),
    )
    bot.send_message(
        message.chat.id,
        f"✅ Старая деталь: *{message.text}*\n\n"
        f"🔧 *Запчасти — новая деталь (установлена)*\n\n"
        f"Введите P/N и серийный номер НОВОЙ установленной детали.\n"
        f"_(Или нажмите кнопку если номер такой же)_",
        parse_mode='Markdown', reply_markup=markup
    )


@bot.callback_query_handler(func=lambda c: c.data == "parts_new:same")
def cb_parts_new_same(call):
    """New part has same number as old part."""
    state = get_state(call.from_user.id)
    state.update({
        'parts_new': state.get('parts_old', 'НП'),
        'step': 'fio'
    })
    set_state(call.from_user.id, state)
    ask_fio(call.message.chat.id, call.message.message_id, state)


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'parts_new')
def handle_parts_new(message):
    """User entered the new/installed part number."""
    state = get_state(message.from_user.id)
    state.update({'parts_new': message.text, 'step': 'fio'})
    set_state(message.from_user.id, state)
    ask_fio(message.chat.id, None, state)


def ask_fio(chat_id, edit_msg_id, state):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back:moto"))
    text = (
        f"✅ Техника: *{state['equipment']}*\n"
        f"✅ Вид: *{state['work_type']}*\n"
        f"✅ Работы: _{state['description']}_\n"
        f"✅ Моточас/Пробег: *{state['moto']}*\n\n"
        f"Шаг 6 из 6 — ФИО исполнителя:\n"
        f"_(Фамилия и инициалы, например: Иванов И.И.)_"
    )
    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id,
                              parse_mode='Markdown', reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=markup)


# ══════════════════════════════════════════════════════════════════
# ШАГ 5: ФИО исполнителя
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'fio')
def handle_fio(message):
    state = get_state(message.from_user.id)
    state.update({
        'fio':  message.text,
        'date': datetime.now().strftime('%d.%m.%Y'),
        'step': 'final_confirm',
    })
    set_state(message.from_user.id, state)
    show_final_confirm(message.chat.id, state)


def show_final_confirm(chat_id, state):
    """Итоговая сводка перед отправкой — пользователь видит что запишется."""
    summary = (
        f"📋 *ИТОГОВАЯ ЗАПИСЬ*\n"
        f"{'─'*32}\n"
        f"📅 Дата:           *{state['date']}*\n"
        f"🔧 Техника:        *{state['equipment']}*\n"
        f"🔁 Вид работ:      *{state['work_type']}*\n"
        f"📝 Описание:       _{state['description']}_\n"
        f"⏱ Моточас/Пробег: *{state['moto']}*\n"
        f"🔩 Запчасти (снята): *{state.get('parts_old', 'НП')}*\n"
        f"🔩 Запчасти (уст.):  *{state.get('parts_new', 'НП')}*\n"
        f"👷 Исполнитель:    *{state['fio']}*\n"
        f"{'─'*32}\n\n"
        f"Нажми *✅ Отправить* — данные запишутся в таблицу."
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Отправить в таблицу", callback_data="final:send"),
        types.InlineKeyboardButton("⬅️ Исправить ФИО",       callback_data="back:fio"),
        types.InlineKeyboardButton("🔄 Начать заново",        callback_data="final:restart"),
    )
    bot.send_message(chat_id, summary, parse_mode='Markdown', reply_markup=markup)


# ══════════════════════════════════════════════════════════════════
# ФИНАЛ: ОТПРАВКА В GOOGLE SHEETS
# ══════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("final:"))
def cb_final(call):
    action = call.data[6:]
    state  = get_state(call.from_user.id)

    if action == 'restart':
        clear_state(call.from_user.id)
        show_equipment_list(call.message.chat.id, call.from_user.id,
                            call.message.message_id)
        return

    if action == 'send':
        # 1. Показываем "сохраняю..." пока идёт запрос к Google
        bot.edit_message_text(
            "⏳ Сохраняю запись в Google Sheets...",
            call.message.chat.id, call.message.message_id
        )

        # 2. ★ ЗАПИСЫВАЕМ В ТАБЛИЦУ ★
        success, detail = write_to_google_sheets(state)

        if success:
            # 3а. Успех — показываем результат с кнопкой новой записи
            success_markup = types.InlineKeyboardMarkup()
            success_markup.add(
                types.InlineKeyboardButton(
                    "📋 Добавить ещё одну запись",
                    callback_data="start:new"
                )
            )
            bot.edit_message_text(
                f"✅ *Запись добавлена в таблицу!*\n\n"
                f"📊 Лист: _{detail}_\n"
                f"📅 Дата: {state['date']}\n"
                f"🔧 Техника: {state['equipment']}\n"
                f"🔁 Вид: {state['work_type']}\n\n"
                f"Нажмите кнопку ниже чтобы добавить ещё одну запись.",
                call.message.chat.id, call.message.message_id,
                parse_mode='Markdown',
                reply_markup=success_markup
            )

            # 4. Уведомление в группу менеджера (если задан NOTIFY_CHAT_ID)
            if NOTIFY_CHAT_ID:
                try:
                    bot.send_message(
                        NOTIFY_CHAT_ID,
                        f"📬 *Новая запись в реестре*\n\n"
                        f"📅 {state['date']} | 🔧 {state['equipment']}\n"
                        f"🔁 {state['work_type']} | ⏱ {state['moto']}\n"
                        f"📝 {state['description']}\n"
                        f"👷 {state['fio']}",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.warning(f"Уведомление не отправлено: {e}")
        else:
            # 3б. Ошибка — показываем причину с кнопкой попробовать снова
            error_markup = types.InlineKeyboardMarkup()
            error_markup.add(
                types.InlineKeyboardButton(
                    "🔄 Попробовать снова",
                    callback_data="start:new"
                )
            )
            bot.edit_message_text(
                f"❌ *Ошибка при сохранении.*\n\n"
                f"`{detail}`\n\n"
                f"Нажмите кнопку ниже чтобы попробовать снова.",
                call.message.chat.id, call.message.message_id,
                parse_mode='Markdown',
                reply_markup=error_markup
            )

        clear_state(call.from_user.id)


# ══════════════════════════════════════════════════════════════════
# КНОПКИ «НАЗАД»
# ══════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("back:"))
def cb_back(call):
    target = call.data[5:]
    uid    = call.from_user.id
    state  = get_state(uid)
    cid    = call.message.chat.id
    mid    = call.message.message_id

    if target in ('equipment', 'work_type'):
        show_equipment_list(cid, uid, mid)

    elif target == 'description':
        state['step'] = 'work_type'
        set_state(uid, state)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📋 ТО — Плановое техобслуживание",
                                       callback_data="wt:ТО"),
            types.InlineKeyboardButton("🔴 Р — Внеплановый ремонт",
                                       callback_data="wt:Р"),
            types.InlineKeyboardButton("⬅️ Назад", callback_data="back:equipment"),
        )
        bot.edit_message_text(
            f"✅ Техника: *{state['equipment']}*\n\nШаг 2 из 5 — Вид обслуживания:",
            cid, mid, parse_mode='Markdown', reply_markup=markup
        )

    elif target == 'moto':
        state['step'] = 'description'
        set_state(uid, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️ Назад",
                                              callback_data="back:work_type"))
        bot.edit_message_text(
            f"✅ Техника: *{state['equipment']}*\n"
            f"✅ Вид: *{state['work_type']}*\n\n"
            f"Шаг 3 из 5 — Опишите выполненные работы (введите текст):",
            cid, mid, parse_mode='Markdown', reply_markup=markup
        )

    elif target == 'fio':
        state['step'] = 'moto'
        set_state(uid, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("НП (нет показаний)",
                                       callback_data="moto:НП"),
            types.InlineKeyboardButton("⬅️ Назад",
                                       callback_data="back:description"),
        )
        bot.edit_message_text(
            f"Шаг 4 из 5 — Введите моточас / пробег:\n"
            f"_(Напишите число или нажмите «НП»)_",
            cid, mid, parse_mode='Markdown', reply_markup=markup
        )


# ══════════════════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════════════════
logger.info("🤖 Запускаю бота...")

# Проверка подключения к Google Sheets при старте
try:
    _client = get_google_client()
    _ss     = _client.open_by_key(GOOGLE_SHEET_ID)
    _tabs   = [s.title for s in _ss.worksheets()]
    logger.info(f"✅ Google Sheets подключён. Таблица: '{_ss.title}'")
    logger.info(f"   Листы: {_tabs}")
except Exception as _e:
    logger.error(f"❌ Нет подключения к Google Sheets: {_e}")
    logger.error("   Проверь GOOGLE_CREDENTIALS_JSON и GOOGLE_SHEET_ID")

# Set up bot commands and menu
setup_bot_commands()

logger.info("✅ Бот готов. Нажми Ctrl+C чтобы остановить.")
bot.infinity_polling(skip_pending=True, none_stop=True, interval=1)
