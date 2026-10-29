"""
Локальные тесты для remont_bot_5.py.
Платформа CIS и Telegram полностью подменены заглушками — ничего наружу не уходит.
Запуск:  python3 test_bot.py
"""
import os
import re
import sys
import types as pytypes
from urllib.parse import unquote

# ── 1. Переменные окружения ───────────────────────────────────────
os.environ.update({
    "BOT_TOKEN": "123:TEST",
    "PLATFORM_URL": "https://platform.test",
    "PLATFORM_KEY": "k" * 40,
    "ANTHROPIC_API_KEY": "test-key",
    "NOTIFY_CHAT_ID": "-100999",
})

# ── 2. Импорт бота ────────────────────────────────────────────────
import remont_bot_5 as B

# ── 3. Заглушка платформы (API реестра ТО) ────────────────────────
# (код, название как в платформе, категория) — как на проде
UNITS = [
    ("E1", "E1 · ТСП 94163. Блендер · 42SKA06", "Блендер"),
    ("B1", "B1 · Насосная установка · 882AHDE", "Насос ГРП"),
    ("B2", "B2 · Насосная установка · 12ABN06", "Насос ГРП"),
    ("B3", "B3 · Насосная установка · 883AHDE", "Насос ГРП"),
    ("J1", "J1 · Mercedes-Benz Actros · 032YO06", "Тягач"),
    ("J2", "J2 · Mercedes-Benz Actros · 043YO06", "Тягач"),
    ("J8", "J8 · SHACMAN SX42584V324 · 650ADK06", "Тягач"),
    ("H6", "H6 · Трал · 59ADV06", "Трал"),
    ("H7", "H7 · Трал · 28ABY06", "Трал"),
    ("O1", "O1 · Водогрейная установка", "Водогрейная установка"),
    ("N1", "N1 · Toyota Hilux · 886AHL06", "Легковые"),
    ("N2", "N2 · Toyota Hilux · 779AHF06", "Легковые"),
    ("N7", "N7 · Toyota Hilux · 701BDC01", "Легковые"),
]
TITLE = {c: t for c, t, _ in UNITS}


def rec(date, kind, description, reading=None, unit="", executor=""):
    return {"date": date, "kind": kind, "description": description,
            "reading": reading, "unit": unit if reading is not None else "",
            "date_start": date, "date_end": date, "duration": "1 день",
            "parts_removed": None, "parts_new": None, "executor": executor,
            "controller": executor, "comment": None, "reason": None,
            "state_before": None, "state_after": None, "source": "Импорт F-002"}


RECORDS = {c: [] for c, _, _ in UNITS}
RECORDS["E1"].append(rec("2026-01-01", "ТО", "замена масла", 100, "м/ч",
                         "Аяпов А.А."))
RECORDS["B3"] += [rec("2026-02-05", "Р", "замена плунжерной пары", 2500, "м/ч",
                      "Ким В.В."),
                  rec("2026-03-06", "ТО", "плановое ТО-2", 2700, "м/ч",
                      "Ким В.В.")]

POSTS = []                  # [(код, тело запроса)]
CALLS = []                  # [(метод, путь, params)]
PLATFORM = {"fail": None, "warning": None}


def fake_platform(method, path="", **kw):
    CALLS.append((method, path, kw.get("params")))
    params = kw.get("params") or {}
    if path == "" and method == "GET":
        last = params.get("last")
        units = []
        for code, title, cat in UNITS:
            u = {"code": code, "title": title, "category": cat}
            if last:
                u.update(total=len(RECORDS[code]), records=RECORDS[code][-last:])
            units.append(u)
        return {"ok": True, "platform": True, "units": units}
    m = re.fullmatch(r"/([^/]+)(/history)?", path)
    code = unquote(m.group(1))
    if code not in TITLE:
        raise B.PlatformError(f"Техники с кодом {code} нет в платформе.", 404)
    if m.group(2):
        rows = RECORDS[code]
        return {"ok": True, "unit": {"code": code, "title": TITLE[code]},
                "total": len(rows), "records": rows[-params.get("limit", 40):]}
    if method == "POST":
        if PLATFORM["fail"]:
            raise B.PlatformError(PLATFORM["fail"], 400)
        body = kw["json"]
        POSTS.append((code, body))
        RECORDS[code].append(rec(body["date"], body["kind"], body["description"],
                                 body.get("reading"), "м/ч", body["executor"]))
        d = body["date"].split("-")
        return {"ok": True, "record": {"label": f"{body['kind']} от {d[2]}.{d[1]}.{d[0]}",
                                       "unit": TITLE[code]},
                "warning": PLATFORM["warning"]}
    raise AssertionError((method, path))


