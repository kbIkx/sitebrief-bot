"""Transport-independent state machine, validation, resumable brief and review."""
import json
import re
import secrets
import time
import uuid
from urllib.parse import urlsplit
from .scenario import ROUTES, SERVICE_NAMES, STARTS
from .store import dumps


class FlowError(ValueError):
    pass


def valid_text(text, validator="text", attachment=None):
    text = (text or "").strip()
    if validator == "file":
        if not attachment:
            raise FlowError("Прикрепите файл, либо вернитесь назад и выберите другой вариант.")
        return attachment["name"]
    if not 1 <= len(text) <= 1500 or any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise FlowError("Введите от 1 до 1500 символов.")
    if validator == "name" and (not 2 <= len(text) <= 80 or not any(c.isalpha() for c in text)):
        raise FlowError("Укажите имя: от 2 до 80 символов.")
    if validator == "phone":
        if not re.fullmatch(r"[+\d\s().-]+", text):
            raise FlowError("Нужен номер телефона, например +7 999 123-45-67.")
        digits = re.sub(r"\D", "", text)
        if not 10 <= len(digits) <= 15:
            raise FlowError("Номер должен содержать от 10 до 15 цифр, включая код страны.")
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        return "+" + digits
    if validator == "url":
        urls = re.findall(r"https?://[^\s<>]+", text)
        if not urls or any(not urlsplit(u).hostname or urlsplit(u).username for u in urls):
            raise FlowError("Пришлите ссылку с https:// или http://. Комментарий можно добавить после ссылки.")
    return text


