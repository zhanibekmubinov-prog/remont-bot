"""
remont_bot_5.py — Telegram бот учёта ТО и ремонтов
ТОО «Caspian Integrated Services»

Записывает ТО и ремонты в реестр ТО платформы CIS (бланк F-002).
Интерфейс: русский / қазақша (переключается кнопкой).

═══════════════════════════════════════════════════════════════════
ЧТО ИЗМЕНИЛОСЬ В ЭТОЙ ВЕРСИИ (2026-10-29)
═══════════════════════════════════════════════════════════════════
1. GOOGLE-ТАБЛИЦЫ БОЛЬШЕ НЕТ. Реестр ТО перенесён в платформу CIS
   (Техника → машина → Реестр ТО), таблица больше не читается.
   Бот берёт список техники из платформы и пишет запись туда же,
   через API платформы (/api/v1/equipment-service) по ключу бота.
   Новая техника появляется в боте сама, когда её завели в платформе
   (кэш 5 минут, либо кнопка «🔄 Обновить список»).

2. ПРОВЕРКИ — КАК У ФОРМЫ В ПЛАТФОРМЕ. Дата не в будущем, показание
   счётчика не меньше прошлого. Если платформа не приняла запись —
   бот показывает её объяснение и даёт повторить, введённое не теряется.
   Показание бот спрашивает как раньше («моточас / пробег»), а счётчик
   выбирает платформа: основной у машины. Если у машины счётчик не
   задан — показание не теряется, уходит в примечание записи, и бот
   об этом предупреждает.

3. РЕЖИМ ВОПРОСОВ читает реестр ТО платформы (последние записи по
   технике или обзор по всей технике).

4. Диалог, кнопки, языки и уведомление менеджеру — прежние. В реестр
   всё пишется по-русски («ТО»/«Р», «1 день»), как и раньше.

═══════════════════════════════════════════════════════════════════
ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (Railway → Variables)
═══════════════════════════════════════════════════════════════════
BOT_TOKEN                 — токен бота от @BotFather              (обязательно)
PLATFORM_URL              — адрес платформы CIS, например
                            https://cis-platform.up.railway.app   (обязательно)
PLATFORM_KEY              — ключ бота: тот же, что в платформе
                            «Админ-панель → Интеграции → Ключ
                            Telegram-бота» (общий с ботом «Статус
                            техники»)                              (обязательно)
NOTIFY_CHAT_ID            — чат для уведомлений менеджеру         (опционально)
ANTHROPIC_API_KEY         — ключ для режима вопросов              (опционально)
EQUIPMENT_CACHE_TTL       — сколько секунд кэшировать список техники (по умолч. 300)
DEFAULT_LANG              — язык по умолчанию: ru или kz (по умолч. ru)
TIMEZONE                  — часовой пояс даты записи (по умолч. Asia/Atyrau)

GOOGLE_SHEET_ID, GOOGLE_CREDENTIALS_JSON и SKIP_SHEETS больше не нужны —
их можно удалить из Variables.

═══════════════════════════════════════════════════════════════════
ЧТО ИДЁТ В ЗАПИСЬ РЕЕСТРА (колонки F-002)
═══════════════════════════════════════════════════════════════════
Дата выполнения, начало и конец работ  ← дата записи (местное время)
Вид обслуживания (ТО / Р)               ← work_type
Описание выполненных работ               ← description
Текущий показатель счётчика              ← moto (НП — без показания)
Продолжительность                        ← «1 день»
Запчасти снята / установлена (P/N)       ← parts_old / parts_new (НП — нет)
Исполнитель и контролирующий             ← fio
Источник записи в платформе — «Telegram»; автор — сотрудник, если его
Telegram привязан к платформе, иначе имя из Telegram.
"""

# ══════════════════════════════════════════════════════════════════
# ИМПОРТЫ
# ══════════════════════════════════════════════════════════════════
import os
import re
import json
import time
import logging
import threading
import requests
from datetime import datetime

import telebot
from telebot import types

from urllib.parse import quote
from zoneinfo import ZoneInfo


# ══════════════════════════════════════════════════════════════════
# ЛОГИРОВАНИЕ — сообщения видны в панели Railway → Logs
# ══════════════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# СЕКРЕТЫ И НАСТРОЙКИ — читаем из Railway Variables
# ══════════════════════════════════════════════════════════════════
BOT_TOKEN         = os.environ.get("BOT_TOKEN")
PLATFORM_URL      = (os.environ.get("PLATFORM_URL") or "").strip().rstrip("/")
PLATFORM_KEY      = (os.environ.get("PLATFORM_KEY") or "").strip()
NOTIFY_CHAT_ID    = os.environ.get("NOTIFY_CHAT_ID")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TIMEZONE          = os.environ.get("TIMEZONE", "Asia/Atyrau")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не задан в переменных окружения")
if not PLATFORM_URL or not PLATFORM_KEY:
    raise ValueError("❌ PLATFORM_URL и PLATFORM_KEY не заданы в переменных "
                     "окружения (ключ — в платформе: Админ-панель → Интеграции)")

# Сколько секунд держать список техники в кэше
try:
    EQUIPMENT_CACHE_TTL = int(os.environ.get("EQUIPMENT_CACHE_TTL", "300"))
except ValueError:
    EQUIPMENT_CACHE_TTL = 300

# Сколько кнопок техники показывать на одной странице
PAGE_SIZE = 50

DEFAULT_LANG = os.environ.get("DEFAULT_LANG", "ru").strip().lower()
if DEFAULT_LANG not in ("ru", "kz"):
    DEFAULT_LANG = "ru"


