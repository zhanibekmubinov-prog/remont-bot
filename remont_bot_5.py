import telebot
from telebot import types
from datetime import datetime

# ============================================================
#  НАСТРОЙКИ — ЗАПОЛНИ ЭТИ 2 СТРОКИ ПЕРЕД ЗАПУСКОМ
# ============================================================
BOT_TOKEN = "8862724348:AAGknp12i168p064YLPbOkHezuogXrdPqe4"

# Telegram ID куда отправлять готовые записи (твой личный ID)
NOTIFY_CHAT_ID = "7924916320"
# ============================================================

bot = telebot.TeleBot(BOT_TOKEN)

# Список техники из таблицы
EQUIPMENT_LIST = [
    "Блендер BL380-X1",
    "№1 Насос 882AHDE",
    "№2 Насос 12ABN",
    "№3 Насос 883AHDE",
    "№4 Насос 876AHDE",
    "№5 Насос 877AHDE",
    "№6 Насос 11ADJ06",
    "№7 Насос MTU",
    "Гидрационка 861AHDE",
    "Mercedes Actros 032YO",
    "Mercedes Actros 043YO",
    "Mercedes Actros 016YO",
    "Mercedes Actros 017YO",
    "Mercedes Actros 040YO",
    "Mercedes Actros 045YO",
    "Mercedes Actros 046YO",
    "УРАЛ ПЦА 162BG",
    "DongFeng 062BG",
    "МАЗ 030YO",
    "SHACMAN 650ADK",
    "SHACMAN 019AES",
    "SHACMAN 307ВН06",
    "SHACMAN 301ВН06",
    "УРАЛ 199ADQ06",
    "Водогрейная установка",
    "АЦН 15CYA06",
    "АЦН 16CYA06",
    "АЦН 05ADF06",
    "Трал 59ADV06",
    "Трал 28ABY06",
    "Полуприцеп 91ACS06",
    "Полуприцеп 25ACR06",
    "Тракторный прицеп УЗПТ",
]

# Хранилище состояний пользователей (в памяти)
user_data = {}

# ─────────────────────────────────────────────
#  ШАГИ: equipment → work_type → description → moto → fio → confirm
# ─────────────────────────────────────────────

def get_state(user_id):
    return user_data.get(user_id, {})

def set_state(user_id, data):
    user_data[user_id] = data

def clear_state(user_id):
    user_data.pop(user_id, None)


# ── /start ──────────────────────────────────
@bot.message_handler(commands=['start', 'new'])
def cmd_start(message):
    clear_state(message.from_user.id)
    show_equipment_list(message.chat.id, message.from_user.id)


def show_equipment_list(chat_id, user_id, edit_msg_id=None):
    set_state(user_id, {'step': 'equipment'})

    markup = types.InlineKeyboardMarkup(row_width=1)
    for eq in EQUIPMENT_LIST:
        markup.add(types.InlineKeyboardButton(eq, callback_data=f"eq:{eq}"))

    text = "🔧 *Новая запись ТО/Ремонт*\n\nШаг 1 из 5 — Выберите технику:"

    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id, parse_mode='Markdown', reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=markup)


# ── Шаг 1: выбор техники ────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("eq:"))
def cb_equipment(call):
    equipment = call.data[3:]
    state = get_state(call.from_user.id)
    state['equipment'] = equipment
    state['step'] = 'work_type'
    set_state(call.from_user.id, state)

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📋 Плановое ТО", callback_data="wt:Плановое ТО"),
        types.InlineKeyboardButton("🔴 Внеплановый ремонт (Р)", callback_data="wt:Внеплановый ремонт (Р)"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back:equipment"),
    )

    bot.edit_message_text(
        f"✅ Техника: *{equipment}*\n\nШаг 2 из 5 — Вид обслуживания:",
        call.message.chat.id,
        call.message.message_id,
        parse_mode='Markdown',
        reply_markup=markup
    )


