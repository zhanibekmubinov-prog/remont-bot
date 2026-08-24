"""
Локальные тесты для remont_bot_5.py.
Google Sheets и Telegram полностью подменены заглушками — ничего наружу не уходит.
Запуск:  python3 test_bot.py
"""
import os
import sys
import types as pytypes

# ── 1. Переменные окружения ───────────────────────────────────────
os.environ.update({
    "BOT_TOKEN": "123:TEST",
    "GOOGLE_SHEET_ID": "TESTSHEET",
    "GOOGLE_CREDENTIALS_JSON": "{}",
    "ANTHROPIC_API_KEY": "test-key",
    "NOTIFY_CHAT_ID": "-100999",
    "SKIP_SHEETS": "Свод",
})

# ── 2. Заглушка Google Sheets ─────────────────────────────────────
class WorksheetNotFound(Exception): pass
class SpreadsheetNotFound(Exception): pass
class APIError(Exception): pass


class FakeWorksheet:
    def __init__(self, gid, title, rows=None):
        self.id, self.title = gid, title
        # rows: словарь {номер_строки: [значения A..Q]}
        self.cells = dict(rows or {})
        self.writes = []

    def col_values(self, col):
        if not self.cells:
            return []
        last = max(self.cells)
        out = []
        for r in range(1, last + 1):
            row = self.cells.get(r, [])
            out.append(row[col - 1] if len(row) >= col else "")
        while out and out[-1] == "":
            out.pop()
        return out

    def update(self, values=None, range_name=None, value_input_option=None, **kw):
        assert isinstance(range_name, str), f"range_name должен быть строкой, а не {type(range_name)}"
        assert isinstance(values, list) and isinstance(values[0], list), "values должен быть [[...]]"
        assert len(values[0]) == 17, f"ожидалось 17 колонок A..Q, пришло {len(values[0])}"
        row_no = int(range_name.split(":")[0][1:])
        self.cells[row_no] = list(values[0])
        self.writes.append((range_name, values[0]))
        return {}

    def get_values(self, range_name=None, **kw):
        if not self.cells:
            return []
        rows = [self.cells.get(r, []) for r in range(1, max(self.cells) + 1)]
        while rows and not any((c or "").strip() for c in rows[-1]):
            rows.pop()
        return rows

    def get_all_values(self):
        if not self.cells:
            return []
        return [self.cells.get(r, []) for r in range(1, max(self.cells) + 1)]


HEADER = {r: [f"шапка{r}"] + [""] * 16 for r in range(1, 10)}


def make_tabs():
    return [
        FakeWorksheet(0,   "Sheet1"),
        FakeWorksheet(11,  "Пустая форма", dict(HEADER)),
        FakeWorksheet(21,  "Блендер", {**HEADER, 10: ["01.01.2026", "ТО", "масло", "Подрядчик",
                                                      "замена масла", "100", "01.01.2026",
                                                      "01.01.2026", "1 день", "", "НП", "НП",
                                                      "", "Склад у подрядчика", "", "Аяпов А.А.",
                                                      "Аяпов А.А."]}),
        FakeWorksheet(31,  "№1 Насос 882AHDE ", dict(HEADER)),
        FakeWorksheet(32,  "№2 Насос 12ABN ", dict(HEADER)),
        FakeWorksheet(33,  "№3 Насос 883AHDE", {**HEADER,
            10: ["05.02.2026", "Р", "ремонт плунжера", "Подрядчик", "замена плунжерной пары",
                 "2500", "05.02.2026", "05.02.2026", "1 день", "", "НП", "НП", "",
                 "Склад у подрядчика", "", "Ким В.В.", "Ким В.В."],
            11: ["06.03.2026", "ТО", "ТО-2", "Подрядчик", "плановое ТО-2", "2700",
                 "06.03.2026", "06.03.2026", "1 день", "", "НП", "НП", "",
                 "Склад у подрядчика", "", "Ким В.В.", "Ким В.В."]}),
        FakeWorksheet(41,  "Mercedes Actros 032YO", dict(HEADER)),
        FakeWorksheet(42,  "Mercedes Actros 043YO", dict(HEADER)),
        FakeWorksheet(51,  "SHACMAN 650ADK", dict(HEADER)),
        FakeWorksheet(61,  "Трал 59ADV06", dict(HEADER)),
        FakeWorksheet(62,  "Трал 28ABY06", dict(HEADER)),
        FakeWorksheet(71,  "Водогрейная установка ", dict(HEADER)),
        FakeWorksheet(81,  "Toyota Hilux 886AHL06", dict(HEADER)),
        FakeWorksheet(82,  "Toyota Hilux 779AHF06", dict(HEADER)),
        # ── НОВАЯ вкладка, добавленная руками, ещё без шапки ──
        FakeWorksheet(99,  "Toyota hilux 701bdc01"),
        # ── служебные ──
        FakeWorksheet(100, "_bot_users", dict(HEADER)),
        FakeWorksheet(101, "Свод", dict(HEADER)),
    ]