class Engine:
    def __init__(self, store):
        self.store = store

    def fresh(self, sid, config_version):
        return {"id": sid, "screen": "privacy", "node": "", "rev": 0, "version": config_version,
                "answers": {}, "history": [], "selected": [], "details": {}, "draft": uuid.uuid4().hex,
                "service": None, "consent_at": None, "section_values": {}, "lead_id": None}

    def get(self, sid):
        with self.store.transaction() as db:
            row = db.execute("SELECT body FROM sessions WHERE id=?", (sid,)).fetchone()
            s = json.loads(row[0]) if row else self.fresh(sid, self.store.config()[0])
            db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)", (sid, dumps(s), time.time()))
            return self.view(s)

    def action(self, sid, action="", text="", revision=None, attachment=None, event_id=None):
        with self.store.transaction() as db:
            row = db.execute("SELECT body FROM sessions WHERE id=?", (sid,)).fetchone()
            s = json.loads(row[0]) if row else self.fresh(sid, self.store.config()[0])
            if event_id and db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
                return self.view(s)
            if revision is not None and revision != s["rev"]:
                raise FlowError("Это сообщение устарело. Продолжите с текущего вопроса.")
            config = self.store.config(s["version"])[1]
            self._act(s, action, text, attachment, config, db)
            s["rev"] += 1
            db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)", (sid, dumps(s), time.time()))
            if event_id:
                db.execute("INSERT INTO events VALUES(?,?)", (event_id, time.time()))
            return self.view(s)

    def _goto(self, s, node, push=True):
        if node.startswith("@"):
            node = ROUTES[node](s["answers"])
        if s.get("return_review"):
            s.pop("return_review")
            s["screen"] = "review"
            s["node"] = "review"
            return
        if push and s["node"]:
            s["history"].append(s["node"])
        s["node"] = node
        s["screen"] = "review" if node == "review" else "question"
        old = s["answers"].get(node, {})
        s["selected"] = list(old.get("values", []))
        s["details"] = dict(old.get("details", {}))
        s.pop("category", None)

    def _answer(self, s, node, values, details, config):
        opts = {o["id"]: o for o in node.get("options", [])}
        s["answers"][node["id"]] = {"title": node["title"], "values": values,
            "labels": [opts[v]["label"] if v in opts else v for v in values], "details": details.copy()}

    def _finish_choice(self, s, node, config):
        if not s["selected"] and not node.get("optional"):
            raise FlowError("Выберите хотя бы один вариант.")
        queue = [o for o in node["options"] if o["id"] in s["selected"] and o.get("detail") and o["id"] not in s["details"]]
        if queue:
            s["screen"] = "detail"
            s["detail_queue"] = queue
        else:
            self._answer(s, node, s["selected"], s["details"], config)
            chosen = next((o for o in node["options"] if o["id"] in s["selected"]), {})
            self._goto(s, chosen.get("next", node["next"]))

    def _act(self, s, action, text, attachment, c, db):
        screen = s["screen"]
        if action in {"start", "resume"}:
            if screen != "privacy":
                return
            s["consent_at"] = time.time()
            s["screen"] = "menu"
            return
        if action == "privacy":
            s["previous_screen"] = screen
            s["screen"] = "policy"
            return
        if action == "delete":
            s["previous_screen"] = screen
            s["screen"] = "delete_confirm"
            return
        if screen == "delete_confirm" and action == "confirm_delete":
            db.execute("DELETE FROM leads WHERE session_id=?", (s["id"],))
            paths = [r[0] for r in db.execute("SELECT path FROM files WHERE owner=?", (s["id"],))]
            for path in paths:
                (self.store.directory / "uploads" / path).unlink(missing_ok=True)
            db.execute("DELETE FROM files WHERE owner=?", (s["id"],))
            sid = s["id"]
            rev = s["rev"]
            s.clear()
            s.update(self.fresh(sid, self.store.config()[0]))
            s["rev"] = rev
            return
        if action == "menu":
            if s["consent_at"]:
                s["screen"] = "menu"
                s.pop("return_review", None)
            return
        if action.startswith("service:"):
            if not s["consent_at"]:
                raise FlowError("Сначала прочитайте условия и нажмите «Начать».")
            service = action.split(":", 1)[1]
            if service not in STARTS:
                raise FlowError("Неизвестная услуга")
            if s["answers"] and not s["lead_id"]:
                s["pending_service"] = service
                s["previous_screen"] = screen
                s["screen"] = "restart_confirm"
            else:
                self._new_draft(s, service)
            return
        if screen == "restart_confirm" and action == "confirm_restart":
            self._new_draft(s, s["pending_service"])
            return
        if action == "continue_draft" and s["service"] and not s["lead_id"]:
            s["screen"] = "review" if s["node"] == "review" else "question"
            return
        if action == "portfolio" and s["consent_at"]:
            s["screen"] = "portfolio"
            return
        if screen == "portfolio" and action.startswith("portfolio:"):
            cid = action.split(":", 1)[1]
            if cid == "other":
                s["screen"] = "portfolio_other"
            elif cid in {p["id"] for p in c["portfolio"]}:
                s["portfolio_id"] = cid
                s["screen"] = "portfolio_item"
            return
        if screen == "portfolio_other" and text:
            s["portfolio_request"] = valid_text(text)
            s["screen"] = "portfolio_item"
            s["portfolio_id"] = "other"
            return
        if action == "contact" and s["consent_at"]:
            s["screen"] = "contact"
            return
        if action == "back":
            if screen in {"policy", "delete_confirm", "restart_confirm"}:
                s["screen"] = s.pop("previous_screen", "menu")
            elif screen in {"portfolio_item", "portfolio_other"}:
                s["screen"] = "portfolio"
            elif screen in {"contact", "portfolio"}:
                s["screen"] = "menu"
            elif s.get("return_review"):
                s.pop("return_review")
                s["screen"] = "review"
                s["node"] = "review"
            elif screen == "detail":
                s["screen"] = "question"
            elif s.get("category"):
                s.pop("category", None)
            elif s["history"]:
                old = s["node"]
                self._goto(s, s["history"].pop(), push=False)
                # Discard abandoned downstream answers: they must not leak into the brief.
                allowed = set(s["history"] + [s["node"]])
                s["answers"] = {k: v for k, v in s["answers"].items() if k in allowed}
                if old == "sections":
                    s["section_values"] = {}
            else:
                s["screen"] = "menu"
            return
        if screen == "review":
            if action.startswith("edit:"):
                key = action.split(":", 1)[1]
                if key not in s["answers"] or key in {"kind", "promote_current"}:
                    raise FlowError("Для изменения маршрута начните новую анкету из меню.")
                self._goto(s, key, push=False)
                s["return_review"] = True
                return
            if action == "submit":
                if s["lead_id"]:
                    s["screen"] = "success"
                    return
                lead_id = "SB-" + secrets.token_hex(4).upper()
                body = {"id": lead_id, "service": s["service"], "service_name": SERVICE_NAMES[s["service"]],
                        "answers": s["answers"], "version": s["version"], "consent_at": s["consent_at"],
                        "source": "telegram" if s["id"].startswith("tg:") else "web",
                        "name": s["answers"]["name"]["values"][0], "phone": s["answers"]["phone"]["values"][0]}
                db.execute("INSERT INTO leads(id,session_id,draft,body,created) VALUES(?,?,?,?,?)",
                           (lead_id, s["id"], s["draft"], dumps(body), time.time()))
                db.execute("INSERT INTO outbox(id,lead_id) VALUES(?,?)", (uuid.uuid4().hex, lead_id))
                s["lead_id"] = lead_id
                s["screen"] = "success"
                return
        if screen not in {"question", "detail"}:
            raise FlowError("Используйте кнопки текущего меню.")
        node = c["nodes"][s["node"]]
        if screen == "detail":
            item = s["detail_queue"][0]
            value = valid_text(text, item.get("validator", "text"), attachment)
            s["details"][item["id"]] = {"text": value, **({"file": attachment} if attachment else {})}
            s["detail_queue"].pop(0)
            if not s["detail_queue"]:
                if item.get("section"):
                    cat = item["section"]
                    s["section_values"].setdefault(cat, {})["other"] = value
                    s["screen"] = "question"
                else:
                    self._finish_choice(s, node, c)
            return
        if node["kind"] == "sections":
            self._sections(s, action, node, c)
            return
        if node["kind"] == "text":
            if action.startswith("opt:"):
                key = action[4:]
                if key not in {o["id"] for o in node["options"]}:
                    raise FlowError("Неизвестный вариант")
                self._answer(s, node, [key], {}, c)
            elif action == "skip" and node.get("optional"):
                self._answer(s, node, ["Не указан"], {}, c)
            else:
                value = valid_text(text, node.get("validator", "text"), attachment)
                self._answer(s, node, [value], {}, c)
            self._goto(s, node["next"])
            return
        if action.startswith("opt:"):
            key = action[4:]
            opt = next((o for o in node["options"] if o["id"] == key), None)
            if not opt:
                raise FlowError("Неизвестный вариант")
            if node["kind"] == "multi":
                if key in s["selected"]:
                    s["selected"].remove(key)
                    s["details"].pop(key, None)
                else:
                    if opt.get("exclusive"):
                        s["selected"] = []
                        s["details"] = {}
                    else:
                        excluded = {o["id"] for o in node["options"] if o.get("exclusive")}
                        s["selected"] = [v for v in s["selected"] if v not in excluded]
                    s["selected"].append(key)
            else:
                s["selected"] = [key]
                s["details"] = {}
                self._finish_choice(s, node, c)
            return
        if action == "done" and node["kind"] == "multi":
            self._finish_choice(s, node, c)
            return
        raise FlowError("Выберите вариант кнопкой.")

    def _new_draft(self, s, service):
        accepted, sid, rev = s["consent_at"], s["id"], s["rev"]
        s.clear()
        s.update(self.fresh(sid, self.store.config()[0]))
        s.update(consent_at=accepted, rev=rev, service=service)
        self._goto(s, STARTS[service], push=False)

    def _sections(self, s, action, node, c):
        if action.startswith("cat:"):
            cat = action[4:]
            if cat not in c["sections"]:
                raise FlowError("Неизвестная категория")
            s["category"] = cat
            return
        cat = s.get("category")
        if action == "section_other":
            s["screen"] = "detail"
            s["detail_queue"] = [{"id": "other", "section": "extra", "detail": "Напишите, какие страницы ещё нужны"}]
            return
        if cat and action.startswith("section:"):
            key = action[8:]
            opts = {o["id"]: o for o in c["sections"][cat]["options"]}
            if key not in opts:
                raise FlowError("Неизвестная страница")
            selected = s["section_values"].setdefault(cat, {})
            if key in selected:
                del selected[key]
            elif key == "other":
                s["screen"] = "detail"
                s["detail_queue"] = [{"id": "other", "section": cat, "detail": "Какие страницы добавить в эту категорию?"}]
            else:
                selected[key] = opts[key]["label"]
            return
        if action == "done":
            if cat:
                s.pop("category", None)
            else:
                values = [v for selected in s["section_values"].values() for v in selected.values()]
                self._answer(s, node, values or ["Нужна рекомендация по разделам"], {}, c)
                self._goto(s, node["next"])
            return
        raise FlowError("Выберите категорию или нажмите «Готово».")

    def view(self, s):
        c = self.store.config(s["version"])[1]
        screen = s["screen"]
        v = {"screen": screen, "revision": s["rev"], "brand": c["brand"], "tagline": c["tagline"],
             "options": [], "input": None, "service": SERVICE_NAMES.get(s["service"], "Ваш следующий проект"),
             "step": len(s["history"]) + 1, "summary": [], "links": [], "attachments": []}
        v["draft_summary"] = self.summary(s)
        v["service_id"] = s["service"]
        v["accepted"] = bool(s["consent_at"])
        def btn(action, label, **kw):
            v["options"].append({"action": action, "label": label, **kw})
        if screen == "privacy":
            v.update(title="Давайте познакомимся", text=c["privacy_intro"], hint="Демо: используйте вымышленные контактные данные.")
            btn("start", "Начать", primary=True)
            btn("privacy", "Политика конфиденциальности")
        elif screen == "policy":
            v.update(title="Конфиденциальность", text=c["privacy_text"])
            btn("back", "Назад")
        elif screen == "menu":
            v.update(title=c["greeting"], text="Расскажите о задаче — соберём понятный бриф для специалиста.")
            for key, label in c["menu_labels"].items():
                btn("service:" + key if key in STARTS else key, label)
            if s["service"] and not s["lead_id"]:
                btn("continue_draft", "Продолжить сохранённую анкету", primary=True)
            btn("delete", "Удалить мои данные", quiet=True)
        elif screen in {"delete_confirm", "restart_confirm"}:
            deleting = screen == "delete_confirm"
            v.update(title="Удалить данные?" if deleting else "Начать новую анкету?",
                     text="Черновик, заявки и файлы будут удалены из локальной базы. Копии уже переданных уведомлений в Telegram удаляются отдельно владельцем." if deleting else "Текущий незавершённый бриф будет заменён. Можно вернуться и продолжить его.")
            btn("confirm_delete" if deleting else "confirm_restart", "Да, удалить" if deleting else "Начать заново")
            btn("back", "Отмена", primary=True)
        elif screen in {"portfolio", "portfolio_item", "portfolio_other"}:
            v.update(title="Портфолио", text="В какой сфере хотите посмотреть примеры сайтов?")
            if screen == "portfolio":
                for cat in c["portfolio"]:
                    btn("portfolio:" + cat["id"], cat["title"])
                btn("portfolio:other", "Иная сфера — уточню")
            elif screen == "portfolio_other":
                v.update(text="Опишите, какие примеры вас интересуют.", input="text")
            else:
                cat = next((p for p in c["portfolio"] if p["id"] == s.get("portfolio_id")), None)
                v["text"] = c["portfolio_intro"]
                if cat:
                    v["title"] = cat["title"]
                    v["links"] = [item for item in cat["items"] if item.get("url")]
                    if not v["links"]:
                        v["text"] += "\n\nПримеры этой категории пока не добавлены. Заказчик сможет загрузить ссылки в панели управления. Можно оставить запрос специалисту."
                else:
                    v["text"] = "Ваш интерес: " + s.get("portfolio_request", "") + ". Подходящие работы можно запросить у специалиста."
                for service, name in SERVICE_NAMES.items():
                    btn("service:" + service, name)
            btn("back", "Назад")
        elif screen == "contact":
            v.update(title="Будем на связи", text=c["contact_text"])
            if c["contact_url"]:
                v["links"].append({"title": "Написать специалисту", "url": c["contact_url"]})
            if c["contact_phone"]:
                v["text"] += "\nТелефон: " + c["contact_phone"]
            if c["contact_email"]:
                v["text"] += "\nEmail: " + c["contact_email"]
            for key, name in SERVICE_NAMES.items():
                btn("service:" + key, name)
            btn("back", "В главное меню")
        elif screen == "success":
            name = s["answers"].get("name", {}).get("values", [""])[0]
            v.update(title="Всё готово", text=c["thanks"].replace("{name}", name), lead_id=s["lead_id"])
            btn("menu", "Вернуться в главное меню", primary=True)
            btn("portfolio", "Примеры наших работ")
            btn("contact", "Связаться напрямую")
        elif screen == "review":
            v.update(title="Ваш проект в одном брифе", text="Проверьте ответы. До отправки можно исправить любой пункт, кроме выбора ветки.")
            v["summary"] = self.summary(s)
            btn("submit", "Отправить бриф специалисту", primary=True)
            btn("back", "Назад")
        else:
            node = c["nodes"][s["node"]]
            v.update(title=node["title"], hint=node.get("hint", ""), node=s["node"], multi=node["kind"] == "multi")
            if screen == "detail":
                item = s["detail_queue"][0]
                v.update(title=item["detail"], input=item.get("validator", "text"), multi=False)
            elif node["kind"] == "sections":
                cat = s.get("category")
                if cat:
                    v["title"] = c["sections"][cat]["title"]
                    for opt in c["sections"][cat]["options"]:
                        if s["answers"].get("kind", {}).get("values") == ["services"] and cat == "catalog" and opt["label"] == "Товары (каталог)":
                            continue
                        btn("section:" + opt["id"], opt["label"], selected=opt["id"] in s["section_values"].get(cat, {}))
                    v["multi"] = True
                    btn("done", "Сохранить категорию", primary=True)
                else:
                    for key, group in c["sections"].items():
                        count = len(s["section_values"].get(key, {}))
                        btn("cat:" + key, group["title"] + (f" · {count}" if count else ""))
                    btn("section_other", "Добавить другие страницы")
                    btn("done", "Готово, продолжить", primary=True)
            else:
                for opt in node["options"]:
                    btn("opt:" + opt["id"], opt["label"], selected=opt["id"] in s["selected"])
                if node["kind"] == "multi":
                    v["hint"] = (v.get("hint", "") + "\nМожно выбрать несколько вариантов. Затем нажмите «Продолжить».").strip()
                    btn("done", "Продолжить", primary=True)
                if node["kind"] == "text":
                    v["input"] = node.get("validator", "text")
                    if node.get("optional"):
                        btn("skip", "Пропустить")
            btn("back", "Назад", quiet=True)
            btn("menu", "Главное меню", quiet=True)
        return v

    def summary(self, s):
        return [{"key": key, "title": answer["title"], "value": answer_text(answer),
                 "editable": key not in {"kind", "promote_current"}}
                for key, answer in s["answers"].items()]


def answer_text(answer):
    labels = answer.get("labels", [])
    text = ", ".join(labels) if labels else "Не требуется"
    for value in answer.get("details", {}).values():
        text += "\n" + (value.get("text", "") if isinstance(value, dict) else str(value))
    return text


def lead_text(lead):
    parts = [f"Бриф {lead['id']}", lead["service_name"]]
    parts.extend(f"\n{a['title']}\n{answer_text(a)}" for a in lead["answers"].values())
    return "\n".join(parts)