# ── Шаг 2: вид работ ────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("wt:"))
def cb_work_type(call):
    work_type = call.data[3:]
    state = get_state(call.from_user.id)
    state['work_type'] = work_type
    state['step'] = 'description'
    set_state(call.from_user.id, state)

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back:work_type"))

    bot.edit_message_text(
        f"✅ Техника: *{state['equipment']}*\n"
        f"✅ Вид: *{work_type}*\n\n"
        f"Шаг 3 из 5 — Опишите выполненные работы:\n"
        f"_(Напишите текстом, например: Замена масляного фильтра, воздушного фильтра)_",
        call.message.chat.id,
        call.message.message_id,
        parse_mode='Markdown',
        reply_markup=markup
    )
    state['awaiting_msg_id'] = call.message.message_id
    set_state(call.from_user.id, state)


# ── Шаг 3: описание работ ───────────────────
@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'description')
def handle_description(message):
    state = get_state(message.from_user.id)
    state['description'] = message.text
    state['step'] = 'confirm_description'
    set_state(message.from_user.id, state)

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Верно", callback_data="desc:ok"),
        types.InlineKeyboardButton("✏️ Изменить", callback_data="desc:edit"),
        types.InlineKeyboardButton("⬅️ Назад", callback_data="back:description"),
    )

    bot.send_message(
        message.chat.id,
        f"Вы написали:\n\n_{message.text}_\n\nВсё верно?",
        parse_mode='Markdown',
        reply_markup=markup
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
        bot.edit_message_text(
            "Введите описание работ заново:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )
    elif action == 'ok':
        state['step'] = 'moto'
        set_state(call.from_user.id, state)

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("НП (нет показаний)", callback_data="moto:НП"),
            types.InlineKeyboardButton("⬅️ Назад", callback_data="back:description"),
        )
        bot.edit_message_text(
            f"✅ Техника: *{state['equipment']}*\n"
            f"✅ Вид: *{state['work_type']}*\n"
            f"✅ Работы: _{state['description']}_\n\n"
            f"Шаг 4 из 5 — Введите моточас / пробег:\n"
            f"_(Напишите число или нажмите кнопку «НП»)_",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=markup
        )


# ── Шаг 4: моточас / пробег ─────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("moto:"))
def cb_moto_np(call):
    state = get_state(call.from_user.id)
    state['moto'] = call.data[5:]
    state['step'] = 'fio'
    set_state(call.from_user.id, state)
    ask_fio(call.message.chat.id, call.message.message_id, state)


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'moto')
def handle_moto(message):
    state = get_state(message.from_user.id)
    state['moto'] = message.text
    state['step'] = 'fio'
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
        f"_(Напишите фамилию и инициалы)_"
    )

    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id, parse_mode='Markdown', reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=markup)


# ── Шаг 5: ФИО ──────────────────────────────
@bot.message_handler(func=lambda m: get_state(m.from_user.id).get('step') == 'fio')
def handle_fio(message):
    state = get_state(message.from_user.id)
    state['fio'] = message.text
    state['step'] = 'final_confirm'
    state['date'] = datetime.now().strftime('%d.%m.%Y')
    set_state(message.from_user.id, state)
    show_final_confirm(message.chat.id, state)


def show_final_confirm(chat_id, state):
    summary = (
        f"📋 *ИТОГОВАЯ ЗАПИСЬ*\n"
        f"{'─'*30}\n"
        f"📅 Дата: *{state['date']}*\n"
        f"🔧 Техника: *{state['equipment']}*\n"
        f"🔁 Вид работ: *{state['work_type']}*\n"
        f"📝 Описание: _{state['description']}_\n"
        f"⏱ Моточас/Пробег: *{state['moto']}*\n"
        f"👷 Исполнитель: *{state['fio']}*\n"
        f"{'─'*30}\n\n"
        f"Всё верно? Отправить запись?"
    )

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✅ Отправить", callback_data="final:send"),
        types.InlineKeyboardButton("⬅️ Назад (ФИО)", callback_data="back:fio"),
        types.InlineKeyboardButton("🔄 Начать заново", callback_data="final:restart"),
    )

    bot.send_message(chat_id, summary, parse_mode='Markdown', reply_markup=markup)