class FakeSpreadsheet:
    def __init__(self):
        self.title = "Реестр ТО и ремонтов"
        self._tabs = make_tabs()
        self.batch_calls = 0

    def worksheets(self):
        return list(self._tabs)

    def get_worksheet_by_id(self, gid):
        for w in self._tabs:
            if w.id == gid:
                return w
        raise WorksheetNotFound(str(gid))

    def by_title(self, title):
        for w in self._tabs:
            if w.title == title:
                return w
        raise AssertionError(f"нет вкладки {title!r}")

    def values_batch_get(self, ranges, params=None):
        self.batch_calls += 1
        out = []
        for rng in ranges:
            title = rng.split("!")[0].strip("'").replace("''", "'")
            ws = self.by_title(title)
            rows = [ws.cells.get(r, []) for r in sorted(ws.cells) if r >= 10]
            out.append({"range": rng, "values": rows})
        return {"valueRanges": out}


FAKE_SS = FakeSpreadsheet()


class FakeClient:
    def open_by_key(self, key):
        assert key == "TESTSHEET"
        return FAKE_SS


fake_gspread = pytypes.ModuleType("gspread")
fake_gspread.authorize = lambda creds: FakeClient()
fake_gspread.exceptions = pytypes.SimpleNamespace(
    WorksheetNotFound=WorksheetNotFound,
    SpreadsheetNotFound=SpreadsheetNotFound,
    APIError=APIError,
)
sys.modules["gspread"] = fake_gspread

fake_creds_mod = pytypes.ModuleType("google.oauth2.service_account")
fake_creds_mod.Credentials = pytypes.SimpleNamespace(
    from_service_account_info=lambda info, scopes=None: object()
)
sys.modules["google.oauth2.service_account"] = fake_creds_mod

# ── 3. Импорт бота ────────────────────────────────────────────────
import remont_bot_5 as B

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


def msg(text):
    return pytypes.SimpleNamespace(
        text=text,
        chat=pytypes.SimpleNamespace(id=CHAT),
        from_user=pytypes.SimpleNamespace(id=UID),
    )


def cb(data, mid=None):
    return pytypes.SimpleNamespace(
        data=data, id="cbid",
        from_user=pytypes.SimpleNamespace(id=UID),
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


# ══════════════════════════════════════════════════════════════════
FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name} {extra}")
        FAILS.append(name)


# ── ТЕСТ 1: список техники ────────────────────────────────────────
print("\n[1] Список техники из вкладок")
items = B.get_equipment_sheets(force=True)
titles = [i["display"] for i in items]
check("служебные скрыты (Sheet1/Пустая форма/_bot_users/Свод)",
      not any(x in titles for x in ["Sheet1", "Пустая форма", "_bot_users", "Свод"]), titles)
check("новая вкладка Toyota hilux 701bdc01 видна", "Toyota hilux 701bdc01" in titles)
check("найдено 13 единиц техники", len(items) == 13, len(items))
check("callback_data короткий (< 64 байт)",
      all(len(f"eq:{i['id']}".encode()) < 64 for i in items))