# ══════════════════════════════════════════════════════════════════
# ПЕРЕВОДЫ ИНТЕРФЕЙСА (ru / kz)
# ══════════════════════════════════════════════════════════════════
# ВАЖНО: это только то, что ВИДИТ пользователь.
# В реестр всегда пишется по-русски (ТО / Р / 1 день).
T = {
    "ru": {
        # ── кнопки нижнего меню ──
        "btn_new":  "📋 Новая запись ТО/Ремонт",
        "btn_ask":  "🔍 Задать вопрос",
        "btn_lang": "🌐 Тіл / Язык",

        # ── язык ──
        "lang_prompt":  "🌐 Выберите язык / Тілді таңдаңыз:",
        "lang_changed": "✅ Язык интерфейса: *русский*",

        # ── приветствие ──
        "welcome": (
            "👷 *Добро пожаловать в бот учёта ТО и ремонтов*\n"
            "🏢 ТОО «Caspian Integrated Services»\n\n"
            "Этот бот помогает быстро записать информацию "
            "о проведённом техническом обслуживании или ремонте.\n\n"
            "Данные автоматически сохраняются в реестр компании.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📋 *Новая запись* — добавить ТО или ремонт\n"
            "🔍 *Задать вопрос* — узнать историю обслуживания"
        ),
        "choose_action":   "Выберите действие:",
        "btn_start_new":   "📋 Начать новую запись",
        "btn_start_ask":   "🔍 Задать вопрос об оборудовании",

        # ── общее ──
        "back":            "⬅️ Назад",
        "cancel":          "❌ Отменить",
        "refresh":         "🔄 Обновить список техники",
        "step":            "Шаг {n} из 6",
        "cancelled":       "❌ Запись отменена.\n\nНажмите 📋 *Новая запись* чтобы начать заново.",
        "session_expired": "⚠️ Сессия устарела. Нажмите 📋 *Новая запись* и начните заново.",
        "already_done":    "✅ Эта запись уже сохранена. Для новой нажмите 📋 *Новая запись*.",
        "unknown_text":    "Не понял 🤔\n\nВыберите действие кнопкой ниже.",
        "page_nav":        "Стр. {cur} из {total}",
        "prev_page":       "⬅️ Предыдущие",
        "next_page":       "Следующие ➡️",

        # ── шаг 1: техника ──
        "step1_title":    "🔧 *Новая запись ТО/Ремонт*",
        "step1_prompt":   "Выберите технику:",
        "list_empty": (
            "❌ В платформе не найдено ни одной единицы техники.\n\n"
            "Проверьте раздел «Техника» в платформе CIS."
        ),
        "list_error":     "❌ Не удалось получить список техники из платформы:\n`{err}`",
        "list_refreshed": "🔄 Список обновлён. Найдено единиц техники: {n}",

        # ── шаг 2: вид работ ──
        "lbl_equipment":  "Техника",
        "step2_prompt":   "Вид обслуживания:",
        "btn_to":         "📋 ТО — Плановое техобслуживание",
        "btn_r":          "🔴 Р — Внеплановый ремонт",
        "lbl_type":       "Вид",

        # ── шаг 3: описание ──
        "step3_prompt":   "Опишите выполненные работы:",
        "step3_hint":     "_(Например: Замена масляного фильтра, воздушного фильтра)_",
        "desc_check":     "Вы написали:\n\n_{text}_\n\nВсё верно?",
        "btn_ok":         "✅ Верно",
        "btn_edit":       "✏️ Изменить",
        "desc_again":     "Введите описание работ заново:",
        "lbl_works":      "Работы",

        # ── шаг 4: моточасы ──
        "step4_prompt":   "Введите моточас / пробег:",
        "step4_hint":     "_(Напишите число, например: 16 500 — или нажмите «НП»)_",
        "btn_np":         "НП (нет показаний)",
        "lbl_moto":       "Моточас/Пробег",
        "moto_bad":       "⚠️ Не понял число. Напишите показание цифрами, например: 16 500 — или нажмите «НП».",

        # ── шаг 5: запчасти ──
        "step5_title":    "Запчасти / материалы:",
        "step5_prompt":   "Были ли использованы запчасти или материалы?",
        "btn_parts_np":   "НП — запчасти не использовались",
        "btn_parts_enter": "✏️ Ввести P/N запчастей",
        "parts_old_prompt": (
            "🔧 *Запчасти — старая деталь (снята)*\n\n"
            "Введите P/N и серийный номер СНЯТОЙ детали.\n"
            "_(Например: FF256 LF17473)_\n\n"
            "Если номер неизвестен — напишите: `НП`"
        ),
        "parts_new_prompt": (
            "✅ Старая деталь: *{old}*\n\n"
            "🔧 *Запчасти — новая деталь (установлена)*\n\n"
            "Введите P/N и серийный номер НОВОЙ установленной детали.\n"
            "_(Или нажмите кнопку если номер такой же)_"
        ),
        "btn_parts_same": "Такой же номер (как старая)",
        "lbl_parts_old":  "Запчасти (снята)",
        "lbl_parts_new":  "Запчасти (уст.)",

        # ── шаг 6: ФИО ──
        "step6_prompt":   "ФИО исполнителя:",
        "step6_hint":     "_(Фамилия и инициалы, например: Иванов И.И.)_",
        "lbl_fio":        "Исполнитель",

        # ── финал ──
        "final_title":    "📋 *ИТОГОВАЯ ЗАПИСЬ*",
        "lbl_date":       "Дата",
        "final_hint":     "Нажми *✅ Отправить* — запись попадёт в реестр ТО платформы.",
        "btn_send":       "✅ Отправить в реестр",
        "btn_fix_fio":    "⬅️ Исправить ФИО",
        "btn_restart":    "🔄 Начать заново",
        "saving":         "⏳ Сохраняю запись в реестр ТО...",
        "saved": (
            "✅ *Запись добавлена в реестр ТО!*\n\n"
            "📊 Запись: _{sheet}_\n"
            "📅 Дата: {date}\n"
            "🔧 Техника: {equipment}\n"
            "🔁 Вид: {wtype}\n\n"
            "Нажмите кнопку ниже чтобы добавить ещё одну запись."
        ),
        "btn_add_more":   "📋 Добавить ещё одну запись",
        "save_error": (
            "❌ *Ошибка при сохранении.*\n\n"
            "`{err}`\n\n"
            "Нажмите кнопку ниже чтобы попробовать снова."
        ),
        "btn_retry":      "🔄 Попробовать снова",
        "sheet_gone": (
            "❌ Техника «{title}» больше не найдена в платформе.\n\n"
            "Возможно, её код изменили или карточку удалили. "
            "Нажмите 🔄 «Обновить список техники» и выберите заново."
        ),
        "eq_gone": (
            "⚠️ Эта техника больше не найдена в платформе "
            "(код изменили или карточку удалили).\nСписок обновлён — выберите заново."
        ),

        # ── помощь ──
        "help": (
            "❓ *Помощь*\n\n"
            "Этот бот записывает данные о ТО и ремонтах в реестр компании.\n\n"
            "*Как использовать:*\n"
            "1. Нажмите 📋 *Новая запись ТО/Ремонт*\n"
            "2. Выберите технику из списка\n"
            "3. Выберите вид работ (ТО или Р)\n"
            "4. Опишите выполненные работы\n"
            "5. Введите моточасы или пробег\n"
            "6. Укажите запчасти (или «НП»)\n"
            "7. Введите ваше ФИО\n"
            "8. Нажмите ✅ *Отправить* — данные сохранятся автоматически\n\n"
            "*Команды:*\n"
            "/new — начать новую запись\n"
            "/ask — задать вопрос об оборудовании\n"
            "/lang — сменить язык\n"
            "/cancel — отменить текущую запись\n"
            "/help — показать эту справку\n\n"
            "*Новая техника не видна в списке?*\n"
            "Нажмите 📋 Новая запись → 🔄 Обновить список техники.\n\n"
            "*Проблемы?* Обратитесь к администратору."
        ),

        # ── режим вопросов ──
        "qa_intro": (
            "🤖 *Режим вопросов и ответов*\n\n"
            "Задайте любой вопрос об оборудовании.\n\n"
            "Например:\n"
            "• _Когда последний раз чинили насос 883?_\n"
            "• _Какое состояние Mercedes 032YO?_\n"
            "• _Кто обслуживал SHACMAN 650?_\n\n"
            "Напишите ваш вопрос:"
        ),
        "qa_next":        "🤖 Задайте следующий вопрос:",
        "qa_searching":   "🔍 Ищу информацию в реестре...",
        "qa_reading":     "📊 Читаю реестр ТО...",
        "qa_thinking":    "🤖 AI анализирует данные...",
        "qa_answer":      "🤖 *Ответ:*",
        "btn_qa_more":    "🔍 Задать ещё вопрос",
        "btn_qa_new":     "📋 Новая запись",
        "qa_cancelled":   "❌ Режим вопросов отменён.",
        "qa_error":       "❌ Произошла ошибка: {err}\n\nПопробуйте ещё раз.",
        "qa_no_key": (
            "❌ AI не настроен. "
            "Добавьте ANTHROPIC_API_KEY в переменные окружения Railway."
        ),
        "qa_ai_error":    "❌ Ошибка AI ({code}). Попробуйте позже.",
        "qa_timeout":     "❌ AI не ответил вовремя. Попробуйте снова.",
        "qa_no_data":     "Данные не найдены",
        "qa_empty_tab":   "По технике {title} записей нет.",
    },

    "kz": {
        # ── кнопки нижнего меню ──
        "btn_new":  "📋 Жаңа жазба ТҚ/Жөндеу",
        "btn_ask":  "🔍 Сұрақ қою",
        "btn_lang": "🌐 Тіл / Язык",

        # ── язык ──
        "lang_prompt":  "🌐 Тілді таңдаңыз / Выберите язык:",
        "lang_changed": "✅ Интерфейс тілі: *қазақша*",

        # ── приветствие ──
        "welcome": (
            "👷 *ТҚ және жөндеу есебі ботына қош келдіңіз*\n"
            "🏢 «Caspian Integrated Services» ЖШС\n\n"
            "Бұл бот атқарылған техникалық қызмет көрсету немесе жөндеу "
            "туралы ақпаратты тез жазуға көмектеседі.\n\n"
            "Деректер компания тізіліміне автоматты түрде сақталады.\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📋 *Жаңа жазба* — ТҚ немесе жөндеу қосу\n"
            "🔍 *Сұрақ қою* — қызмет көрсету тарихын білу"
        ),
        "choose_action":   "Әрекетті таңдаңыз:",
        "btn_start_new":   "📋 Жаңа жазба бастау",
        "btn_start_ask":   "🔍 Жабдық туралы сұрақ қою",

        # ── общее ──
        "back":            "⬅️ Артқа",
        "cancel":          "❌ Болдырмау",
        "refresh":         "🔄 Техника тізімін жаңарту",
        "step":            "{n}-қадам / 6",
        "cancelled":       "❌ Жазба тоқтатылды.\n\nҚайтадан бастау үшін 📋 *Жаңа жазба* батырмасын басыңыз.",
        "session_expired": "⚠️ Сессия ескірді. 📋 *Жаңа жазба* батырмасын басып, қайтадан бастаңыз.",
        "already_done":    "✅ Бұл жазба сақталған. Жаңасы үшін 📋 *Жаңа жазба* батырмасын басыңыз.",
        "unknown_text":    "Түсінбедім 🤔\n\nТөмендегі батырмамен әрекетті таңдаңыз.",
        "page_nav":        "{cur} / {total} бет",
        "prev_page":       "⬅️ Алдыңғы",
        "next_page":       "Келесі ➡️",

        # ── шаг 1: техника ──
        "step1_title":    "🔧 *Жаңа жазба ТҚ/Жөндеу*",
        "step1_prompt":   "Техниканы таңдаңыз:",
        "list_empty": (
            "❌ Платформада бірде-бір техника табылмады.\n\n"
            "CIS платформасындағы «Техника» бөлімін тексеріңіз."
        ),
        "list_error":     "❌ Платформадан техника тізімін алу мүмкін болмады:\n`{err}`",
        "list_refreshed": "🔄 Тізім жаңартылды. Табылған техника саны: {n}",

        # ── шаг 2: вид работ ──
        "lbl_equipment":  "Техника",
        "step2_prompt":   "Қызмет көрсету түрі:",
        "btn_to":         "📋 ТО — Жоспарлы техникалық қызмет",
        "btn_r":          "🔴 Р — Жоспардан тыс жөндеу",
        "lbl_type":       "Түрі",

        # ── шаг 3: описание ──
        "step3_prompt":   "Атқарылған жұмыстарды сипаттаңыз:",
        "step3_hint":     "_(Мысалы: Май сүзгісін, ауа сүзгісін ауыстыру)_",
        "desc_check":     "Сіз жаздыңыз:\n\n_{text}_\n\nБарлығы дұрыс па?",
        "btn_ok":         "✅ Дұрыс",
        "btn_edit":       "✏️ Өзгерту",
        "desc_again":     "Жұмыс сипаттамасын қайта енгізіңіз:",
        "lbl_works":      "Жұмыстар",

        # ── шаг 4: моточасы ──
        "step4_prompt":   "Мотосағат / жүрісті енгізіңіз:",
        "step4_hint":     "_(Санды жазыңыз, мысалы: 16 500 — немесе «НП» батырмасын басыңыз)_",
        "btn_np":         "НП (көрсеткіш жоқ)",
        "lbl_moto":       "Мотосағат/Жүріс",
        "moto_bad":       "⚠️ Санды түсінбедім. Көрсеткішті цифрмен жазыңыз, мысалы: 16 500 — немесе «НП» батырмасын басыңыз.",

        # ── шаг 5: запчасти ──
        "step5_title":    "Қосалқы бөлшектер / материалдар:",
        "step5_prompt":   "Қосалқы бөлшектер немесе материалдар пайдаланылды ма?",
        "btn_parts_np":   "НП — бөлшектер пайдаланылмады",
        "btn_parts_enter": "✏️ Бөлшектердің P/N нөмірін енгізу",
        "parts_old_prompt": (
            "🔧 *Бөлшектер — ескі бөлшек (шешілді)*\n\n"
            "ШЕШІЛГЕН бөлшектің P/N және сериялық нөмірін енгізіңіз.\n"
            "_(Мысалы: FF256 LF17473)_\n\n"
            "Нөмірі белгісіз болса — `НП` деп жазыңыз"
        ),
        "parts_new_prompt": (
            "✅ Ескі бөлшек: *{old}*\n\n"
            "🔧 *Бөлшектер — жаңа бөлшек (орнатылды)*\n\n"
            "ОРНАТЫЛҒАН жаңа бөлшектің P/N және сериялық нөмірін енгізіңіз.\n"
            "_(Немесе нөмірі бірдей болса, батырмасын басыңыз)_"
        ),
        "btn_parts_same": "Нөмірі бірдей (ескісі сияқты)",
        "lbl_parts_old":  "Бөлшек (шешілді)",
        "lbl_parts_new":  "Бөлшек (орнатылды)",

        # ── шаг 6: ФИО ──
        "step6_prompt":   "Орындаушының аты-жөні:",
        "step6_hint":     "_(Тегі және аты-жөнінің бас әріптері, мысалы: Иванов И.И.)_",
        "lbl_fio":        "Орындаушы",

        # ── финал ──
        "final_title":    "📋 *ҚОРЫТЫНДЫ ЖАЗБА*",
        "lbl_date":       "Күні",
        "final_hint":     "*✅ Жіберу* батырмасын басыңыз — жазба платформаның ТҚ тізіліміне түседі.",
        "btn_send":       "✅ Тізілімге жіберу",
        "btn_fix_fio":    "⬅️ Аты-жөнін түзету",
        "btn_restart":    "🔄 Қайтадан бастау",
        "saving":         "⏳ Жазба ТҚ тізіліміне сақталып жатыр...",
        "saved": (
            "✅ *Жазба ТҚ тізіліміне қосылды!*\n\n"
            "📊 Жазба: _{sheet}_\n"
            "📅 Күні: {date}\n"
            "🔧 Техника: {equipment}\n"
            "🔁 Түрі: {wtype}\n\n"
            "Тағы бір жазба қосу үшін төмендегі батырмасын басыңыз."
        ),
        "btn_add_more":   "📋 Тағы бір жазба қосу",
        "save_error": (
            "❌ *Сақтау кезінде қате.*\n\n"
            "`{err}`\n\n"
            "Қайта көру үшін төмендегі батырмасын басыңыз."
        ),
        "btn_retry":      "🔄 Қайта көру",
        "sheet_gone": (
            "❌ «{title}» техникасы платформада табылмады.\n\n"
            "Оның коды өзгертілген немесе карточкасы жойылған болуы мүмкін. "
            "🔄 «Техника тізімін жаңарту» батырмасын басып, қайта таңдаңыз."
        ),
        "eq_gone": (
            "⚠️ Бұл техника платформада табылмады "
            "(коды өзгертілген немесе карточкасы жойылған).\nТізім жаңартылды — қайта таңдаңыз."
        ),

        # ── помощь ──
        "help": (
            "❓ *Көмек*\n\n"
            "Бұл бот ТҚ және жөндеу деректерін компания тізіліміне жазады.\n\n"
            "*Қалай пайдалану:*\n"
            "1. 📋 *Жаңа жазба ТҚ/Жөндеу* батырмасын басыңыз\n"
            "2. Тізімнен техниканы таңдаңыз\n"
            "3. Жұмыс түрін таңдаңыз (ТО немесе Р)\n"
            "4. Атқарылған жұмыстарды сипаттаңыз\n"
            "5. Мотосағат немесе жүрісті енгізіңіз\n"
            "6. Қосалқы бөлшектерді көрсетіңіз (немесе «НП»)\n"
            "7. Аты-жөніңізді енгізіңіз\n"
            "8. ✅ *Жіберу* батырмасын басыңыз — деректер автоматты сақталады\n\n"
            "*Командалар:*\n"
            "/new — жаңа жазба бастау\n"
            "/ask — жабдық туралы сұрақ қою\n"
            "/lang — тілді ауыстыру\n"
            "/cancel — ағымдағы жазбаны тоқтату\n"
            "/help — осы анықтаманы көрсету\n\n"
            "*Жаңа техника тізімде жоқ па?*\n"
            "📋 Жаңа жазба → 🔄 Техника тізімін жаңарту.\n\n"
            "*Мәселе туындады ма?* Әкімшіге хабарласыңыз."
        ),

        # ── режим вопросов ──
        "qa_intro": (
            "🤖 *Сұрақ-жауап режимі*\n\n"
            "Жабдық туралы кез келген сұрақ қойыңыз.\n\n"
            "Мысалы:\n"
            "• _883 сорабын соңғы рет қашан жөндеді?_\n"
            "• _Mercedes 032YO жағдайы қандай?_\n"
            "• _SHACMAN 650 техникасына кім қызмет көрсетті?_\n\n"
            "Сұрағыңызды жазыңыз:"
        ),
        "qa_next":        "🤖 Келесі сұрағыңызды қойыңыз:",
        "qa_searching":   "🔍 Тізілімнен ақпарат іздеп жатырмын...",
        "qa_reading":     "📊 ТҚ тізілімін оқып жатырмын...",
        "qa_thinking":    "🤖 AI деректерді талдап жатыр...",
        "qa_answer":      "🤖 *Жауап:*",
        "btn_qa_more":    "🔍 Тағы сұрақ қою",
        "btn_qa_new":     "📋 Жаңа жазба",
        "qa_cancelled":   "❌ Сұрақ режимі тоқтатылды.",
        "qa_error":       "❌ Қате орын алды: {err}\n\nҚайта көріңіз.",
        "qa_no_key": (
            "❌ AI бапталмаған. "
            "Railway айнымалыларына ANTHROPIC_API_KEY қосыңыз."
        ),
        "qa_ai_error":    "❌ AI қатесі ({code}). Кейінірек көріңіз.",
        "qa_timeout":     "❌ AI уақытында жауап бермеді. Қайта көріңіз.",
        "qa_no_data":     "Деректер табылмады",
        "qa_empty_tab":   "{title} техникасы бойынша жазбалар жоқ.",
    },
}