# ── Финальное подтверждение ──────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("final:"))
def cb_final(call):
    action = call.data[6:]
    state = get_state(call.from_user.id)

    if action == 'restart':
        clear_state(call.from_user.id)
        show_equipment_list(call.message.chat.id, call.from_user.id, call.message.message_id)

    elif action == 'send':
        # Форматируем запись для таблицы
        record = format_record_for_table(state)

        # Отправляем в нужный чат (ответственному человеку)
        try:
            bot.send_message(
                NOTIFY_CHAT_ID,
                f"📬 *Новая запись ТО/Ремонт*\n\n{record}",
                parse_mode='Markdown'
            )
            notify_sent = "✅ Запись отправлена ответственному лицу."
        except Exception as e:
            notify_sent = f"⚠️ Не удалось отправить уведомление: {e}"

        bot.edit_message_text(
            f"✅ *Запись сохранена!*\n\n{notify_sent}\n\n"
            f"Нажми /new чтобы добавить ещё одну запись.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown'
        )
        clear_state(call.from_user.id)


def format_record_for_table(state):
    """Форматирует данные в читаемый вид для вставки в таблицу"""
    return (
        f"```\n"
        f"Дата:           {state['date']}\n"
        f"Техника:        {state['equipment']}\n"
        f"Вид работ:      {state['work_type']}\n"
        f"Описание:       {state['description']}\n"
        f"Моточас/Пробег: {state['moto']}\n"
        f"Исполнитель:    {state['fio']}\n"
        f"```"
    )


# ── Кнопки «Назад» ───────────────────────────
@bot.callback_query_handler(func=lambda c: c.data.startswith("back:"))
def cb_back(call):
    target = call.data[5:]
    user_id = call.from_user.id
    state = get_state(user_id)
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if target == 'equipment':
        show_equipment_list(chat_id, user_id, msg_id)

    elif target == 'work_type':
        # Вернуться к выбору техники
        show_equipment_list(chat_id, user_id, msg_id)

    elif target == 'description':
        # Вернуться к выбору вида работ
        state['step'] = 'work_type'
        set_state(user_id, state)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("📋 Плановое ТО", callback_data="wt:Плановое ТО"),
            types.InlineKeyboardButton("🔴 Внеплановый ремонт (Р)", callback_data="wt:Внеплановый ремонт (Р)"),
            types.InlineKeyboardButton("⬅️ Назад", callback_data="back:equipment"),
        )
        bot.edit_message_text(
            f"✅ Техника: *{state['equipment']}*\n\nШаг 2 из 5 — Вид обслуживания:",
            chat_id, msg_id, parse_mode='Markdown', reply_markup=markup
        )

    elif target == 'moto':
        # Вернуться к описанию
        state['step'] = 'description'
        set_state(user_id, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="back:work_type"))
        bot.edit_message_text(
            f"✅ Техника: *{state['equipment']}*\n"
            f"✅ Вид: *{state['work_type']}*\n\n"
            f"Шаг 3 из 5 — Опишите выполненные работы (введите текст):",
            chat_id, msg_id, parse_mode='Markdown', reply_markup=markup
        )

    elif target == 'fio':
        # Вернуться к моточасу
        state['step'] = 'moto'
        set_state(user_id, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("НП (нет показаний)", callback_data="moto:НП"),
            types.InlineKeyboardButton("⬅️ Назад", callback_data="back:description"),
        )
        bot.edit_message_text(
            f"Шаг 4 из 5 — Введите моточас / пробег:\n_(Напишите число или нажмите «НП»)_",
            chat_id, msg_id, parse_mode='Markdown', reply_markup=markup
        )


# ── Запуск бота ─────────────────────────────
print("✅ Бот запущен! Нажми Ctrl+C чтобы остановить.")
bot.infinity_polling()