# ── ТЕСТ 2: определение техники в вопросе ─────────────────────────
print("\n[2] Поиск техники по тексту вопроса")
cases = [
    ("Когда последний раз чинили насос 883?",            "№3 Насос 883AHDE"),
    ("насос 3 когда обслуживали",                        "№3 Насос 883AHDE"),
    ("Какое состояние Mercedes 032YO?",                  "Mercedes Actros 032YO"),
    ("мерс 043 что делали",                              "Mercedes Actros 043YO"),
    ("Кто обслуживал SHACMAN 650?",                      "SHACMAN 650ADK"),
    ("шакман 650 ремонт",                                "SHACMAN 650ADK"),
    ("трал 28 когда ремонтировали",                      "Трал 28ABY06"),
    ("что с блендером",                                  "Блендер"),
    ("Toyota hilux 701bdc01 история",                    "Toyota hilux 701bdc01"),
    ("хилукс 701 жағдайы қандай",                        "Toyota hilux 701bdc01"),
    ("701bdc01",                                         "Toyota hilux 701bdc01"),
    ("883 сорабын соңғы рет қашан жөндеді?",             "№3 Насос 883AHDE"),
    ("водогрейная установка",                            "Водогрейная установка"),
    ("12ABN обслуживание",                               "№2 Насос 12ABN"),
]
for q, expected in cases:
    found, score = B.find_sheet_for_question(q, items)
    got = found["display"] if found else None
    check(f"«{q[:38]}» → {expected}", got == expected, f"получено: {got} (score={score})")

for q in ["Сколько всего записей за март?", "Привет", "какая техника обслуживалась вчера"]:
    found, score = B.find_sheet_for_question(q, items)
    check(f"общий вопрос «{q[:30]}» → читаем все вкладки", found is None,
          f"ошибочно выбрано: {found['display'] if found else None}")

# ── ТЕСТ 3: чтение данных ─────────────────────────────────────────
print("\n[3] Чтение данных для AI")
before = FAKE_SS.batch_calls
txt_all = B.read_all_tabs(items, UID)
check("все вкладки читаются ОДНИМ batch-запросом", FAKE_SS.batch_calls - before == 1,
      FAKE_SS.batch_calls - before)
check("в обзор попал 883 (13-я вкладка, раньше обрезалось на 10)", "883AHDE" in txt_all)
check("в обзоре есть Блендер", "Блендер" in txt_all)
one = B.read_single_tab([i for i in items if "883" in i["display"]][0], UID)
check("по одной технике видны обе записи", one.count("Запись") == 2, one)
check("данные распарсены по колонкам", "Моточасы=2700" in one and "Вид=ТО" in one)

# ── ТЕСТ 4: запись в таблицу ──────────────────────────────────────
print("\n[4] Запись в Google Sheets")
new_tab = [i for i in items if i["display"] == "Toyota hilux 701bdc01"][0]
ok, detail = B.write_to_google_sheets({
    "sheet_id": new_tab["id"], "equipment": new_tab["display"], "work_type": "ТО",
    "description": "Замена масла и фильтров", "moto": "45 000",
    "parts_old": "НП", "parts_new": "OC90", "fio": "Мубинов Ж.",
})
ws_new = FAKE_SS.get_worksheet_by_id(99)
check("запись успешна", ok is True, detail)
check("лист в ответе — правильный", detail == "Toyota hilux 701bdc01", detail)
check("новая пустая вкладка → строка 10", ws_new.writes[0][0] == "A10:Q10", ws_new.writes)
row = ws_new.writes[0][1]
check("B=ТО, D=Подрядчик, I=1 день, N=Склад у подрядчика",
      row[1] == "ТО" and row[3] == "Подрядчик" and row[8] == "1 день"
      and row[13] == "Склад у подрядчика", row)
check("K/L = запчасти", row[10] == "НП" and row[11] == "OC90", row[10:12])
check("P=Q=ФИО", row[15] == row[16] == "Мубинов Ж.")
check("шапка 1–9 не тронута", all(r not in ws_new.cells for r in range(1, 10)))

ok2, d2 = B.write_to_google_sheets({
    "sheet_id": 33, "equipment": "№3 Насос 883AHDE", "work_type": "Р",
    "description": "тест", "moto": "НП", "fio": "Тест Т.",
})
ws883 = FAKE_SS.get_worksheet_by_id(33)
check("вкладка с данными → следующая свободная строка (12)",
      ws883.writes[-1][0] == "A12:Q12", ws883.writes[-1][0])