B.platform = fake_platform

# ── 4. Заглушка Telegram ──────────────────────────────────────────
SENT = []          # все отправленные/отредактированные сообщения
_MSG_ID = [1000]


class FakeMsgObj:
    def __init__(self, mid, chat_id):
        self.message_id = mid
        self.chat = pytypes.SimpleNamespace(id=chat_id)


def fake_send_message(chat_id, text, **kw):
    _MSG_ID[0] += 1
    SENT.append({"kind": "send", "chat": chat_id, "text": text,
                 "id": _MSG_ID[0], "markup": kw.get("reply_markup"),
                 "parse": kw.get("parse_mode")})
    return FakeMsgObj(_MSG_ID[0], chat_id)


def fake_edit_message_text(text, chat_id, message_id, **kw):
    SENT.append({"kind": "edit", "chat": chat_id, "text": text,
                 "id": message_id, "markup": kw.get("reply_markup"),
                 "parse": kw.get("parse_mode")})
    return FakeMsgObj(message_id, chat_id)


B.bot.send_message        = fake_send_message
B.bot.edit_message_text   = fake_edit_message_text
B.bot.delete_message      = lambda *a, **k: None
B.bot.send_chat_action    = lambda *a, **k: None
B.bot.answer_callback_query = lambda *a, **k: None
B.ask_claude = lambda q, data, uid=None: f"[AI отвечает на: {q[:40]}]"

UID, CHAT = 555, 777
USER = pytypes.SimpleNamespace(id=UID, first_name="Асхат", last_name=None,
                               username="ash")


def msg(text):
    return pytypes.SimpleNamespace(
        text=text,
        chat=pytypes.SimpleNamespace(id=CHAT),
        from_user=USER,
    )


def cb(data, mid=None):
    return pytypes.SimpleNamespace(
        data=data, id="cbid",
        from_user=USER,
        message=FakeMsgObj(mid or SENT[-1]["id"], CHAT),
    )


def last(n=1):
    return SENT[-n]["text"]


def buttons(entry=None):
    e = entry or SENT[-1]
    mk = e.get("markup")
    if mk is None or not hasattr(mk, "keyboard"):
        return []
    out = []
    for row in mk.keyboard:
        for b in row:
            d = b if isinstance(b, dict) else b.__dict__
            out.append((d.get("text"), d.get("callback_data")))
    return out


def eq(code):
    """callback_data кнопки техники на последнем экране."""
    return [b for b in buttons() if b[1] == f"eq:{code}"][0][1]


def posts(code):
    return [b for c, b in POSTS if c == code]


# ══════════════════════════════════════════════════════════════════
FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name} {extra}")
        FAILS.append(name)


# ── ТЕСТ 1: список техники ────────────────────────────────────────
print("\n[1] Список техники из платформы")
items = B.get_equipment_list(force=True)
check("найдено 13 единиц техники", len(items) == 13, len(items))
check("порядок — как в платформе", [i["id"] for i in items] == [u[0] for u in UNITS])
check("на кнопке — код, марка и госномер",
      items[1]["display"] == "B1 · Насосная установка · 882AHDE", items[1])
check("callback_data короткий (< 64 байт)",
      all(len(f"eq:{i['id']}".encode()) < 64 for i in items))
