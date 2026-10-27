"""Run: python run.py. Python 3.11+; no third-party dependencies."""
import argparse
import os
from pathlib import Path
import secrets
import threading
from briefbot.engine import Engine
from briefbot.store import Store
from briefbot.telegram import Telegram
from briefbot.web import Application
from briefbot.locking import acquire_lock


def load_env(path):
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"BOT_TOKEN", "MANAGER_CHAT_ID", "ADMIN_PASSWORD", "DATA_DIR", "PORT", "HOST", "WEBHOOK_SECRET", "PUBLIC_BASE_URL"}:
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="SiteBrief — Telegram bot and customer panel")
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--host")
    parser.add_argument("--secure-cookies", action="store_true", help="Required behind a public HTTPS reverse proxy")
    args = parser.parse_args()
    load_env(args.env_file)
    directory = args.data_dir or Path(os.environ.get("DATA_DIR", root / "data"))
    directory.mkdir(parents=True, exist_ok=True)
    try:
        instance_lock = acquire_lock(directory / '.runtime.lock')
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    password = os.environ.get("ADMIN_PASSWORD")
    password_file = directory / "admin-password.txt"
    if not password:
        if not password_file.exists():
            password_file.write_text(secrets.token_urlsafe(18), encoding="utf-8")
            try:
                password_file.chmod(0o600)
            except OSError:
                pass
        password = password_file.read_text(encoding="utf-8").strip()
    if len(password) < 12:
        raise SystemExit("ADMIN_PASSWORD must contain at least 12 characters")
    store = Store(directory)
    engine = Engine(store)
    app = Application(store, engine, password, secure=args.secure_cookies)
    token = os.environ.get("BOT_TOKEN", "")
    telegram = None
    if token:
        telegram = Telegram(token, store, engine, os.environ.get("MANAGER_CHAT_ID", ""), app)
        app.telegram = telegram
        for target in (telegram.run, telegram.notifications):
            threading.Thread(target=target, daemon=True).start()
    server = app.server(args.host or os.environ.get("HOST", "127.0.0.1"), args.port or int(os.environ.get("PORT", "8765")))
    print(f"SiteBrief: http://127.0.0.1:{server.server_port}", flush=True)
    print(f"Customer panel: http://127.0.0.1:{server.server_port}/admin", flush=True)
    print(f"Admin password file (keep private): {password_file.resolve()}", flush=True)
    print("Telegram: configured" if token else "Telegram: not configured; browser demo is available", flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        if telegram:
            telegram.stop.set()
        server.server_close()
        # Daemon polling threads end with the process. Never close their DB mid-request.


if __name__ == "__main__":
    main()