check("другие вкладки не задеты", not FAKE_SS.get_worksheet_by_id(21).writes)

ok3, d3 = B.write_to_google_sheets({"sheet_id": 12345, "equipment": "Удалённая"})
check("удалённая вкладка → честная ошибка, НЕ первый лист",
      ok3 is False and d3 == "SHEET_GONE", (ok3, d3))
check("в Блендер (первый лист) так и не писали", not FAKE_SS.get_worksheet_by_id(21).writes)

# ── ТЕСТ 5: полный диалог по-русски ───────────────────────────────
print("\n[5] Полный диалог (русский)")
SENT.clear()
B.user_lang[UID] = "ru"
B.cmd_new(msg("/new"))
check("шаг 1: список техники", "Шаг 1 из 6" in last())
btn_new = [b for b in buttons() if b[0] == "Toyota hilux 701bdc01"]
check("кнопка новой техники есть", bool(btn_new), buttons()[:3])
check("кнопка «Обновить список» есть", any("Обновить" in (b[0] or "") for b in buttons()))

B.cb_equipment(cb(btn_new[0][1]))
check("шаг 2: вид работ", "Шаг 2 из 6" in last() and "Toyota hilux 701bdc01" in last())
B.cb_work_type(cb("wt:ТО"))
check("шаг 3: описание", "Шаг 3 из 6" in last())
B.handle_description(msg("Замена тормозных колодок"))
check("подтверждение описания", "Всё верно?" in last())
B.cb_description_confirm(cb("desc:ok"))
check("шаг 4: моточасы", "Шаг 4 из 6" in last())
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

writes_before = len(FAKE_SS.get_worksheet_by_id(99).writes)
B.cb_final(cb("final:send"))
check("сохранено", "Запись добавлена" in last(2) or "Запись добавлена" in last(),
      [SENT[-2]["text"][:40], SENT[-1]["text"][:40]])
w = FAKE_SS.get_worksheet_by_id(99).writes[-1][1]
check("данные ушли в нужную вкладку", len(FAKE_SS.get_worksheet_by_id(99).writes) == writes_before + 1)
check("описание и ФИО на месте", w[4] == "Замена тормозных колодок" and w[15] == "Мубинов Ж.М.")
check("запчасти K→L", w[10] == "D1234" and w[11] == "D5678")
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
B.cb_equipment(cb([b for b in buttons() if b[0] == "Блендер"][0][1]))
check("2-қадам", "2-қадам / 6" in last())
B.cb_work_type(cb("wt:Р"))
B.handle_description(msg("Май ауыстыру"))
B.cb_description_confirm(cb("desc:ok"))
check("4-қадам моточасы", "Мотосағат" in last())
B.cb_moto_np(cb("moto:НП"))
check("5-қадам запчасти", "Қосалқы бөлшектер" in last())
B.cb_parts(cb("parts:np"))
check("6-қадам ФИО", "Орындаушының аты-жөні" in last())
B.handle_fio(msg("Мубинов Ж."))
check("итог на казахском", "ҚОРЫТЫНДЫ ЖАЗБА" in last())
B.cb_final(cb("final:send"))
blender_row = FAKE_SS.get_worksheet_by_id(21).writes[-1][1]
check("в ТАБЛИЦУ всё равно пишется по-русски",
      blender_row[1] == "Р" and blender_row[3] == "Подрядчик"
      and blender_row[8] == "1 день" and blender_row[13] == "Склад у подрядчика", blender_row)
check("моточасы НП", blender_row[5] == "НП")

# ── ТЕСТ 7: кнопки «Назад» ────────────────────────────────────────
print("\n[7] Кнопки «Назад»")
SENT.clear()
B.user_lang[UID] = "ru"
B.cmd_new(msg("/new"))
B.cb_equipment(cb([b for b in buttons() if b[0] == "Блендер"][0][1]))
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
check("назад с ФИО → шаг 5 (раньше кнопка не работала)", "Шаг 5 из 6" in last())
B.cb_parts(cb("parts:enter"))
B.cb_back(cb("back:parts"))
check("назад из ввода P/N → шаг 5", "Шаг 5 из 6" in last())
B.cb_back(cb("back:equipment"))
check("назад → шаг 1", "Шаг 1 из 6" in last())