n_calls = len(CALLS)
B.get_equipment_list()
check("повторный запрос — из кэша", len(CALLS) == n_calls)

# ── ТЕСТ 2: определение техники в вопросе ─────────────────────────
print("\n[2] Поиск техники по тексту вопроса")
cases = [
    ("Когда последний раз чинили насос 883?",            "B3"),
    ("Какое состояние Mercedes 032YO?",                  "J1"),
    ("мерс 043 что делали",                              "J2"),
    ("Кто обслуживал SHACMAN 650?",                      "J8"),
    ("шакман 650 ремонт",                                "J8"),
    ("трал 28 когда ремонтировали",                      "H7"),
    ("что с блендером",                                  "E1"),
    ("Toyota hilux 701bdc01 история",                    "N7"),
    ("хилукс 701 жағдайы қандай",                        "N7"),
    ("701bdc01",                                         "N7"),
    ("883 сорабын соңғы рет қашан жөндеді?",             "B3"),
    ("водогрейная установка",                            "O1"),
    ("12ABN обслуживание",                               "B2"),
]
for q, expected in cases:
    found, score = B.find_sheet_for_question(q, items)
    got = found["id"] if found else None
    check(f"«{q[:38]}» → {expected}", got == expected, f"получено: {got} (score={score})")

for q in ["Сколько всего записей за март?", "Привет", "какая техника обслуживалась вчера",
          "что с насосами"]:
    found, score = B.find_sheet_for_question(q, items)
    check(f"общий вопрос «{q[:30]}» → обзор по всей технике", found is None,
          f"ошибочно выбрано: {found['display'] if found else None}")

# ── ТЕСТ 3: чтение данных ─────────────────────────────────────────
print("\n[3] Чтение реестра для AI")
before = len(CALLS)
txt_all = B.read_all_tabs(items, UID)
check("обзор — ОДНИМ запросом к платформе", len(CALLS) - before == 1, CALLS[before:])
check("обзор просит последние записи каждой машины",
      CALLS[-1][2] == {"last": B.ROWS_PER_TAB_OVERVIEW}, CALLS[-1])
check("в обзор попал B3 (883AHDE)", "883AHDE" in txt_all)
check("в обзоре есть Блендер", "Блендер" in txt_all)
check("машины без записей в обзор не попали", "701BDC01" not in txt_all)
one = B.read_single_tab([i for i in items if i["id"] == "B3"][0], UID)
check("по одной технике видны обе записи", one.count("Запись") == 2, one)
check("данные разобраны по колонкам",
      "Моточасы=2700 м/ч" in one and "Вид=ТО" in one and "Дата=06.03.2026" in one, one)
empty = B.read_single_tab([i for i in items if i["id"] == "N7"][0], UID)
check("пустая история — понятный текст", "записей нет" in empty, empty)

# ── ТЕСТ 4: запись в реестр ───────────────────────────────────────
print("\n[4] Запись в реестр ТО платформы")
ok, detail = B.write_record({
    "code": "N7", "equipment": TITLE["N7"], "work_type": "ТО",
    "description": "Замена масла и фильтров", "moto": "45 000",
    "parts_old": "НП", "parts_new": "OC90", "fio": "Мубинов Ж.",
    "date": "29.10.2026", "tg_id": UID, "author": "Асхат (@ash)",
})
check("запись успешна", ok is True, detail)
check("в ответе — метка записи и машина",
      detail["label"] == "ТО от 29.10.2026" and detail["unit"] == TITLE["N7"], detail)
code, body = POSTS[-1]
check("запись ушла в выбранную машину", code == "N7")
check("вид, дата, описание",
      (body["kind"], body["date"], body["description"])
      == ("ТО", "2026-10-29", "Замена масла и фильтров"), body)
check("показание — число, счётчик выбирает платформа",
      body["reading"] == 45000.0 and "meter" not in body, body)
check("начало = конец = дата, 1 день",
      body["date_start"] == body["date_end"] == "2026-10-29"
      and body["duration"] == "1 день", body)