# Язык каждого пользователя — в памяти процесса.
# После рестарта/деплоя бот спросит язык заново (кнопка 🌐 всегда доступна).
user_lang = {}


def lang_of(user_id) -> str:
    return user_lang.get(user_id, DEFAULT_LANG)


def t(user_id, key, **kwargs) -> str:
    """Возвращает строку интерфейса на языке пользователя."""
    lang = lang_of(user_id)
    text = T.get(lang, T["ru"]).get(key) or T["ru"].get(key, key)
    return text.format(**kwargs) if kwargs else text


# Тексты кнопок нижнего меню на ВСЕХ языках — чтобы бот понимал нажатие
# даже если пользователь только что сменил язык.
BTN_NEW_TEXTS  = {T[l]["btn_new"]  for l in T}
BTN_ASK_TEXTS  = {T[l]["btn_ask"]  for l in T}
BTN_LANG_TEXTS = {T[l]["btn_lang"] for l in T}


def md(text) -> str:
    """
    Экранирует символы, которые ломают parse_mode='Markdown' в Telegram.
    Нужно для НАЗВАНИЙ ВКЛАДОК и текста от пользователя:
    вкладка вида «Насос_2 (new)» иначе уронит отправку сообщения.
    """
    return re.sub(r"([_*`\[\]])", r"\\\1", str(text if text is not None else ""))


# ══════════════════════════════════════════════════════════════════
# ПЛАТФОРМА CIS — API РЕЕСТРА ТО (вместо Google Sheets)
# ══════════════════════════════════════════════════════════════════
SERVICE_API = "/api/v1/equipment-service"


class PlatformError(Exception):
    """Платформа отказала: текст ошибки показываем человеку как есть."""

    def __init__(self, text, status=None):
        super().__init__(text)
        self.status = status


_http = requests.Session()


def platform(method, path="", **kw):
    """Запрос к API реестра ТО платформы. Ответ — словарь из JSON."""
    try:
        r = _http.request(method, PLATFORM_URL + SERVICE_API + path, timeout=30,
                          headers={"Authorization": f"Bearer {PLATFORM_KEY}"},
                          **kw)
    except requests.RequestException as e:
        raise PlatformError(f"нет связи с платформой: {e}")
    try:
        data = r.json()
    except ValueError:
        raise PlatformError(f"платформа ответила {r.status_code}", r.status_code)
    if r.status_code != 200 or not data.get("ok"):
        raise PlatformError(data.get("error")
                            or f"платформа ответила {r.status_code}",
                            r.status_code)
    return data


def local_now():
    """Сейчас по местному времени (сервер бота живёт в UTC)."""
    return datetime.now(ZoneInfo(TIMEZONE))


def who_is(user):
    """Подпись автора для журнала платформы: «Имя Фамилия (@ник)»."""
    name = " ".join(x for x in (getattr(user, "first_name", None),
                                getattr(user, "last_name", None)) if x)
    if getattr(user, "username", None):
        name += f" (@{user.username})"
    return name.strip() or str(user.id)


# ══════════════════════════════════════════════════════════════════
# СПИСОК ТЕХНИКИ — ИЗ ПЛАТФОРМЫ
# ══════════════════════════════════════════════════════════════════
_eq_lock = threading.Lock()
_eq_cache = {"ts": 0.0, "items": []}   # items: [{"id": код, "title": ..., ...}]


def get_equipment_list(force: bool = False):
    """
    Возвращает список техники: [{"id": код, "title", "display", "search"}].

      id      — код машины в платформе (B1, J7…): идёт в callback_data
                (коротко, латиница — в лимит Telegram 64 байта влезает).
      title / display — «B1 · Насосная установка · 882AHDE» для кнопок.
      search  — то же + категория («Насос ГРП»): по нему режим вопросов
                узнаёт технику в тексте вопроса.

    Порядок — как в разделе «Техника» платформы.
    Результат кэшируется на EQUIPMENT_CACHE_TTL секунд.
    """
    now = time.time()
    if not force:
        with _eq_lock:
            if _eq_cache["items"] and (now - _eq_cache["ts"]) < EQUIPMENT_CACHE_TTL:
                return list(_eq_cache["items"])

    units = platform("GET")["units"]
    items = [
        {"id": u["code"], "title": u["title"], "display": u["title"],
         "search": f"{u['title']} {u.get('category') or ''}".strip()}
        for u in units
    ]

    with _eq_lock:
        _eq_cache["ts"] = time.time()
        _eq_cache["items"] = items

    logger.info(f"Список техники обновлён: {len(items)} шт.")
    return list(items)