# ── ТЕСТ 8: сессия устарела / вкладку удалили ─────────────────────
print("\n[8] Крайние случаи")
SENT.clear()
B.clear_state(UID)
B.cb_work_type(cb("wt:ТО", 1))
check("после рестарта бота — понятное сообщение", "Сессия устарела" in last())
B.cb_equipment(cb("eq:777777", 1))
check("нажали кнопку удалённой вкладки → список обновлён", "Шаг 1 из 6" in last())

# ── ТЕСТ 9: старые кнопки не создают мусорных строк ───────────────
print("\n[9] Старые кнопки / мусорные строки (баг из ревью)")
SENT.clear()
B.user_lang[UID] = "ru"
# 1) доводим запись по Блендеру до сводки
B.cmd_new(msg("/new"))
B.cb_equipment(cb([b for b in buttons() if b[0] == "Блендер"][0][1]))
B.cb_work_type(cb("wt:ТО"))
B.handle_description(msg("первая запись"))
B.cb_description_confirm(cb("desc:ok"))
B.cb_moto_np(cb("moto:НП"))
B.cb_parts(cb("parts:np"))
B.handle_fio(msg("Иванов И.И."))
OLD_SUMMARY_ID = SENT[-1]["id"]
check("сводка первой записи получена", "ИТОГОВАЯ ЗАПИСЬ" in last())

# 2) не отправляя, начинаем НОВУЮ запись по другой технике
B.cmd_new(msg("/new"))
B.cb_equipment(cb([b for b in buttons() if b[0] == "Toyota hilux 701bdc01"][0][1]))
check("начата запись по другой технике", "Toyota hilux 701bdc01" in last())

# 3) жмём «Исправить ФИО» на СТАРОЙ сводке
writes99 = len(FAKE_SS.get_worksheet_by_id(99).writes)
B.cb_back(cb("back:fio", OLD_SUMMARY_ID))
check("старая кнопка «Исправить ФИО» → понятное сообщение, без падения",
      "Сессия устарела" in last(), last()[:60])

# 4) жмём «Отправить» на СТАРОЙ сводке
B.cb_final(cb("final:send", OLD_SUMMARY_ID))
check("старая кнопка «Отправить» не пишет полупустую строку",
      len(FAKE_SS.get_worksheet_by_id(99).writes) == writes99,
      FAKE_SS.get_worksheet_by_id(99).writes[writes99:])
check("вместо записи — сообщение о сессии", "Сессия устарела" in last())

# ── ТЕСТ 10: двойное нажатие «Отправить» ─────────────────────────
print("\n[10] Двойное нажатие «Отправить»")
SENT.clear()
B.clear_state(UID)
B.cmd_new(msg("/new"))
B.cb_equipment(cb([b for b in buttons() if b[0] == "SHACMAN 650ADK"][0][1]))
B.cb_work_type(cb("wt:Р"))
B.handle_description(msg("сварка рамы"))
B.cb_description_confirm(cb("desc:ok"))
B.handle_moto(msg("120 000"))
B.cb_parts(cb("parts:np"))
B.handle_fio(msg("Петров П.П."))
SUM_ID = SENT[-1]["id"]
B.cb_final(cb("final:send", SUM_ID))
w51 = len(FAKE_SS.get_worksheet_by_id(51).writes)
B.cb_final(cb("final:send", SUM_ID))
B.cb_final(cb("final:send", SUM_ID))
check("повторные нажатия не создают дублей",
      len(FAKE_SS.get_worksheet_by_id(51).writes) == w51, w51)

# ── ТЕСТ 11: строка с пустой колонкой A не затирается ────────────
print("\n[11] Строка с пустой колонкой A")
gap = FAKE_SS.get_worksheet_by_id(41)          # Mercedes 032YO
gap.cells[10] = [""] * 4 + ["дописано вручную"] + [""] * 12   # A пустая, E заполнена
ok, det = B.write_to_google_sheets({
    "sheet_id": 41, "equipment": "Mercedes Actros 032YO", "work_type": "ТО",
    "description": "новая запись", "moto": "10", "fio": "Тест",
})
check("запись пошла в строку 11, а не поверх строки 10",
      gap.writes[-1][0] == "A11:Q11", gap.writes[-1][0])