check("запчасти: НП не передаётся, установленная — да",
      "parts_removed" not in body and body["parts_new"] == "OC90", body)
check("ФИО — исполнитель и контролирующий",
      body["executor"] == body["controller"] == "Мубинов Ж.", body)
check("автор из Telegram передан", body["tg_id"] == UID and body["author"] == "Асхат (@ash)")

B.write_record({"code": "B3", "equipment": TITLE["B3"], "work_type": "Р",
                "description": "тест", "moto": "НП", "fio": "Тест Т.",
                "date": "29.10.2026"})
check("моточасы НП → без показания", "reading" not in posts("B3")[-1], posts("B3")[-1])
check("другие машины не задеты", not posts("E1"))

ok3, d3 = B.write_record({"code": "Z99", "equipment": "Удалённая", "work_type": "ТО",
                          "description": "x", "moto": "НП", "fio": "x",
                          "date": "29.10.2026"})
check("удалённая техника → честная ошибка", ok3 is False and d3 == "SHEET_GONE", (ok3, d3))

for text, value in [("16 500", 16500.0), ("16500,5", 16500.5), ("16 500 м/ч", 16500.0),
                    ("НП", None), ("нп", None), ("много", False), ("", False)]:
    check(f"моточасы «{text}» → {value}", B.parse_moto(text) == value, B.parse_moto(text))

# ── ТЕСТ 5: полный диалог по-русски ───────────────────────────────
print("\n[5] Полный диалог (русский)")
SENT.clear()
B.user_lang[UID] = "ru"
B.cmd_new(msg("/new"))
check("шаг 1: список техники", "Шаг 1 из 6" in last())
check("кнопка техники с кодом и госномером есть",
      ("N7 · Toyota Hilux · 701BDC01", "eq:N7") in buttons(), buttons()[:3])
check("кнопка «Обновить список» есть", any("Обновить" in (b[0] or "") for b in buttons()))

B.cb_equipment(cb(eq("N7")))
check("шаг 2: вид работ", "Шаг 2 из 6" in last() and "701BDC01" in last())
B.cb_work_type(cb("wt:ТО"))
check("шаг 3: описание", "Шаг 3 из 6" in last())
B.handle_description(msg("Замена тормозных колодок"))
check("подтверждение описания", "Всё верно?" in last())
B.cb_description_confirm(cb("desc:ok"))
check("шаг 4: моточасы", "Шаг 4 из 6" in last())
B.handle_moto(msg("много"))
check("не число → переспросили", "Не понял число" in last() and B.get_state(UID)["step"] == "moto")
B.handle_moto(msg("45 100"))
check("шаг 5: запчасти", "Шаг 5 из 6" in last())
B.cb_parts(cb("parts:enter"))
check("запрос старой детали", "снята" in last())
B.handle_parts_old(msg("D1234"))
check("запрос новой детали", "установлена" in last())
B.handle_parts_new(msg("D5678"))
check("шаг 6: ФИО", "Шаг 6 из 6" in last())
B.handle_fio(msg("Мубинов Ж.М."))
check("итоговая сводка", "ИТОГОВАЯ ЗАПИСЬ" in last() and "D5678" in last())
check("дата — местная", B.local_now().strftime("%d.%m.%Y") in last())

n7_before = len(posts("N7"))
B.cb_final(cb("final:send"))
check("сохранено", "Запись добавлена" in last(2) or "Запись добавлена" in last(),
      [SENT[-2]["text"][:40], SENT[-1]["text"][:40]])
check("в подтверждении — машина и запись",
      any("701BDC01" in e["text"] and "ТО от" in e["text"] for e in SENT[-3:]))
w = posts("N7")[-1]
check("запись ушла в нужную машину", len(posts("N7")) == n7_before + 1)
check("описание и ФИО на месте", w["description"] == "Замена тормозных колодок"
      and w["executor"] == "Мубинов Ж.М.")