def get_equipment_by_id(code):
    """Ищет технику по коду. Возвращает dict или None."""
    code = str(code or "")
    for item in get_equipment_list():
        if item["id"] == code:
            return item
    # Могли только что завести технику — пробуем обновить кэш
    for item in get_equipment_list(force=True):
        if item["id"] == code:
            return item
    return None


# ══════════════════════════════════════════════════════════════════
# ЗАПИСЬ В РЕЕСТР ТО ПЛАТФОРМЫ
# ══════════════════════════════════════════════════════════════════
NP_WORDS = {"нп", "н/п", "-", "—", "нет", "жоқ"}


def is_np(text) -> bool:
    """«НП» (не применимо / нет показаний / нет запчастей)."""
    return str(text or "").strip().lower() in NP_WORDS


def parse_moto(text):
    """«16 500», «16500,5», «16 500 м/ч» → 16500.0; «НП» → None;
    не число → False (бот переспросит)."""
    if is_np(text):
        return None
    m = re.search(r"\d[\d\s]*(?:[.,]\d+)?", str(text or ""))
    if not m:
        return False
    digits = "".join(ch for ch in m.group(0) if not ch.isspace())
    try:
        return float(digits.replace(",", "."))
    except ValueError:
        return False


def write_record(data: dict) -> tuple:
    """
    Записывает ТО / ремонт в реестр ТО платформы.

    Поля — те же, что раньше шли в строку таблицы: начало и конец работ =
    дата записи, продолжительность «1 день», ФИО — исполнитель и
    контролирующий. Счётчик бот не называет — платформа берёт основной
    счётчик машины.

    Возвращает (True, {"label", "unit", "warning"}) или
    (False, текст ошибки | "SHEET_GONE" — техники с таким кодом больше нет).
    """
    code = data.get("code")
    if not code:
        return False, "техника не выбрана (перезапустите запись)"
    try:
        day = datetime.strptime(data.get("date") or "", "%d.%m.%Y").date()
    except ValueError:
        day = local_now().date()
    fio = (data.get("fio") or "").strip()
    body = {
        "kind":        data.get("work_type", ""),
        "date":        day.isoformat(),
        "description": data.get("description", ""),
        "date_start":  day.isoformat(),
        "date_end":    day.isoformat(),
        "duration":    "1 день",
        "executor":    fio,
        "controller":  fio,
        "tg_id":       data.get("tg_id"),
        "author":      data.get("author"),
    }
    moto = parse_moto(data.get("moto", "НП"))
    if isinstance(moto, float):
        body["reading"] = moto
    for key, field in (("parts_old", "parts_removed"), ("parts_new", "parts_new")):
        value = data.get(key, "НП")
        if not is_np(value):
            body[field] = str(value).strip()

    try:
        res = platform("POST", "/" + quote(code, safe=""), json=body)
    except PlatformError as e:
        if e.status == 404:
            logger.error(f"❌ Техники {code} нет в платформе")
            return False, "SHEET_GONE"
        logger.error(f"❌ Платформа не приняла запись по {code}: {e}")
        return False, str(e)
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при записи: {e}", exc_info=True)
        return False, str(e)

    rec = res.get("record") or {}
    logger.info(
        f"✅ Записано в реестр ТО: {code} — {rec.get('label')}, "
        f"мото={data.get('moto')}, ФИО={fio}"
    )
    return True, {"label": rec.get("label", ""),
                  "unit": rec.get("unit") or data.get("equipment", ""),
                  "warning": res.get("warning")}


# ══════════════════════════════════════════════════════════════════
# СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ (в памяти)
# ══════════════════════════════════════════════════════════════════
user_data = {}

def get_state(user_id):    return user_data.get(user_id, {})
def set_state(user_id, d): user_data[user_id] = d
def clear_state(user_id):  user_data.pop(user_id, None)


# Какие поля обязательно должны быть заполнены к началу каждого шага.
# Защита от «старых» кнопок: пользователь может нажать кнопку из
# сообщения получасовой давности, когда бот уже перезапущен или
# начата запись по другой технике — тогда шаг просто не пустим дальше.
REQUIRED_FOR = {
    "work_type":   ("code", "equipment"),
    "description": ("code", "equipment", "work_type"),
    "moto":        ("code", "equipment", "work_type", "description"),
    "parts":       ("code", "equipment", "work_type", "description", "moto"),
    "fio":         ("code", "equipment", "work_type", "description", "moto",
                    "parts_old", "parts_new"),
    "final":       ("code", "equipment", "work_type", "description", "moto",
                    "parts_old", "parts_new", "fio", "date"),
}


def state_has(state: dict, step: str) -> bool:
    """Проверяет, что все нужные для шага поля заполнены."""
    return all(state.get(k) not in (None, "") for k in REQUIRED_FOR[step])


def ack(call):
    """Убирает «часики» на нажатой кнопке."""
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass


def say_expired(chat_id, user_id, edit_msg_id=None):
    """Сообщение «сессия устарела» + кнопка начать заново."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(user_id, "btn_restart"), callback_data="start:new"))
    text = t(user_id, "session_expired")
    try:
        if edit_msg_id:
            bot.edit_message_text(text, chat_id, edit_msg_id,
                                  parse_mode="Markdown", reply_markup=markup)
            return
    except Exception as e:
        logger.warning(f"edit не удался: {e}")
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)


def require(call, step):
    """
    Возвращает состояние, если шаг доступен. Иначе показывает
    «сессия устарела» и возвращает None (обработчик должен выйти).
    """
    uid = call.from_user.id
    state = get_state(uid)
    if state_has(state, step):
        return state
    logger.info(f"Шаг '{step}' недоступен для {uid}: состояние {list(state)}")
    say_expired(call.message.chat.id, uid, call.message.message_id)
    return None


def cut(text, limit: int = 400) -> str:
    """Обрезает длинный текст для показа в сводке (в таблицу идёт полный)."""
    value = str(text if text is not None else "")
    return value if len(value) <= limit else value[:limit] + "…"

# Кто сейчас в режиме вопросов
qa_mode_users = set()

# Замок на отправку записи: telebot обрабатывает обновления в нескольких потоках,
# без него два быстрых нажатия «Отправить» могли создать две строки.
_send_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════
# Q&A — ПОИСК ТЕХНИКИ В ВОПРОСЕ (без ручного словаря на каждую единицу)
# ══════════════════════════════════════════════════════════════════
_TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁәғқңөұүһіӘҒҚҢӨҰҮҺІ]+")

# Только ОБЩИЕ синонимы (не привязаны к конкретной единице техники),
# поэтому новую технику добавлять сюда не нужно.
ALIASES = {
    # русская/казахская запись латинских марок
    "мерс": "mercedes", "мерседес": "mercedes", "мерс.": "mercedes",
    "шакман": "shacman", "шахман": "shacman", "шэкман": "shacman",
    "донгфенг": "dongfeng", "донгфэнг": "dongfeng", "дунфэн": "dongfeng",
    "тойота": "toyota", "хилукс": "hilux", "хайлюкс": "hilux",
    "прадо": "prado", "актрос": "actros",
    # казахские слова → русские, как в названиях вкладок
    "сорап": "насос", "сорабы": "насос", "сорабын": "насос",
    "тіркеме": "полуприцеп", "жартылай": "полуприцеп",
    "жүкқұбыр": "трал",
    "су": "водогрейная", "жылытқыш": "водогрейная",
    "араластырғыш": "блендер",
}


def _norm(s: str) -> str:
    s = str(s or "").lower().replace("ё", "е").replace("№", " ")
    return " ".join(_TOKEN_RE.findall(s))


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def _token_score(tok: str, title_tokens, title_norm: str) -> int:
    """Насколько слово вопроса похоже на название вкладки."""
    has_digit = any(c.isdigit() for c in tok)

    if has_digit:
        # Код/номер — самый весомый признак
        if tok in title_tokens:
            return 4 if len(tok) <= 2 else len(tok) * 3
        if len(tok) >= 2 and tok in title_norm:
            # «883» внутри «883AHDE», «28» внутри «28ABY06», «701» внутри «701BDC01»
            return len(tok) * 3
        return 0

    if tok in title_tokens:
        return len(tok)
    if len(tok) >= 3 and tok in title_norm:
        return len(tok)

    # Словоформы: «блендером» ≈ «Блендер», «насосов» ≈ «Насос», «тралом» ≈ «Трал»
    if len(tok) >= 4:
        best = 0
        for tt in title_tokens:
            if len(tt) < 4 or any(c.isdigit() for c in tt):
                continue
            p = _common_prefix_len(tok, tt)
            if p >= 4 and p >= 0.6 * min(len(tok), len(tt)):
                best = max(best, p)
        return best

    return 0


def find_sheet_for_question(question: str, items):
    """
    Пытается понять, о какой технике спрашивают.
    Сравнивает слова вопроса с названием техники в платформе (код, марка,
    госномер, категория) — поэтому работает
    и для только что добавленной техники, без правок кода.

    Если несколько единиц техники подходят одинаково хорошо
    (например вопрос «что с насосами?»), возвращает None —
    тогда бот читает все вкладки и отдаёт AI общую картину.

    Возвращает (item | None, score).
    """
    q_tokens = set(_norm(question).split())
    for tok in list(q_tokens):
        if tok in ALIASES:
            q_tokens.add(ALIASES[tok])

    scored = []
    for item in items:
        title_norm = _norm(item.get("search") or item["title"])
        title_tokens = set(title_norm.split())
        score = sum(_token_score(tok, title_tokens, title_norm) for tok in q_tokens)
        scored.append((score, item))

    if not scored:
        return None, 0

    best_score = max(s for s, _ in scored)
    winners = [i for s, i in scored if s == best_score]

    if best_score < 4 or len(winners) > 1:
        return None, best_score
    return winners[0], best_score


# ══════════════════════════════════════════════════════════════════
# Q&A — ЧТЕНИЕ РЕЕСТРА ТО ИЗ ПЛАТФОРМЫ
# ══════════════════════════════════════════════════════════════════
MAX_ROWS_SINGLE_TAB = 40      # сколько последних записей брать по одной технике
ROWS_PER_TAB_OVERVIEW = 3     # сколько последних записей брать по каждой технике
MAX_CHARS_TO_AI = 45000       # ограничение объёма данных для AI


def _ru_date(iso) -> str:
    try:
        return datetime.strptime(iso or "", "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return ""


def _fmt_record(r, index=None) -> str:
    def g(key):
        v = r.get(key)
        return str(v).strip() if v not in (None, "") else ""
    moto = f"{g('reading')} {g('unit')}".strip() if g("reading") else ""
    head = f"Запись {index}: " if index else "  "
    return (
        f"{head}Дата={_ru_date(r.get('date'))} | Вид={g('kind')} | "
        f"Основание={g('reason')} | Состояние до={g('state_before')} | "
        f"Описание={g('description')} | Моточасы={moto} | "
        f"Начало={_ru_date(r.get('date_start'))} | "
        f"Конец={_ru_date(r.get('date_end'))} | Длит={g('duration')} | "
        f"Запчасти план={g('parts_removed')} | Запчасти факт={g('parts_new')} | "
        f"Примечание={g('comment')} | Исполнитель={g('executor')} | "
        f"Контролирующий={g('controller')}"
    )


def read_single_tab(item, user_id=None) -> str:
    """Последние записи по одной единице техники."""
    res = platform("GET", "/" + quote(item["id"], safe="") + "/history",
                   params={"limit": MAX_ROWS_SINGLE_TAB})
    rows = res.get("records") or []
    if not rows:
        return t(user_id, "qa_empty_tab", title=item["display"]) if user_id \
            else f"По технике {item['display']} записей нет."

    truncated = ""
    total = res.get("total", len(rows))
    if total > len(rows):
        truncated = f"(показаны последние {len(rows)} из {total} записей)"

    out = [f"=== {item['display']} {truncated} ==="]
    for i, row in enumerate(rows, 1):
        out.append(_fmt_record(row, i))
    return "\n".join(out)


def read_all_tabs(items=None, user_id=None) -> str:
    """Последние записи по ВСЕЙ технике — одним запросом к платформе."""
    units = platform("GET", params={"last": ROWS_PER_TAB_OVERVIEW})["units"]
    out = []
    for u in units:
        rows = u.get("records") or []
        if not rows:
            continue
        out.append(f"\n=== {u['title']} (всего записей: {u.get('total', len(rows))}) ===")
        for row in rows:
            out.append(_fmt_record(row))

    text = "\n".join(out)
    if len(text) > MAX_CHARS_TO_AI:
        text = text[:MAX_CHARS_TO_AI] + "\n...(данные обрезаны)"
    return text or (t(user_id, "qa_no_data") if user_id else "Данные не найдены")


def get_sheet_data_for_question(question: str, user_id=None) -> str:
    """
    Готовит данные для AI: по конкретной технике или обзор по всей.
    Ошибки НЕ глотаем — иначе AI получил бы текст ошибки как «данные»
    и ответил «информация не найдена». Пусть пользователь видит сбой.
    """
    items = get_equipment_list()
    item, score = find_sheet_for_question(question, items)
    if item:
        logger.info(f"QA: вопрос отнесён к '{item['display']}' (score={score})")
        return read_single_tab(item, user_id)
    logger.info(f"QA: конкретная техника не определена (score={score}), читаю обзор")
    return read_all_tabs(items, user_id)


# ══════════════════════════════════════════════════════════════════
# Q&A — ЗАПРОС К CLAUDE
# ══════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """Ты — помощник по техническому обслуживанию оборудования компании ТОО «Caspian Integrated Services».