check("ручные данные в строке 10 целы", gap.cells[10][4] == "дописано вручную")

# ── ТЕСТ 12: ошибка записи → повтор без потери данных ────────────
print("\n[12] Ошибка записи и повтор")
SENT.clear()
B.clear_state(UID)
B.cmd_new(msg("/new"))
B.cb_equipment(cb([b for b in buttons() if b[0] == "Трал 59ADV06"][0][1]))
B.cb_work_type(cb("wt:ТО"))
B.handle_description(msg("замена колодок"))
B.cb_description_confirm(cb("desc:ok"))
B.cb_moto_np(cb("moto:НП"))
B.cb_parts(cb("parts:np"))
B.handle_fio(msg("Сидоров С.С."))
SUM_ID = SENT[-1]["id"]
_real_write = B.write_to_google_sheets
B.write_to_google_sheets = lambda d: (False, "Ошибка Google API: 503")
B.cb_final(cb("final:send", SUM_ID))
check("показана ошибка", "Ошибка при сохранении" in last())
check("данные сохранены в состоянии", B.get_state(UID).get("fio") == "Сидоров С.С.")
B.write_to_google_sheets = _real_write
B.cb_final(cb("final:retry", SUM_ID))
check("повтор показывает сводку с данными",
      "ИТОГОВАЯ ЗАПИСЬ" in last() and "Сидоров С.С." in last())
B.cb_final(cb("final:send", SENT[-1]["id"]))
check("после повтора запись сохранена", "Запись добавлена" in last(2) or "Запись добавлена" in last())
check("данные ушли в Трал 59ADV06",
      FAKE_SS.get_worksheet_by_id(61).writes[-1][1][4] == "замена колодок")

# ── ТЕСТ 13: «Задать ещё вопрос» не съедает текст записи ─────────
print("\n[13] Q&A не перехватывает текст записи")
SENT.clear()
B.clear_state(UID)
B.cmd_new(msg("/new"))
B.cb_equipment(cb([b for b in buttons() if b[0] == "Блендер"][0][1]))
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
B.cb_equipment(cb([b for b in buttons() if b[0] == "Toyota Hilux 886AHL06"][0][1]))
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
w81 = len(FAKE_SS.get_worksheet_by_id(81).writes)
B.cb_final(cb("final:send", SUM_ID))
check("повторный тап не пишет дубль", len(FAKE_SS.get_worksheet_by_id(81).writes) == w81)
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
B.cb_equipment(cb([b for b in buttons() if b[0] == "Toyota Hilux 779AHF06"][0][1]))
B.cb_work_type(cb("wt:Р"))
B.handle_description(msg("замена сальника"))
B.cb_description_confirm(cb("desc:ok"))
B.cb_moto_np(cb("moto:НП"))
check("бот на шаге запчастей", B.get_state(UID)["step"] == "parts")
B.cb_back(cb("back:fio"))
check("прыжок на ФИО в обход запчастей заблокирован",
      B.get_state(UID) == {} or B.get_state(UID).get("step") != "fio",
      B.get_state(UID))

print("\n[16] Экранирование Markdown")
check("подчёркивание и звёздочка экранируются",
      B.md("Насос_2 *новый*") == r"Насос\_2 \*новый\*", B.md("Насос_2 *новый*"))

SENT.clear()
B.qa_mode_users.add(UID)
B.handle_qa_question(msg("Когда чинили насос 883?"))
check("режим вопросов отвечает", "AI отвечает" in last())
check("после ответа режим сброшен", UID not in B.qa_mode_users)

# ── Итог ──────────────────────────────────────────────────────────
print("\n" + "═" * 60)
if FAILS:
    print(f"❌ ПРОВАЛЕНО: {len(FAILS)}")
    for f in FAILS:
        print("   •", f)
    sys.exit(1)
print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