check("запчасти снята → установлена", w["parts_removed"] == "D1234" and w["parts_new"] == "D5678")
check("показание 45 100", w["reading"] == 45100.0)
check("автор — из Telegram", w["tg_id"] == UID and w["author"] == "Асхат (@ash)", w)
check("уведомление менеджеру отправлено", any(e["chat"] == "-100999" for e in SENT))
check("состояние очищено", B.get_state(UID) == {})

# ── ТЕСТ 6: полный диалог по-казахски ─────────────────────────────
print("\n[6] Полный диалог (қазақша)")
SENT.clear()
B.cb_lang(cb("lang:kz", 1))
check("язык переключён", B.lang_of(UID) == "kz")
check("приветствие на казахском", "қош келдіңіз" in SENT[-2]["text"])
kb = SENT[0].get("markup")
check("нижнее меню на казахском", "Жаңа жазба" in str(kb.keyboard))

B.btn_new_handler(msg("📋 Жаңа жазба ТҚ/Жөндеу"))
check("кнопка на казахском распознана", "1-қадам / 6" in last())
B.cb_equipment(cb(eq("E1")))
check("2-қадам", "2-қадам / 6" in last())
B.cb_work_type(cb("wt:Р"))
B.handle_description(msg("Май ауыстыру"))
B.cb_description_confirm(cb("desc:ok"))
check("4-қадам моточасы", "Мотосағат" in last())
B.handle_moto(msg("көп"))
check("не число → переспросили по-казахски", "Санды түсінбедім" in last())
B.cb_moto_np(cb("moto:НП"))
check("5-қадам запчасти", "Қосалқы бөлшектер" in last())
B.cb_parts(cb("parts:np"))
check("6-қадам ФИО", "Орындаушының аты-жөні" in last())
B.handle_fio(msg("Мубинов Ж."))
check("итог на казахском", "ҚОРЫТЫНДЫ ЖАЗБА" in last() and "Тізілімге жіберу" in str(buttons()))
B.cb_final(cb("final:send"))
blender = posts("E1")[-1]
check("в РЕЕСТР всё равно пишется по-русски",
      blender["kind"] == "Р" and blender["duration"] == "1 день", blender)
check("моточасы НП → без показания", "reading" not in blender)
check("подтверждение на казахском", any("ТҚ тізіліміне қосылды" in e["text"] for e in SENT[-3:]))

# ── ТЕСТ 7: кнопки «Назад» ────────────────────────────────────────
print("\n[7] Кнопки «Назад»")
SENT.clear()
B.user_lang[UID] = "ru"
B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("E1")))
B.cb_work_type(cb("wt:ТО"))
B.handle_description(msg("тест"))
B.cb_description_confirm(cb("desc:ok"))
B.cb_moto_np(cb("moto:НП"))
check("на шаге запчастей", "Шаг 5 из 6" in last())
B.cb_back(cb("back:moto"))
check("назад → шаг 4", "Шаг 4 из 6" in last())
B.cb_moto_np(cb("moto:НП"))
B.cb_parts(cb("parts:np"))
check("на шаге ФИО", "Шаг 6 из 6" in last())
B.cb_back(cb("back:parts"))
check("назад с ФИО → шаг 5", "Шаг 5 из 6" in last())
B.cb_parts(cb("parts:enter"))
B.cb_back(cb("back:parts"))
check("назад из ввода P/N → шаг 5", "Шаг 5 из 6" in last())
B.cb_back(cb("back:equipment"))
check("назад → шаг 1", "Шаг 1 из 6" in last())

# ── ТЕСТ 8: сессия устарела / технику удалили ─────────────────────
print("\n[8] Крайние случаи")
SENT.clear()
B.clear_state(UID)
B.cb_work_type(cb("wt:ТО", 1))
check("после рестарта бота — понятное сообщение", "Сессия устарела" in last())
B.cb_equipment(cb("eq:Z999", 1))
check("нажали кнопку удалённой техники → список обновлён",
      "Шаг 1 из 6" in last() and "больше не найдена в платформе" in last())

