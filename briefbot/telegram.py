"""Telegram Bot API adapter. No tokens or raw API errors are written to logs."""
import json
import threading
import time
import urllib.error
import urllib.request
from .engine import FlowError, lead_text
from .web import ALLOWED_FILES, MAX_FILE
from pathlib import Path


class TelegramError(Exception):
    def __init__(self, code, retry_after=None, ambiguous=False):
        self.code, self.retry_after, self.ambiguous = code, retry_after, ambiguous
        super().__init__(f"telegram_error_{code}")


class Telegram:
    def __init__(self, token, store, engine, manager_chat="", application=None):
        self._token, self.store, self.engine = token, store, engine
        self.manager_chat, self.application = manager_chat, application
        self.stop = threading.Event()

    def call(self, method, payload=None, timeout=40):
        request = urllib.request.Request("https://api.telegram.org/bot" + self._token + "/" + method,
            data=json.dumps(payload or {}).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                error = json.load(exc)
                raise TelegramError(exc.code, error.get("parameters", {}).get("retry_after")) from None
            except (json.JSONDecodeError, ValueError):
                raise TelegramError(exc.code) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            raise TelegramError("network", ambiguous=True) from None
        if not result.get("ok"):
            raise TelegramError(result.get("error_code", "invalid_response"))
        return result["result"]

    def download(self, file_id):
        result = self.call("getFile", {"file_id": file_id})
        path = result.get("file_path", "")
        if not path or ".." in path or ":" in path or path.startswith("/"):
            raise ValueError("Файл недоступен")
        try:
            with urllib.request.urlopen("https://api.telegram.org/file/bot" + self._token + "/" + path, timeout=30) as response:
                data = response.read(MAX_FILE + 1)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError("Не удалось получить файл из Telegram. Попробуйте позже.") from None
        if len(data) > MAX_FILE:
            raise ValueError("Файл превышает 10 МБ")
        return data

    def send_view(self, chat_id, view):
        text = view["title"]
        if view.get("text"):
            text += "\n\n" + view["text"]
        if view.get("hint"):
            text += "\n\n" + view["hint"]
        if view.get("summary"):
            text += "\n\n" + "\n\n".join(x["title"] + "\n" + x["value"] for x in view["summary"])
        if view.get("lead_id"):
            text += "\n\nНомер заявки: " + view["lead_id"]
        rows = []
        for opt in view["options"]:
            rows.append([{"text": ("✓ " if opt.get("selected") else "") + opt["label"],
                          "callback_data": str(view["revision"]) + "|" + opt["action"]}])
        for link in view.get("links", []):
            rows.append([{"text": link["title"], "url": link["url"]}])
        # Telegram edits remain native, with the same engine as the browser demo.
        if view["screen"] == "review":
            for item in view["summary"]:
                if item["editable"]:
                    rows.append([{"text": "Изменить: " + item["title"][:45],
                                  "callback_data": str(view["revision"]) + "|edit:" + item["key"]}])
        chunks = split_text(text)
        for i, chunk in enumerate(chunks):
            self.call("sendMessage", {"chat_id": chat_id, "text": chunk,
                **({"reply_markup": {"inline_keyboard": rows}} if i == len(chunks) - 1 else {})})

    def handle(self, update):
        callback = update.get("callback_query")
        msg = callback.get("message", {}) if callback else update.get("message", {})
        chat = msg.get("chat", {})
        if chat.get("type") != "private":
            return
        chat_id = chat["id"]
        sid = "tg:" + str(chat_id)
        text, action, revision, attachment = msg.get("text", ""), "", None, None
        if callback:
            try:
                raw_revision, action = callback.get("data", "").split("|", 1)
                revision = int(raw_revision)
            except (ValueError, TypeError):
                return
            text = ""
            try:
                self.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
            except TelegramError:
                pass
        elif text.startswith("/"):
            command = text.split()[0].split("@")[0]
            if command in {"/start", "/resume"}:
                self.send_view(chat_id, self.engine.get(sid))
                return
            action = {"/menu": "menu", "/privacy": "privacy", "/delete_me": "delete", "/cancel": "back"}.get(command, "")
            if command == "/id":
                self.call("sendMessage", {"chat_id": chat_id, "text": f"Ваш chat ID: {chat_id}"})
                return
            if not action:
                self.call("sendMessage", {"chat_id": chat_id, "text": "Команды: /start, /menu, /resume, /privacy, /delete_me"})
                return
            text = ""
        elif msg.get("document") or msg.get("photo"):
            doc = msg.get("document") or {**msg["photo"][-1], "file_name": "image-" + str(msg.get("message_id", 0)) + ".jpg"}
            name = doc.get("file_name", "document")
            if Path(name).suffix.lower() not in ALLOWED_FILES or not 0 < doc.get("file_size", 0) <= MAX_FILE:
                self.call("sendMessage", {"chat_id": chat_id, "text": "Пришлите PDF, DOCX, XLSX, TXT, PNG или JPG до 10 МБ."})
                return
            if self.engine.get(sid).get("input") != "file":
                self.call("sendMessage", {"chat_id": chat_id, "text": "Загрузка файла доступна на шаге «Есть подробное ТЗ»."})
                return
            attachment = {"name": name[:160], "size": doc["file_size"], "file_id": doc["file_id"], "source": "telegram"}
        try:
            view = self.engine.action(sid, action, text, revision, attachment, "tg:" + str(update["update_id"]))
            self.send_view(chat_id, view)
        except FlowError as exc:
            self.call("sendMessage", {"chat_id": chat_id, "text": str(exc)})
            self.send_view(chat_id, self.engine.get(sid))

    def run(self):
        # Temporary startup outages do not leave the bot stopped forever.
        while not self.stop.is_set():
            try:
                self.call("getMe")
                webhook = self.call("getWebhookInfo")
                if webhook.get("url"):
                    if self.application:
                        self.application.telegram_status = "webhook_conflict"
                    return  # Do not remove an existing webhook without an explicit decision.
                self.call("setMyCommands", {"commands": [
                {"command": "start", "description": "Начать или продолжить бриф"},
                {"command": "menu", "description": "Главное меню"},
                {"command": "resume", "description": "Продолжить сохранённую анкету"},
                {"command": "privacy", "description": "Конфиденциальность"},
                    {"command": "delete_me", "description": "Удалить мои данные"}]})
                break
            except TelegramError as exc:
                if self.application:
                    self.application.telegram_status = "connection_error"
                with self.store.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO settings VALUES('telegram_error',?)", (str(exc.code),))
                if exc.code in {401, 404}:
                    return
                self.stop.wait(min(exc.retry_after or 5, 60))
        if self.stop.is_set():
            return
        if self.application:
            self.application.telegram_status = "connected"
        with self.store.lock:
            row = self.store.db.execute("SELECT value FROM settings WHERE key='telegram_offset'").fetchone()
        offset = int(row[0]) if row else 0
        while not self.stop.is_set():
            try:
                updates = self.call("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message", "callback_query"]})
                for update in updates:
                    try:
                        self.handle(update)
                    except (TelegramError, ValueError, KeyError):
                        # State is durable. /resume retrieves it if the response was lost.
                        pass
                    offset = update["update_id"] + 1
                    with self.store.transaction() as db:
                        db.execute("INSERT OR REPLACE INTO settings VALUES('telegram_offset',?)", (str(offset),))
                if self.application:
                    self.application.telegram_status = "connected"
                with self.store.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO settings VALUES('telegram_last_poll',?)", (str(time.time()),))
                    db.execute("DELETE FROM settings WHERE key='telegram_error'")
            except TelegramError as exc:
                if self.application:
                    self.application.telegram_status = "connection_error"
                with self.store.transaction() as db:
                    db.execute("INSERT OR REPLACE INTO settings VALUES('telegram_error',?)", (str(exc.code),))
                if exc.code in {401, 409}:
                    return
                self.stop.wait(min(exc.retry_after or 5, 60))

    def notifications(self):
        while not self.stop.wait(2):
            self.notify_once()

    def notify_once(self):
        if not self.manager_chat:
            return False
        with self.store.transaction() as db:
            row = db.execute("SELECT o.*,l.body FROM outbox o JOIN leads l ON l.id=o.lead_id WHERE o.status='pending' AND o.due<=? ORDER BY l.created LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return False
            db.execute("UPDATE outbox SET status='sending',attempts=attempts+1 WHERE id=?", (row["id"],))
        try:
            lead = json.loads(row["body"])
            # One compact notification. The complete brief stays in the panel.
            text = lead_text(lead)
            if len(text) > 3800:
                text = text[:3650] + "\n\nПолный бриф и файлы доступны в панели управления."
            self.call("sendMessage", {"chat_id": self.manager_chat, "text": text})
        except TelegramError as exc:
            status = "review" if exc.ambiguous else ("failed" if row["attempts"] >= 4 or exc.code in {400, 401, 403} else "pending")
            with self.store.transaction() as db:
                db.execute("UPDATE outbox SET status=?,due=?,error=? WHERE id=?", (status, time.time() + (exc.retry_after or 2 ** (row["attempts"] + 2)), str(exc), row["id"]))
        except Exception:
            with self.store.transaction() as db:
                db.execute("UPDATE outbox SET status='review',error='local_error' WHERE id=?", (row["id"],))
        else:
            with self.store.transaction() as db:
                db.execute("UPDATE outbox SET status='sent',error=NULL WHERE id=?", (row["id"],))
        return True


def split_text(text, limit=3800):
    chunks = []
    while len(text) > limit:
        split = text.rfind("\n", 0, limit)
        if split < limit // 2:
            split = limit
        chunks.append(text[:split])
        text = text[split:].lstrip("\n")
    return chunks + [text]
