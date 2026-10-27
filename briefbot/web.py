"""Local review/demo and authenticated customer administration, stdlib only."""
import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import secrets
import socket
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from .engine import FlowError, lead_text
from .scenario import validate_config

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE = 10 * 1024 * 1024
ALLOWED_FILES = {".pdf", ".docx", ".xlsx", ".txt", ".png", ".jpg", ".jpeg"}


class Application:
    def __init__(self, store, engine, password, secure=False):
        self.store, self.engine, self.secure = store, engine, secure
        self.salt = secrets.token_bytes(16)
        self.password_hash = hashlib.scrypt(password.encode(), salt=self.salt, n=16384, r=8, p=1)
        self.admin_sessions = {}
        self.login_attempts = defaultdict(deque)
        self.requests = defaultdict(deque)
        self.telegram_status = "not_configured"
        self.telegram = None

    def check_password(self, password):
        if not isinstance(password, str) or len(password) > 200:
            return False
        actual = hashlib.scrypt(password.encode(), salt=self.salt, n=16384, r=8, p=1)
        return hmac.compare_digest(actual, self.password_hash)

    def handler_class(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "SiteBrief"

            def log_message(self, *_):
                pass  # Never log tokens, phone numbers, file names or request bodies.

            def response(self, data, status=200, mime="application/json; charset=utf-8", cookie=None, filename=None):
                if isinstance(data, (dict, list)):
                    data = json.dumps(data, ensure_ascii=False).encode()
                elif isinstance(data, str):
                    data = data.encode()
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: https:; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                if filename:
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.end_headers()
                self.wfile.write(data)

            def cookies(self):
                try:
                    c = SimpleCookie(self.headers.get("Cookie", ""))
                    return {k: v.value for k, v in c.items()}
                except Exception:
                    return {}

            def cookie(self, key, value, age=86400 * 30):
                return f"{key}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}" + ("; Secure" if app.secure else "")

            def sid(self):
                value = self.cookies().get("sb_session", "")
                if not value or len(value) != 48 or any(c not in "0123456789abcdef" for c in value):
                    value = secrets.token_hex(24)
                return "web:" + value, self.cookie("sb_session", value)

            def admin(self, csrf=False):
                key = self.cookies().get("sb_admin", "")
                record = app.admin_sessions.get(key)
                if not record or record["expires"] < time.time():
                    raise PermissionError("Нужно войти в панель управления")
                if csrf and not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), record["csrf"]):
                    raise PermissionError("Обновите страницу панели управления")
                return record

            def rate_limit(self):
                ip = self.client_address[0]
                now = time.monotonic()
                with app.store.lock:
                    q = app.requests[ip]
                    while q and now - q[0] > 60:
                        q.popleft()
                    if len(q) >= 180:
                        raise FlowError("Слишком много запросов. Подождите минуту.")
                    q.append(now)

            def do_GET(self):
                try:
                    self.route_get()
                except PermissionError as exc:
                    self.response({"error": str(exc)}, 401)
                except (ValueError, KeyError) as exc:
                    self.response({"error": str(exc)}, 400)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    self.response({"error": "Внутренняя ошибка. Повторите запрос позже."}, 500)

            def route_get(self):
                path = urlsplit(self.path).path
                if path in {"/", "/admin", "/privacy"}:
                    self.response((ROOT / "static" / ("admin.html" if path == "/admin" else "index.html")).read_bytes(), mime="text/html; charset=utf-8")
                elif path.startswith("/static/"):
                    name = path.removeprefix("/static/")
                    if "/" in name or name.startswith("."):
                        return self.response({"error": "Не найдено"}, 404)
                    f = ROOT / "static" / name
                    if not f.is_file():
                        return self.response({"error": "Не найдено"}, 404)
                    self.response(f.read_bytes(), mime=mimetypes.guess_type(name)[0] or "application/octet-stream")
                elif path == "/api/state":
                    sid, cookie = self.sid()
                    self.response(app.engine.get(sid), cookie=cookie)
                elif path == "/api/health":
                    self.response({"ok": True, "version": "1.0.0"})
                elif path == "/api/admin/session":
                    self.response({"csrf": self.admin()["csrf"]})
                elif path == "/api/admin/config":
                    self.admin()
                    version, config = app.store.config()
                    self.response({"version": version, "config": config})
                elif path == "/api/admin/leads":
                    self.admin()
                    leads = app.store.leads()
                    self.response({"leads": leads, "telegram": app.telegram_status,
                                   "counts": {s: sum(l["status"] == s for l in leads) for s in ("new", "working", "done")}})
                elif path == "/api/admin/export.csv":
                    self.admin()
                    output = io.StringIO(newline="")
                    writer = csv.writer(output)
                    writer.writerow(["id", "service", "name", "phone", "status", "brief"])
                    for l in app.store.leads():
                        writer.writerow([safe_csv(v) for v in [l["id"], l["service_name"], l["name"], l["phone"], l["status"], lead_text(l)]])
                    self.response(b"\xef\xbb\xbf" + output.getvalue().encode(), mime="text/csv; charset=utf-8", filename="sitebrief-leads.csv")
                elif path.startswith("/api/file/"):
                    self.admin()
                    fid = path.rsplit("/", 1)[-1]
                    with app.store.lock:
                        row = app.store.db.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
                    if not row:
                        return self.response({"error": "Файл не найден"}, 404)
                    self.response((app.store.directory / "uploads" / row["path"]).read_bytes(), mime="application/octet-stream", filename=f"attachment-{fid}{Path(row['name']).suffix}")
                elif path.startswith("/api/telegram-file/"):
                    self.admin()
                    parts = path.split("/")
                    if len(parts) != 6 or not app.telegram:
                        raise ValueError("Telegram не подключён")
                    _, _, _, lead_id, answer_key, detail_key = parts
                    with app.store.lock:
                        row = app.store.db.execute("SELECT body FROM leads WHERE id=?", (lead_id,)).fetchone()
                    if not row:
                        raise ValueError("Заявка не найдена")
                    attachment = json.loads(row[0])["answers"][answer_key]["details"][detail_key]["file"]
                    if attachment["source"] != "telegram":
                        raise ValueError("Файл получен другим способом")
                    data = app.telegram.download(attachment["file_id"])
                    self.response(data, mime="application/octet-stream", filename="telegram-attachment" + Path(attachment["name"]).suffix)
                elif path == "/api/brief.txt":
                    sid, _ = self.sid()
                    with app.store.lock:
                        row = app.store.db.execute("SELECT body FROM leads WHERE session_id=? ORDER BY created DESC LIMIT 1", (sid,)).fetchone()
                    if not row:
                        return self.response({"error": "Сначала отправьте бриф"}, 404)
                    self.response(lead_text(json.loads(row[0])), mime="text/plain; charset=utf-8", filename="my-brief.txt")
                else:
                    self.response({"error": "Не найдено"}, 404)

            def do_POST(self):
                try:
                    self.rate_limit()
                    origin = self.headers.get("Origin")
                    host = self.headers.get("Host", "")
                    if self.headers.get("X-Requested-With") != "SiteBrief" or (origin and urlsplit(origin).netloc != host):
                        raise PermissionError("Запрос должен поступить со страницы приложения")
                    if self.headers.get_content_type() != "application/json":
                        raise ValueError("Ожидается JSON")
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 14 * 1024 * 1024:
                        return self.response({"error": "Слишком большой запрос"}, 413)
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("Ожидается объект")
                    self.route_post(urlsplit(self.path).path, body)
                except FlowError as exc:
                    self.response({"error": str(exc)}, 409 if "устарело" in str(exc) else 400)
                except PermissionError as exc:
                    self.response({"error": str(exc)}, 401)
                except (ValueError, KeyError, TypeError) as exc:
                    self.response({"error": str(exc)}, 400)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    self.response({"error": "Не удалось выполнить действие. Данные не потеряны."}, 500)

            def route_post(self, path, b):
                if path == "/api/action":
                    sid, cookie = self.sid()
                    if not isinstance(b.get("revision"), int):
                        raise ValueError("Нужна версия текущего вопроса")
                    self.response(app.engine.action(sid, str(b.get("action", "")), str(b.get("text", "")), b["revision"]), cookie=cookie)
                elif path == "/api/upload":
                    sid, cookie = self.sid()
                    state = app.engine.get(sid)
                    if state.get("input") != "file":
                        raise ValueError("Загрузка доступна только на шаге прикрепления ТЗ")
                    name = str(b.get("name", ""))
                    suffix = Path(name).suffix.lower()
                    if len(name) > 160 or suffix not in ALLOWED_FILES or "/" in name or "\\" in name:
                        raise ValueError("Поддерживаются PDF, DOCX, XLSX, TXT, PNG и JPG")
                    data = base64.b64decode(b.get("data", ""), validate=True)
                    if not 0 < len(data) <= MAX_FILE:
                        raise ValueError("Размер файла: от 1 байта до 10 МБ")
                    fid = secrets.token_hex(16)
                    target = app.store.directory / "uploads" / fid
                    target.write_bytes(data)
                    try:
                        with app.store.transaction() as db:
                            db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)", (fid, sid, name, fid, len(data), "application/octet-stream", time.time()))
                        view = app.engine.action(sid, text=name, revision=b["revision"], attachment={"id": fid, "name": name, "size": len(data), "source": "web"})
                    except Exception:
                        target.unlink(missing_ok=True)
                        with app.store.transaction() as db:
                            db.execute("DELETE FROM files WHERE id=?", (fid,))
                        raise
                    self.response(view, cookie=cookie)
                elif path == "/api/admin/login":
                    attempts = app.login_attempts[self.client_address[0]]
                    now = time.monotonic()
                    while attempts and now - attempts[0] > 600:
                        attempts.popleft()
                    if len(attempts) >= 5:
                        return self.response({"error": "Слишком много попыток. Повторите через 10 минут."}, 429)
                    attempts.append(now)
                    if not app.check_password(b.get("password", "")):
                        raise PermissionError("Неверный пароль")
                    attempts.clear()
                    key, csrf = secrets.token_hex(32), secrets.token_hex(24)
                    with app.store.lock:
                        app.admin_sessions = {k: v for k, v in app.admin_sessions.items() if v["expires"] > time.time()}
                        app.admin_sessions[key] = {"csrf": csrf, "expires": time.time() + 8 * 3600}
                    self.response({"csrf": csrf}, cookie=self.cookie("sb_admin", key, 8 * 3600))
                elif path.startswith("/api/admin/"):
                    self.admin(csrf=True)
                    if path == "/api/admin/logout":
                        app.admin_sessions.pop(self.cookies().get("sb_admin"), None)
                        self.response({"ok": True}, cookie=self.cookie("sb_admin", "", 0))
                    elif path == "/api/admin/config":
                        body = validate_config(b["config"])
                        version = app.store.save_config(body, b["version"])
                        self.response({"version": version})
                    elif path == "/api/admin/status":
                        if b.get("status") not in {"new", "working", "done"}:
                            raise ValueError("Неизвестный статус")
                        with app.store.transaction() as db:
                            db.execute("UPDATE leads SET status=? WHERE id=?", (b["status"], b["id"]))
                        self.response({"ok": True})
                    elif path == "/api/admin/retry":
                        with app.store.transaction() as db:
                            db.execute("UPDATE outbox SET status='pending',attempts=0,due=0,error=NULL WHERE lead_id=? AND status IN ('failed','review')", (b["id"],))
                        self.response({"ok": True})
                    else:
                        self.response({"error": "Не найдено"}, 404)
                else:
                    self.response({"error": "Не найдено"}, 404)

        return Handler

    def server(self, host, port):
        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = False

            def server_bind(self):
                if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                super().server_bind()

        return Server((host, port), self.handler_class())


def safe_csv(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
