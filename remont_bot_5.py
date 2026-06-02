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
            "",                           # K — Запчасти план (вручную)
            "",                           # L — Запчасти факт (вручную)
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
# СОЗДАНИЕ БОТА
# ══════════════════════════════════════════════════════════════════
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
            types.BotCommand("/new",   "📋 Новая запись ТО/Ремонт"),
            types.BotCommand("/help",  "❓ Помощь"),
            types.BotCommand("/cancel","❌ Отменить текущую запись"),
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
        persistent=True,
        is_persistent=True,
    )
    markup.add(types.KeyboardButton("📋 Новая запись ТО/Ремонт"))
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

    # Inline button in the message
    inline_markup = types.InlineKeyboardMarkup()
    inline_markup.add(
        types.InlineKeyboardButton(
            "📋 Начать новую запись",
            callback_data="start:new"
        )
    )

    welcome_text = (
        "👷 *Добро пожаловать в бот учёта ТО и ремонтов*
"
        "🏢 ТОО «Caspian Integrated Services»

"
        "Этот бот помогает быстро записать информацию "
        "о проведённом техническом обслуживании или ремонте.

"
        "Данные автоматически сохраняются в реестр компании.

"
        "━━━━━━━━━━━━━━━━━━━━
"
        "Нажмите кнопку ниже чтобы начать:"
    )

    bot.send_message(
        message.chat.id,
        welcome_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard(),
    )
    bot.send_message(
        message.chat.id,
        "⬇️",
        reply_markup=inline_markup,
    )


@bot.callback_query_handler(func=lambda c: c.data == "start:new")
def cb_start_new(call):
    """User tapped the inline Start button."""
    clear_state(call.from_user.id)
    bot.delete_message(call.message.chat.id, call.message.message_id)
    show_equipment_list(call.message.chat.id, call.from_user.id)


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
        "❓ *Помощь*

"
        "Этот бот записывает данные о ТО и ремонтах в реестр компании.

"
        "*Как использовать:*
"
        "1. Нажмите 📋 *Новая запись ТО/Ремонт*
"
        "2. Выберите технику из списка
"
        "3. Выберите вид работ (ТО или Р)
"
        "4. Опишите выполненные работы
"
        "5. Введите моточасы или пробег
"
        "6. Введите ваше ФИО
"
        "7. Нажмите ✅ *Отправить* — данные сохранятся автоматически

"
        "*Команды:*
"
        "/new — начать новую запись
"
        "/cancel — отменить текущую запись
"
        "/help — показать эту справку

"
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
        "❌ Запись отменена.

Нажмите 📋 *Новая запись* чтобы начать заново.",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard(),
    )


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
    state.update({'moto': call.data[5:], 'step': 'fio'})
    set_state(call.from_user.id, state)
    ask_fio(call.message.chat.id, call.message.message_id, state)


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'moto')
def handle_moto(message):
    state = get_state(message.from_user.id)
    state.update({'moto': message.text, 'step': 'fio'})
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
        f"Шаг 5 из 5 — ФИО исполнителя:\n"
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
            # 3а. Успех
            bot.edit_message_text(
                f"✅ *Запись добавлена в таблицу!*\n\n"
                f"📊 Лист: _{detail}_\n"
                f"📅 Дата: {state['date']}\n"
                f"🔧 Техника: {state['equipment']}\n"
                f"🔁 Вид: {state['work_type']}\n\n"
                f"Нажми /new чтобы добавить ещё одну запись.",
                call.message.chat.id, call.message.message_id,
                parse_mode='Markdown'
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
            # 3б. Ошибка — показываем причину
            bot.edit_message_text(
                f"❌ *Ошибка при сохранении.*\n\n"
                f"`{detail}`\n\n"
                f"Попробуй ещё раз: /new\n"
                f"Или сообщи администратору.",
                call.message.chat.id, call.message.message_id,
                parse_mode='Markdown'
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