# ── ТЕСТ 9: старые кнопки не создают мусорных записей ─────────────
print("\n[9] Старые кнопки / мусорные записи")
SENT.clear()
B.user_lang[UID] = "ru"
B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("E1")))
B.cb_work_type(cb("wt:ТО"))
B.handle_description(msg("первая запись"))
B.cb_description_confirm(cb("desc:ok"))
B.cb_moto_np(cb("moto:НП"))
B.cb_parts(cb("parts:np"))
B.handle_fio(msg("Иванов И.И."))
OLD_SUMMARY_ID = SENT[-1]["id"]
check("сводка первой записи получена", "ИТОГОВАЯ ЗАПИСЬ" in last())

B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("N7")))
check("начата запись по другой технике", "701BDC01" in last())

n_posts = len(POSTS)
B.cb_back(cb("back:fio", OLD_SUMMARY_ID))
check("старая кнопка «Исправить ФИО» → понятное сообщение, без падения",
      "Сессия устарела" in last(), last()[:60])
B.cb_final(cb("final:send", OLD_SUMMARY_ID))
check("старая кнопка «Отправить» не пишет полупустую запись",
      len(POSTS) == n_posts, POSTS[n_posts:])
check("вместо записи — сообщение о сессии", "Сессия устарела" in last())

# ── ТЕСТ 10: двойное нажатие «Отправить» ─────────────────────────
print("\n[10] Двойное нажатие «Отправить»")
SENT.clear()
B.clear_state(UID)
B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("J8")))
B.cb_work_type(cb("wt:Р"))
B.handle_description(msg("сварка рамы"))
B.cb_description_confirm(cb("desc:ok"))
B.handle_moto(msg("120 000"))
B.cb_parts(cb("parts:np"))
B.handle_fio(msg("Петров П.П."))
SUM_ID = SENT[-1]["id"]
B.cb_final(cb("final:send", SUM_ID))
w_j8 = len(posts("J8"))
B.cb_final(cb("final:send", SUM_ID))
B.cb_final(cb("final:send", SUM_ID))
check("повторные нажатия не создают дублей", len(posts("J8")) == w_j8 == 1, w_j8)

# ── ТЕСТ 11: платформа записала с оговоркой ──────────────────────
print("\n[11] Предупреждение платформы")
SENT.clear()
B.clear_state(UID)
PLATFORM["warning"] = "У машины не задан счётчик — показание записано в примечание."
B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("N1")))
B.cb_work_type(cb("wt:ТО"))
B.handle_description(msg("ТО, масло и фильтры"))
B.cb_description_confirm(cb("desc:ok"))
B.handle_moto(msg("226 500"))
B.cb_parts(cb("parts:np"))
B.handle_fio(msg("Ким В."))
B.cb_final(cb("final:send", SENT[-1]["id"]))
PLATFORM["warning"] = None
check("запись сохранена", posts("N1") and posts("N1")[-1]["reading"] == 226500.0)
check("предупреждение показано отдельным сообщением",
      any(e["text"].startswith("⚠️ У машины не задан счётчик") for e in SENT),
      [e["text"][:30] for e in SENT])

# ── ТЕСТ 12: платформа отказала → повтор без потери данных ───────
print("\n[12] Отказ платформы и повтор")
SENT.clear()
B.clear_state(UID)
B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("H6")))
B.cb_work_type(cb("wt:ТО"))
B.handle_description(msg("замена колодок"))
B.cb_description_confirm(cb("desc:ok"))
B.handle_moto(msg("1000"))
B.cb_parts(cb("parts:np"))
B.handle_fio(msg("Сидоров С.С."))
SUM_ID = SENT[-1]["id"]
PLATFORM["fail"] = "Показание меньше предыдущего (1200 на 01.10.2026)."
B.cb_final(cb("final:send", SUM_ID))
check("показана ошибка с объяснением платформы",
      "Ошибка при сохранении" in last() and "меньше предыдущего" in last(), last())