У тебя есть доступ к реестру технического обслуживания и ремонтных работ компании.
Твоя задача — отвечать на вопросы сотрудников о состоянии оборудования, истории обслуживания и ремонтов.

ПРАВИЛА:
1. Отвечай ТОЛЬКО на основе предоставленных данных из реестра. Не придумывай ничего.
2. Если данных нет — честно скажи что информация не найдена.
3. ЯЗЫК ОТВЕТА: отвечай на том же языке, на котором задан вопрос — русском, казахском или английском. Если вопрос на казахском, отвечай по-казахски.
4. Будь конкретным — называй даты, имена, типы работ из реальных данных.
5. Если вопрос непонятен — попроси уточнить.
6. Форматируй ответ кратко и понятно для работника на телефоне.
7. Понимай сокращения: ТО = техническое обслуживание (жоспарлы техникалық қызмет),
   Р = ремонт (жөндеу), НП = нет показаний (көрсеткіш жоқ).
8. Названия техники в реестре — это названия вкладок таблицы
   (например «№3 Насос 883AHDE», «Mercedes Actros 032YO»).
   Понимай неточные и казахские варианты: «насос 3» = «№3 Насос 883AHDE»,
   «мерс 032» = «Mercedes Actros 032YO», «сорап» = насос.
9. Данные в реестре ведутся по-русски — переводи их в ответе, если вопрос на казахском."""


def ask_claude(question: str, spreadsheet_data: str, user_id=None) -> str:
    """Отправляет вопрос + данные реестра в Claude. Возвращает текст ответа."""
    if not ANTHROPIC_API_KEY:
        return t(user_id, "qa_no_key")

    lang_hint = (
        "Пользователь выбрал казахский интерфейс — если вопрос на казахском, "
        "обязательно отвечай по-казахски."
        if lang_of(user_id) == "kz" else ""
    )

    user_message = f"""Данные из реестра технического обслуживания:

{spreadsheet_data}

Вопрос сотрудника: {question}

{lang_hint}
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
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_message}],
            },
            timeout=30,
        )

        if response.status_code == 200:
            return response.json()["content"][0]["text"]

        logger.error(f"Anthropic API error: {response.status_code} {response.text}")
        return t(user_id, "qa_ai_error", code=response.status_code)

    except requests.Timeout:
        return t(user_id, "qa_timeout")
    except Exception as e:
        logger.error(f"Ошибка вызова Claude API: {e}")
        return f"❌ {e}"


# ══════════════════════════════════════════════════════════════════
# СОЗДАНИЕ БОТА
# ══════════════════════════════════════════════════════════════════
bot = telebot.TeleBot(BOT_TOKEN)


def setup_bot_commands():
    """Меню команд по кнопке «/»."""
    try:
        bot.set_my_commands([
            types.BotCommand("/new",    "📋 Новая запись / Жаңа жазба"),
            types.BotCommand("/ask",    "🔍 Задать вопрос / Сұрақ қою"),
            types.BotCommand("/lang",   "🌐 Язык / Тіл"),
            types.BotCommand("/help",   "❓ Помощь / Көмек"),
            types.BotCommand("/cancel", "❌ Отменить / Болдырмау"),
        ])
        logger.info("✅ Команды бота установлены")
    except Exception as e:
        logger.warning(f"Не удалось установить команды: {e}")