check("данные сохранены в состоянии", B.get_state(UID).get("fio") == "Сидоров С.С.")
PLATFORM["fail"] = None
B.cb_final(cb("final:retry", SUM_ID))
check("повтор показывает сводку с данными",
      "ИТОГОВАЯ ЗАПИСЬ" in last() and "Сидоров С.С." in last())
B.cb_final(cb("final:send", SENT[-1]["id"]))
check("после повтора запись сохранена", "Запись добавлена" in last(2) or "Запись добавлена" in last())
check("данные ушли в H6", posts("H6")[-1]["description"] == "замена колодок")

# ── ТЕСТ 13: «Задать ещё вопрос» не съедает текст записи ─────────
print("\n[13] Q&A не перехватывает текст записи")
SENT.clear()
B.clear_state(UID)
B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("E1")))
B.cb_work_type(cb("wt:ТО"))
check("бот ждёт описание", B.get_state(UID)["step"] == "description")
B.cb_qa_another(cb("qa:another"))
check("режим вопросов сбросил незаконченную запись", B.get_state(UID) == {})
B.qa_mode_users.discard(UID)

# ── ТЕСТ 14: повторный тап после успешного сохранения ────────────
print("\n[14] Повторный тап после сохранения")
SENT.clear()
B.clear_state(UID)
B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("N2")))
B.cb_work_type(cb("wt:ТО"))
B.handle_description(msg("ТО-1"))
B.cb_description_confirm(cb("desc:ok"))
B.cb_moto_np(cb("moto:НП"))
B.cb_parts(cb("parts:np"))
B.handle_fio(msg("Ким В."))
SUM_ID = SENT[-1]["id"]
B.cb_final(cb("final:send", SUM_ID))
conf_text = [e for e in SENT if e["id"] == SUM_ID][-1]["text"]
check("подтверждение показано", "Запись добавлена" in conf_text)
w_n2 = len(posts("N2"))
B.cb_final(cb("final:send", SUM_ID))
check("повторный тап не пишет дубль", len(posts("N2")) == w_n2)
last_edit_of_summary = [e for e in SENT if e["id"] == SUM_ID][-1]["text"]
check("подтверждение НЕ затёрто «сессия устарела»",
      "Запись добавлена" in last_edit_of_summary, last_edit_of_summary[:60])
check("вместо этого — отдельное сообщение «уже сохранена»",
      "уже сохранена" in SENT[-1]["text"], SENT[-1]["text"][:60])

# ── ТЕСТ 15: шаг «запчасти» нельзя перескочить ───────────────────
print("\n[15] Шаг запчастей не перескакивается")
SENT.clear()
B.clear_state(UID)
B.cmd_new(msg("/new"))
B.cb_equipment(cb(eq("B2")))
B.cb_work_type(cb("wt:Р"))
B.handle_description(msg("замена сальника"))
B.cb_description_confirm(cb("desc:ok"))
B.cb_moto_np(cb("moto:НП"))
check("бот на шаге запчастей", B.get_state(UID)["step"] == "parts")
B.cb_back(cb("back:fio"))
check("прыжок на ФИО в обход запчастей заблокирован",
      B.get_state(UID) == {} or B.get_state(UID).get("step") != "fio",
      B.get_state(UID))

print("\n[16] Экранирование Markdown и режим вопросов")
check("подчёркивание и звёздочка экранируются",
      B.md("Насос_2 *новый*") == r"Насос\_2 \*новый\*", B.md("Насос_2 *новый*"))

SENT.clear()
B.qa_mode_users.add(UID)
B.handle_qa_question(msg("Когда чинили насос 883?"))
check("режим вопросов отвечает", "AI отвечает" in last())
check("для вопроса прочитана история B3", ("GET", "/B3/history", {"limit": 40}) in CALLS)
check("после ответа режим сброшен", UID not in B.qa_mode_users)

# ── Итог ──────────────────────────────────────────────────────────
print("\n" + "═" * 60)
if FAILS:
    print(f"❌ ПРОВАЛЕНО: {len(FAILS)}")
    for f in FAILS:
        print("   •", f)
    sys.exit(1)
print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