def get_main_menu_keyboard(user_id):
    """Постоянная клавиатура внизу чата — на языке пользователя."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    markup.row(
        types.KeyboardButton(t(user_id, "btn_new")),
        types.KeyboardButton(t(user_id, "btn_ask")),
    )
    markup.row(types.KeyboardButton(t(user_id, "btn_lang")))
    return markup


# ══════════════════════════════════════════════════════════════════
# ВЫБОР ЯЗЫКА
# ══════════════════════════════════════════════════════════════════
def show_language_choice(chat_id, user_id, edit_msg_id=None):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🇷🇺 Русский",  callback_data="lang:ru"),
        types.InlineKeyboardButton("🇰🇿 Қазақша", callback_data="lang:kz"),
    )
    text = t(user_id, "lang_prompt")
    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id, reply_markup=markup)
    else:
        bot.send_message(chat_id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("lang:"))
def cb_lang(call):
    ack(call)
    choice = call.data.split(":", 1)[1]
    if choice not in T:
        choice = "ru"
    user_lang[call.from_user.id] = choice
    logger.info(f"Пользователь {call.from_user.id} выбрал язык: {choice}")

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    uid = call.from_user.id
    bot.send_message(
        call.message.chat.id,
        t(uid, "lang_changed"),
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(uid),
    )
    send_welcome(call.message.chat.id, uid)


@bot.message_handler(commands=["lang"])
def cmd_lang(message):
    show_language_choice(message.chat.id, message.from_user.id)


@bot.message_handler(func=lambda m: m.text in BTN_LANG_TEXTS)
def btn_lang_handler(message):
    show_language_choice(message.chat.id, message.from_user.id)


# ══════════════════════════════════════════════════════════════════
# /start — приветствие
# ══════════════════════════════════════════════════════════════════
def send_welcome(chat_id, user_id):
    inline = types.InlineKeyboardMarkup(row_width=1)
    inline.add(
        types.InlineKeyboardButton(t(user_id, "btn_start_new"), callback_data="start:new"),
        types.InlineKeyboardButton(t(user_id, "btn_start_ask"), callback_data="start:ask"),
    )
    bot.send_message(
        chat_id,
        t(user_id, "welcome"),
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(user_id),
    )
    bot.send_message(chat_id, t(user_id, "choose_action"), reply_markup=inline)


@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    clear_state(uid)
    qa_mode_users.discard(uid)

    if uid not in user_lang:
        # Первый вход — сначала язык
        show_language_choice(message.chat.id, uid)
        return

    send_welcome(message.chat.id, uid)


@bot.callback_query_handler(func=lambda c: c.data == "start:new")
def cb_start_new(call):
    ack(call)
    uid = call.from_user.id
    clear_state(uid)
    qa_mode_users.discard(uid)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    show_equipment_list(call.message.chat.id, uid)


@bot.callback_query_handler(func=lambda c: c.data == "start:ask")
def cb_start_ask(call):
    ack(call)
    uid = call.from_user.id
    clear_state(uid)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    enter_qa_mode(call.message.chat.id, uid)


# ══════════════════════════════════════════════════════════════════
# КОМАНДЫ И КНОПКИ МЕНЮ
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(commands=["new"])
def cmd_new(message):
    uid = message.from_user.id
    clear_state(uid)
    qa_mode_users.discard(uid)
    show_equipment_list(message.chat.id, uid)


@bot.message_handler(func=lambda m: m.text in BTN_NEW_TEXTS)
def btn_new_handler(message):
    uid = message.from_user.id
    clear_state(uid)
    qa_mode_users.discard(uid)
    show_equipment_list(message.chat.id, uid)


@bot.message_handler(commands=["help"])
def cmd_help(message):
    uid = message.from_user.id
    bot.send_message(
        message.chat.id,
        t(uid, "help"),
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(uid),
    )


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    uid = message.from_user.id
    clear_state(uid)
    qa_mode_users.discard(uid)
    bot.send_message(
        message.chat.id,
        t(uid, "cancelled"),
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard(uid),
    )


# ══════════════════════════════════════════════════════════════════
# РЕЖИМ ВОПРОСОВ
# ══════════════════════════════════════════════════════════════════
def enter_qa_mode(chat_id, user_id, edit_msg_id=None):
    clear_state(user_id)          # выходим из незаконченной записи
    qa_mode_users.add(user_id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(user_id, "cancel"), callback_data="qa:cancel"))
    text = t(user_id, "qa_intro")
    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id,
                              parse_mode="Markdown", reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)


@bot.message_handler(commands=["ask"])
def cmd_ask(message):
    enter_qa_mode(message.chat.id, message.from_user.id)


@bot.message_handler(func=lambda m: m.text in BTN_ASK_TEXTS)
def btn_ask_handler(message):
    enter_qa_mode(message.chat.id, message.from_user.id)


@bot.callback_query_handler(func=lambda c: c.data == "qa:cancel")
def cb_qa_cancel(call):
    ack(call)
    uid = call.from_user.id
    qa_mode_users.discard(uid)
    bot.edit_message_text(t(uid, "qa_cancelled"), call.message.chat.id, call.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == "qa:another")
def cb_qa_another(call):
    ack(call)
    uid = call.from_user.id
    clear_state(uid)      # выходим из незаконченной записи, иначе её текст уйдёт в AI
    qa_mode_users.add(uid)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(uid, "cancel"), callback_data="qa:cancel"))
    bot.edit_message_text(t(uid, "qa_next"), call.message.chat.id,
                          call.message.message_id, reply_markup=markup)


@bot.message_handler(func=lambda m: m.from_user.id in qa_mode_users)
def handle_qa_question(message):
    """Любой текст от пользователя в режиме вопросов."""
    uid = message.from_user.id
    question = message.text or ""

    bot.send_chat_action(message.chat.id, "typing")
    searching = bot.send_message(message.chat.id, t(uid, "qa_searching"))

    try:
        bot.edit_message_text(t(uid, "qa_reading"), message.chat.id, searching.message_id)
        sheet_data = get_sheet_data_for_question(question, uid)

        bot.edit_message_text(t(uid, "qa_thinking"), message.chat.id, searching.message_id)
        answer = ask_claude(question, sheet_data, uid)

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(t(uid, "btn_qa_more"), callback_data="qa:another"),
            types.InlineKeyboardButton(t(uid, "btn_qa_new"),  callback_data="start:new"),
        )

        try:
            bot.delete_message(message.chat.id, searching.message_id)
        except Exception:
            pass

        # Символы * и _ из ответа AI экранируем, иначе Markdown ломает сообщение.
        # Длинный ответ режем на части — лимит Telegram 4096 символов.
        raw = str(answer or "")
        # Режем СЫРОЙ текст (до экранирования), иначе разрыв может попасть
        # между «\\» и символом и Telegram вернёт ошибку разметки.
        parts = [raw[i:i + 1800] for i in range(0, len(raw), 1800)] or [""]
        for idx, part in enumerate(parts):
            head = f"{t(uid, 'qa_answer')}\n\n" if idx == 0 else ""
            bot.send_message(
                message.chat.id, head + md(part), parse_mode="Markdown",
                reply_markup=markup if idx == len(parts) - 1 else None,
            )

    except Exception as e:
        logger.error(f"QA error: {e}", exc_info=True)
        try:
            bot.edit_message_text(t(uid, "qa_error", err=e),
                                  message.chat.id, searching.message_id)
        except Exception:
            bot.send_message(message.chat.id, t(uid, "qa_error", err=e))

    finally:
        qa_mode_users.discard(uid)


# ══════════════════════════════════════════════════════════════════
# ШАГ 1: ВЫБОР ТЕХНИКИ (список строится из вкладок таблицы)
# ══════════════════════════════════════════════════════════════════
def show_equipment_list(chat_id, user_id, edit_msg_id=None, page=0, force=False, note=None):
    set_state(user_id, {"step": "equipment", "page": page})

    try:
        items = get_equipment_list(force=force)
    except Exception as e:
        logger.error(f"Не удалось получить список вкладок: {e}", exc_info=True)
        text = t(user_id, "list_error", err=e)
        if edit_msg_id:
            bot.edit_message_text(text, chat_id, edit_msg_id, parse_mode="Markdown")
        else:
            bot.send_message(chat_id, text, parse_mode="Markdown")
        return

    if not items:
        text = t(user_id, "list_empty")
        if edit_msg_id:
            bot.edit_message_text(text, chat_id, edit_msg_id)
        else:
            bot.send_message(chat_id, text)
        return

    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    markup = types.InlineKeyboardMarkup(row_width=1)
    for item in chunk:
        # В callback_data кладём ТОЛЬКО gid вкладки — коротко и однозначно.
        # (Раньше туда шло название; длинное название на кириллице
        #  выходило за лимит Telegram в 64 байта и роняло меню.)
        label = item["display"]
        if len(label) > 60:          # очень длинное имя вкладки обрезаем для кнопки
            label = label[:57] + "..."
        markup.add(types.InlineKeyboardButton(label, callback_data=f"eq:{item['id']}"))

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(types.InlineKeyboardButton(
                t(user_id, "prev_page"), callback_data=f"eqpage:{page - 1}"))
        if page < total_pages - 1:
            nav.append(types.InlineKeyboardButton(
                t(user_id, "next_page"), callback_data=f"eqpage:{page + 1}"))
        if nav:
            markup.row(*nav)

    markup.add(types.InlineKeyboardButton(t(user_id, "refresh"), callback_data="eqrefresh"))

    text = (
        f"{t(user_id, 'step1_title')}\n\n"
        f"{t(user_id, 'step', n=1)} — {t(user_id, 'step1_prompt')}"
    )
    if total_pages > 1:
        text += f"\n_{t(user_id, 'page_nav', cur=page + 1, total=total_pages)}_"
    if note:
        text = f"{note}\n\n{text}"

    if edit_msg_id:
        try:
            bot.edit_message_text(text, chat_id, edit_msg_id,
                                  parse_mode="Markdown", reply_markup=markup)
            return
        except Exception as e:
            # Например «message is not modified» при повторном обновлении
            logger.info(f"Список не перерисован ({e})")
            if "not modified" in str(e):
                return
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("eqpage:"))
def cb_equipment_page(call):
    ack(call)
    page = int(call.data.split(":", 1)[1])
    show_equipment_list(call.message.chat.id, call.from_user.id,
                        call.message.message_id, page=page)


@bot.callback_query_handler(func=lambda c: c.data == "eqrefresh")
def cb_equipment_refresh(call):
    ack(call)
    uid = call.from_user.id
    try:
        items = get_equipment_list(force=True)
        note = t(uid, "list_refreshed", n=len(items))
    except Exception as e:
        note = t(uid, "list_error", err=e)
    page = get_state(uid).get("page", 0)
    show_equipment_list(call.message.chat.id, uid, call.message.message_id,
                        page=page, note=note)


# ══════════════════════════════════════════════════════════════════
# ШАГ 2: ВИД РАБОТ (ТО / Р)
# ══════════════════════════════════════════════════════════════════
def work_type_markup(user_id):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        # callback_data остаётся русским (ТО / Р) — это значение для таблицы
        types.InlineKeyboardButton(t(user_id, "btn_to"), callback_data="wt:ТО"),
        types.InlineKeyboardButton(t(user_id, "btn_r"),  callback_data="wt:Р"),
        types.InlineKeyboardButton(t(user_id, "back"),   callback_data="back:equipment"),
    )
    return markup


@bot.callback_query_handler(func=lambda c: c.data.startswith("eq:"))
def cb_equipment(call):
    ack(call)
    uid = call.from_user.id
    gid = call.data[3:]

    item = get_equipment_by_id(gid)
    if item is None:
        show_equipment_list(call.message.chat.id, uid, call.message.message_id,
                            force=True, note=t(uid, "eq_gone"))
        return

    state = get_state(uid)
    state.update({
        "code":      item["id"],
        "equipment": item["display"],
        "step":      "work_type",
    })
    set_state(uid, state)

    bot.edit_message_text(
        f"✅ {t(uid, 'lbl_equipment')}: *{md(item['title'])}*\n\n"
        f"{t(uid, 'step', n=2)} — {t(uid, 'step2_prompt')}",
        call.message.chat.id, call.message.message_id,
        parse_mode="Markdown", reply_markup=work_type_markup(uid),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("wt:"))
def cb_work_type(call):
    ack(call)
    uid = call.from_user.id
    state = require(call, "work_type")
    if state is None:
        return

    state.update({"work_type": call.data[3:], "step": "description"})
    set_state(uid, state)

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(uid, "back"), callback_data="back:work_type"))

    bot.edit_message_text(
        f"✅ {t(uid, 'lbl_equipment')}: *{md(state['equipment'])}*\n"
        f"✅ {t(uid, 'lbl_type')}: *{state['work_type']}*\n\n"
        f"{t(uid, 'step', n=3)} — {t(uid, 'step3_prompt')}\n"
        f"{t(uid, 'step3_hint')}",
        call.message.chat.id, call.message.message_id,
        parse_mode="Markdown", reply_markup=markup,
    )


# ══════════════════════════════════════════════════════════════════
# ШАГ 3: ОПИСАНИЕ РАБОТ
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "description")
def handle_description(message):
    uid = message.from_user.id
    state = get_state(uid)
    if not state_has(state, "description"):
        clear_state(uid)          # несогласованное состояние — сбрасываем
        say_expired(message.chat.id, uid)
        return
    state.update({"description": message.text, "step": "confirm_description"})
    set_state(uid, state)

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(t(uid, "btn_ok"),   callback_data="desc:ok"),
        types.InlineKeyboardButton(t(uid, "btn_edit"), callback_data="desc:edit"),
        types.InlineKeyboardButton(t(uid, "back"),     callback_data="back:description"),
    )
    bot.send_message(
        message.chat.id,
        t(uid, "desc_check", text=md(message.text)),
        parse_mode="Markdown", reply_markup=markup,
    )


def moto_markup(user_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(t(user_id, "btn_np"), callback_data="moto:НП"),
        types.InlineKeyboardButton(t(user_id, "back"),   callback_data="back:description"),
    )
    return markup


@bot.callback_query_handler(func=lambda c: c.data.startswith("desc:"))
def cb_description_confirm(call):
    ack(call)
    uid = call.from_user.id
    action = call.data[5:]
    state = require(call, "moto" if call.data == "desc:ok" else "description")
    if state is None:
        return

    if action == "edit":
        state["step"] = "description"
        set_state(uid, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(uid, "back"), callback_data="back:work_type"))
        bot.edit_message_text(t(uid, "desc_again"), call.message.chat.id,
                              call.message.message_id, reply_markup=markup)

    elif action == "ok":
        state["step"] = "moto"
        set_state(uid, state)
        bot.edit_message_text(
            f"✅ {t(uid, 'lbl_equipment')}: *{md(state['equipment'])}*\n"
            f"✅ {t(uid, 'lbl_type')}: *{state['work_type']}*\n"
            f"✅ {t(uid, 'lbl_works')}: _{md(state['description'])}_\n\n"
            f"{t(uid, 'step', n=4)} — {t(uid, 'step4_prompt')}\n"
            f"{t(uid, 'step4_hint')}",
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=moto_markup(uid),
        )


# ══════════════════════════════════════════════════════════════════
# ШАГ 4: МОТОЧАСЫ / ПРОБЕГ
# ══════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("moto:"))
def cb_moto_np(call):
    ack(call)
    uid = call.from_user.id
    state = require(call, "moto")
    if state is None:
        return
    state.update({"moto": call.data[5:], "step": "parts"})
    set_state(uid, state)
    ask_parts(call.message.chat.id, uid, call.message.message_id, state)


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "moto")
def handle_moto(message):
    uid = message.from_user.id
    state = get_state(uid)
    if not state_has(state, "moto"):
        clear_state(uid)          # несогласованное состояние — сбрасываем
        say_expired(message.chat.id, uid)
        return
    if parse_moto(message.text) is False:
        # не число и не «НП» — переспрашиваем, а не пишем мусор в реестр
        bot.send_message(message.chat.id, t(uid, "moto_bad"),
                         reply_markup=moto_markup(uid))
        return
    state.update({"moto": message.text.strip(), "step": "parts"})
    set_state(uid, state)
    ask_parts(message.chat.id, uid, None, state)


# ══════════════════════════════════════════════════════════════════
# ШАГ 5: ЗАПЧАСТИ (колонки K и L)
# ══════════════════════════════════════════════════════════════════
def ask_parts(chat_id, user_id, edit_msg_id, state):
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(t(user_id, "btn_parts_np"),    callback_data="parts:np"),
        types.InlineKeyboardButton(t(user_id, "btn_parts_enter"), callback_data="parts:enter"),
        types.InlineKeyboardButton(t(user_id, "back"),            callback_data="back:moto"),
    )
    text = (
        f"✅ {t(user_id, 'lbl_equipment')}: *{md(state.get('equipment', ''))}*\n"
        f"✅ {t(user_id, 'lbl_type')}: *{state.get('work_type', '')}*\n"
        f"✅ {t(user_id, 'lbl_works')}: _{md(cut(state.get('description', '')))}_\n"
        f"✅ {t(user_id, 'lbl_moto')}: *{md(cut(state.get('moto', ''), 60))}*\n\n"
        f"{t(user_id, 'step', n=5)} — {t(user_id, 'step5_title')}\n\n"
        f"{t(user_id, 'step5_prompt')}"
    )
    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id,
                              parse_mode="Markdown", reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda c: c.data.startswith("parts:"))
def cb_parts(call):
    ack(call)
    uid = call.from_user.id
    action = call.data[6:]
    state = require(call, "parts")
    if state is None:
        return

    if action == "np":
        state.update({"parts_old": "НП", "parts_new": "НП", "step": "fio"})
        set_state(uid, state)
        ask_fio(call.message.chat.id, uid, call.message.message_id, state)

    elif action == "enter":
        state["step"] = "parts_old"
        set_state(uid, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(uid, "back"), callback_data="back:parts"))
        bot.edit_message_text(
            t(uid, "parts_old_prompt"),
            call.message.chat.id, call.message.message_id,
            parse_mode="Markdown", reply_markup=markup,
        )


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "parts_old")
def handle_parts_old(message):
    uid = message.from_user.id
    state = get_state(uid)
    if not state_has(state, "parts"):
        clear_state(uid)          # несогласованное состояние — сбрасываем
        say_expired(message.chat.id, uid)
        return
    state.update({"parts_old": message.text, "step": "parts_new"})
    set_state(uid, state)

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(t(uid, "btn_parts_same"), callback_data="parts_new:same"),
        types.InlineKeyboardButton(t(uid, "back"),           callback_data="back:parts"),
    )
    bot.send_message(
        message.chat.id,
        t(uid, "parts_new_prompt", old=md(message.text)),
        parse_mode="Markdown", reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda c: c.data == "parts_new:same")
def cb_parts_new_same(call):
    ack(call)
    uid = call.from_user.id
    state = require(call, "parts")
    if state is None:
        return
    state.update({"parts_new": state.get("parts_old", "НП"), "step": "fio"})
    set_state(uid, state)
    ask_fio(call.message.chat.id, uid, call.message.message_id, state)


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "parts_new")
def handle_parts_new(message):
    uid = message.from_user.id
    state = get_state(uid)
    if not state_has(state, "parts"):
        clear_state(uid)          # несогласованное состояние — сбрасываем
        say_expired(message.chat.id, uid)
        return
    state.update({"parts_new": message.text, "step": "fio"})
    set_state(uid, state)
    ask_fio(message.chat.id, uid, None, state)


# ══════════════════════════════════════════════════════════════════
# ШАГ 6: ФИО ИСПОЛНИТЕЛЯ
# ══════════════════════════════════════════════════════════════════
def ask_fio(chat_id, user_id, edit_msg_id, state):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(user_id, "back"), callback_data="back:parts"))
    text = (
        f"✅ {t(user_id, 'lbl_equipment')}: *{md(state.get('equipment', ''))}*\n"
        f"✅ {t(user_id, 'lbl_type')}: *{state.get('work_type', '')}*\n"
        f"✅ {t(user_id, 'lbl_works')}: _{md(cut(state.get('description', '')))}_\n"
        f"✅ {t(user_id, 'lbl_moto')}: *{md(cut(state.get('moto', ''), 60))}*\n"
        f"✅ {t(user_id, 'lbl_parts_old')}: *{md(cut(state.get('parts_old', 'НП'), 120))}*\n"
        f"✅ {t(user_id, 'lbl_parts_new')}: *{md(cut(state.get('parts_new', 'НП'), 120))}*\n\n"
        f"{t(user_id, 'step', n=6)} — {t(user_id, 'step6_prompt')}\n"
        f"{t(user_id, 'step6_hint')}"
    )
    if edit_msg_id:
        bot.edit_message_text(text, chat_id, edit_msg_id,
                              parse_mode="Markdown", reply_markup=markup)
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)


@bot.message_handler(func=lambda m: get_state(m.from_user.id).get("step") == "fio")
def handle_fio(message):
    uid = message.from_user.id
    state = get_state(uid)
    if not state_has(state, "fio"):
        clear_state(uid)          # несогласованное состояние — сбрасываем
        say_expired(message.chat.id, uid)
        return
    state.update({
        "fio":  message.text,
        "date": local_now().strftime("%d.%m.%Y"),
        "step": "final_confirm",
    })
    set_state(uid, state)
    show_final_confirm(message.chat.id, uid, state)


# ══════════════════════════════════════════════════════════════════
# ИТОГОВАЯ СВОДКА
# ══════════════════════════════════════════════════════════════════
def show_final_confirm(chat_id, user_id, state):
    line = "─" * 32
    summary = (
        f"{t(user_id, 'final_title')}\n"
        f"{line}\n"
        f"📅 {t(user_id, 'lbl_date')}: *{state.get('date', '')}*\n"
        f"🔧 {t(user_id, 'lbl_equipment')}: *{md(cut(state.get('equipment', ''), 120))}*\n"
        f"🔁 {t(user_id, 'lbl_type')}: *{state.get('work_type', '')}*\n"
        f"📝 {t(user_id, 'lbl_works')}: _{md(cut(state.get('description', ''), 900))}_\n"
        f"⏱ {t(user_id, 'lbl_moto')}: *{md(cut(state.get('moto', ''), 60))}*\n"
        f"🔩 {t(user_id, 'lbl_parts_old')}: *{md(cut(state.get('parts_old', 'НП'), 200))}*\n"
        f"🔩 {t(user_id, 'lbl_parts_new')}: *{md(cut(state.get('parts_new', 'НП'), 200))}*\n"
        f"👷 {t(user_id, 'lbl_fio')}: *{md(cut(state.get('fio', ''), 120))}*\n"
        f"{line}\n\n"
        f"{t(user_id, 'final_hint')}"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(t(user_id, "btn_send"),    callback_data="final:send"),
        types.InlineKeyboardButton(t(user_id, "btn_fix_fio"), callback_data="back:fio"),
        types.InlineKeyboardButton(t(user_id, "btn_restart"), callback_data="final:restart"),
    )
    # Страховка от лимита Telegram в 4096 символов: если сводка почему-то
    # получилась длиннее — отправляем короткую версию, но с той же кнопкой,
    # иначе пользователь остался бы без кнопки «Отправить».
    if len(summary) > 3900:
        summary = summary[:3800] + "\n…\n\n" + t(user_id, "final_hint")
    try:
        bot.send_message(chat_id, summary, parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        logger.warning(f"Сводка не отправилась ({e}), отправляю без разметки")
        bot.send_message(
            chat_id,
            f"{t(user_id, 'lbl_equipment')}: {state.get('equipment', '')}\n"
            f"{t(user_id, 'lbl_type')}: {state.get('work_type', '')}\n"
            f"{t(user_id, 'lbl_moto')}: {state.get('moto', '')}\n"
            f"{t(user_id, 'lbl_fio')}: {state.get('fio', '')}\n\n"
            f"{t(user_id, 'final_hint')}",
            reply_markup=markup,
        )


# ══════════════════════════════════════════════════════════════════
# ФИНАЛ: ОТПРАВКА В РЕЕСТР ТО ПЛАТФОРМЫ
# ══════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("final:"))
def cb_final(call):
    ack(call)
    uid = call.from_user.id
    action = call.data[6:]
    state = get_state(uid)

    if action == "restart":
        clear_state(uid)
        show_equipment_list(call.message.chat.id, uid, call.message.message_id)
        return

    if action == "retry":
        # Повтор после ошибки: введённые данные не потеряны — показываем сводку снова
        if state_has(state, "final"):
            state.pop("sending", None)
            set_state(uid, state)
            show_final_confirm(call.message.chat.id, uid, state)
        else:
            clear_state(uid)
            show_equipment_list(call.message.chat.id, uid, call.message.message_id)
        return

    if action != "send":
        return

    # Проверяем ВСЕ поля, которые реально пойдут в таблицу.
    # Иначе нажатие старой кнопки могло записать полупустую строку.
    if not state_has(state, "final"):
        if not state:
            # Состояние пустое — запись уже сохранена, а нажали ту же кнопку снова.
            # НЕ редактируем сообщение: подтверждение «✅ Запись добавлена» должно остаться.
            bot.send_message(call.message.chat.id, t(uid, "already_done"),
                             parse_mode="Markdown", reply_markup=get_main_menu_keyboard(uid))
        else:
            say_expired(call.message.chat.id, uid, call.message.message_id)
        return

    # Защита от двойного нажатия, пока идёт запрос к платформе.
    # Под замком, т.к. telebot обрабатывает обновления в нескольких потоках.
    with _send_lock:
        state = get_state(uid)
        if state.get("sending"):
            logger.info(f"Повторное нажатие «Отправить» от {uid} — игнорируем")
            return
        if not state_has(state, "final"):
            return
        state["sending"] = True
        set_state(uid, state)
        payload = dict(state)      # копия: дальше состояние может быть очищено
        payload["tg_id"] = uid
        payload["author"] = who_is(call.from_user)

    try:
        bot.edit_message_text(t(uid, "saving"), call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.warning(f"Не удалось показать «сохраняю»: {e}")

    success, detail = write_record(payload)

    if success:
        # Чистим состояние СРАЗУ после успешной записи: если ниже что-то
        # упадёт (Telegram, уведомление), повторный тап не создаст дубль строки.
        clear_state(uid)

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(uid, "btn_add_more"), callback_data="start:new"))
        try:
            bot.edit_message_text(
                t(uid, "saved",
                  sheet=md(f"{detail['unit']} — {detail['label']}"),
                  date=payload.get("date", ""),
                  equipment=md(payload.get("equipment", "")),
                  wtype=payload.get("work_type", "")),
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=markup,
            )
        except Exception as e:
            logger.warning(f"Запись сохранена, но подтверждение не отправилось: {e}")
            try:
                bot.send_message(call.message.chat.id, "✅ Запись сохранена / Жазба сақталды",
                                 reply_markup=markup)
            except Exception:
                pass

        # Платформа записала, но с оговоркой (например, показание ушло в
        # примечание: у машины не задан счётчик) — говорим об этом прямо
        if detail.get("warning"):
            try:
                bot.send_message(call.message.chat.id, "⚠️ " + detail["warning"])
            except Exception as e:
                logger.warning(f"Предупреждение не отправилось: {e}")

        # Уведомление менеджеру — всегда по-русски
        if NOTIFY_CHAT_ID:
            try:
                bot.send_message(
                    NOTIFY_CHAT_ID,
                    f"📬 *Новая запись в реестре*\n\n"
                    f"📅 {payload.get('date', '')} | 🔧 {md(payload.get('equipment', ''))}\n"
                    f"🔁 {payload.get('work_type', '')} | ⏱ {md(cut(payload.get('moto', ''), 60))}\n"
                    f"🔩 {md(cut(payload.get('parts_old', 'НП'), 200))} → "
                    f"{md(cut(payload.get('parts_new', 'НП'), 200))}\n"
                    f"📝 {md(cut(payload.get('description', ''), 1500))}\n"
                    f"👷 {md(cut(payload.get('fio', ''), 120))}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.warning(f"Уведомление не отправлено: {e}")

    else:
        # Ошибка записи: состояние сохраняем, снимаем только флаг отправки
        state = get_state(uid)
        state.pop("sending", None)
        set_state(uid, state)

        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(uid, "btn_retry"), callback_data="final:retry"))
        if detail == "SHEET_GONE":
            text = t(uid, "sheet_gone", title=md(payload.get("equipment", "?")))
            get_equipment_list(force=True)
        else:
            text = t(uid, "save_error", err=cut(detail, 500))
        try:
            bot.edit_message_text(
                text, call.message.chat.id, call.message.message_id,
                parse_mode="Markdown", reply_markup=markup,
            )
        except Exception as e:
            logger.warning(f"Не удалось показать ошибку: {e}")
            bot.send_message(call.message.chat.id, text, reply_markup=markup)


# ══════════════════════════════════════════════════════════════════
# КНОПКИ «НАЗАД»
# ══════════════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("back:"))
def cb_back(call):
    ack(call)
    uid    = call.from_user.id
    target = call.data[5:]
    state  = get_state(uid)
    cid    = call.message.chat.id
    mid    = call.message.message_id

    if target == "equipment" or not state.get("equipment"):
        show_equipment_list(cid, uid, mid, page=state.get("page", 0))
        return

    # Шаг, на который возвращаемся, должен быть доступен: иначе это нажатие
    # старой кнопки из другой (уже завершённой или заменённой) записи.
    need = {
        "work_type":   "work_type",
        "description": "description",
        "moto":        "moto",
        "parts":       "parts",
        "parts_old":   "parts",
        "fio":         "fio",
    }.get(target)
    if need and not state_has(state, need):
        say_expired(cid, uid, mid)
        return

    if target == "work_type":
        # вернуться к выбору вида работ
        state["step"] = "work_type"
        set_state(uid, state)
        bot.edit_message_text(
            f"✅ {t(uid, 'lbl_equipment')}: *{md(state['equipment'])}*\n\n"
            f"{t(uid, 'step', n=2)} — {t(uid, 'step2_prompt')}",
            cid, mid, parse_mode="Markdown", reply_markup=work_type_markup(uid),
        )

    elif target == "description":
        state["step"] = "description"
        set_state(uid, state)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(uid, "back"), callback_data="back:work_type"))
        bot.edit_message_text(
            f"✅ {t(uid, 'lbl_equipment')}: *{md(state['equipment'])}*\n"
            f"✅ {t(uid, 'lbl_type')}: *{state.get('work_type', '')}*\n\n"
            f"{t(uid, 'step', n=3)} — {t(uid, 'step3_prompt')}\n"
            f"{t(uid, 'step3_hint')}",
            cid, mid, parse_mode="Markdown", reply_markup=markup,
        )

    elif target == "moto":
        state["step"] = "moto"
        set_state(uid, state)
        bot.edit_message_text(
            f"✅ {t(uid, 'lbl_equipment')}: *{md(state['equipment'])}*\n"
            f"✅ {t(uid, 'lbl_type')}: *{state.get('work_type', '')}*\n"
            f"✅ {t(uid, 'lbl_works')}: _{md(state.get('description', ''))}_\n\n"
            f"{t(uid, 'step', n=4)} — {t(uid, 'step4_prompt')}\n"
            f"{t(uid, 'step4_hint')}",
            cid, mid, parse_mode="Markdown", reply_markup=moto_markup(uid),
        )

    elif target in ("parts", "parts_old"):
        state["step"] = "parts"
        set_state(uid, state)
        ask_parts(cid, uid, mid, state)

    elif target == "fio":
        state["step"] = "fio"
        set_state(uid, state)
        ask_fio(cid, uid, mid, state)


# ══════════════════════════════════════════════════════════════════
# ЛЮБОЙ ДРУГОЙ ТЕКСТ — подсказка с кнопками (регистрируется ПОСЛЕДНИМ)
# ══════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_unknown(message):
    uid = message.from_user.id
    if uid not in user_lang:
        show_language_choice(message.chat.id, uid)
        return
    bot.send_message(
        message.chat.id,
        t(uid, "unknown_text"),
        reply_markup=get_main_menu_keyboard(uid),
    )


# ══════════════════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info("🤖 Запускаю бота...")

    # Проверка связи с платформой при старте
    try:
        _eq = get_equipment_list(force=True)
        logger.info(f"✅ Платформа CIS: {PLATFORM_URL}. Техника в меню: {len(_eq)}")
    except Exception as _e:
        logger.error(f"❌ Нет связи с платформой: {_e}")
        logger.error("   Проверь PLATFORM_URL и PLATFORM_KEY "
                     "(ключ — Админ-панель → Интеграции)")

    setup_bot_commands()

    logger.info(f"✅ Бот готов. Язык по умолчанию: {DEFAULT_LANG}")
    bot.infinity_polling(skip_pending=True, none_stop=True, interval=1)
