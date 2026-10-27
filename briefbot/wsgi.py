"""WSGI hosting and authenticated Telegram webhooks. No background worker required.

The existing HTTP handlers are reused through an in-memory HTTP adapter. This
keeps authorization and response headers identical to the desktop version.
Only the standard library is used; no socket is opened by the WSGI application.
"""
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit
from .engine import Engine
from .store import Store
from .telegram import Telegram, TelegramError
from .web import Application


class WebhookTelegram(Telegram):
    def call(self, method, payload=None, timeout=12):
        return super().call(method, payload, timeout)


class MemorySocket:
    def __init__(self, request):
        self.reader = io.BytesIO(request)
        self.written = bytearray()

    def makefile(self, *_):
        return self.reader

    def sendall(self, data):
        self.written.extend(data)


class HostedApplication:
    def __init__(self, store, password, token, manager_chat, webhook_secret, public_base_url):
        if not isinstance(webhook_secret, str) or len(webhook_secret) < 32:
            raise ValueError("WEBHOOK_SECRET must contain at least 32 characters")
        origin = urlsplit(public_base_url)
        if origin.scheme != 'https' or not origin.hostname or origin.path not in {'', '/'} or origin.query or origin.fragment or origin.username:
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin")
        if len(password) < 12:
            raise ValueError("ADMIN_PASSWORD must contain at least 12 characters")
        self.store = store
        self.engine = Engine(store)
        self.app = Application(store, self.engine, password, secure=True)
        self.telegram = WebhookTelegram(token, store, self.engine, manager_chat, self.app) if token else None
        self.app.telegram = self.telegram
        self.app.telegram_status = 'connected' if token else 'not_configured'
        self.secret = webhook_secret
        self.origin = public_base_url.rstrip('/')
        self.hostname = origin.netloc.lower()
        self.handler = self.app.handler_class()

    @staticmethod
    def respond(start_response, status, body, headers=()):
        data = json.dumps(body, ensure_ascii=False).encode()
        start_response(status, [('Content-Type','application/json; charset=utf-8'),
            ('Content-Length',str(len(data))),('Cache-Control','no-store'),
            ('X-Content-Type-Options','nosniff'), *headers])
        return [data]

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO','/')
        if environ.get('HTTP_HOST','').lower() != self.hostname:
            return self.respond(start_response,'400 Bad Request',{'error':'Unknown host'})
        if path == '/telegram/webhook':
            return self.webhook(environ,start_response)
        if environ.get('wsgi.url_scheme') != 'https':
            start_response('308 Permanent Redirect',[('Location',self.origin+path),('Content-Length','0'),('Content-Type','text/plain; charset=utf-8')])
            return [b'']
        try:
            length = int(environ.get('CONTENT_LENGTH') or 0)
        except ValueError:
            return self.respond(start_response,'400 Bad Request',{'error':'Invalid content length'})
        if not 0 <= length <= 14*1024*1024:
            return self.respond(start_response,'413 Content Too Large',{'error':'Request too large'})
        body = environ['wsgi.input'].read(length)
        if len(body) != length:
            return self.respond(start_response,'400 Bad Request',{'error':'Incomplete request'})
        method = environ.get('REQUEST_METHOD','GET')
        query = environ.get('QUERY_STRING','')
        target = path + ('?'+query if query else '')
        if method not in {'GET','POST'} or any(c in target for c in '\r\n'):
            return self.respond(start_response,'405 Method Not Allowed',{'error':'Method not allowed'})
        headers = {'Host':self.hostname,'Content-Length':str(length)}
        if environ.get('CONTENT_TYPE'):
            headers['Content-Type']=environ['CONTENT_TYPE']
        for key, value in environ.items():
            if key.startswith('HTTP_') and key not in {'HTTP_HOST','HTTP_CONTENT_LENGTH','HTTP_TRANSFER_ENCODING','HTTP_CONNECTION'}:
                name=key.removeprefix('HTTP_').replace('_','-')
                if any(c in str(value) for c in '\r\n'):
                    return self.respond(start_response,'400 Bad Request',{'error':'Invalid header'})
                headers[name]=str(value)
        raw=(method+' '+target+' HTTP/1.0\r\n'+''.join(k+': '+v+'\r\n' for k,v in headers.items())+'\r\n').encode('latin-1')+body
        connection=MemorySocket(raw)
        self.handler(connection,(environ.get('REMOTE_ADDR','127.0.0.1'),0),object())
        head, sep, data = bytes(connection.written).partition(b'\r\n\r\n')
        if not sep:
            return self.respond(start_response,'500 Internal Server Error',{'error':'Invalid application response'})
        lines=head.decode('latin-1').split('\r\n')
        status=lines[0].split(' ',1)[1]
        response_headers=[]
        for line in lines[1:]:
            key,value=line.split(':',1)
            if key.lower() not in {'connection','transfer-encoding','keep-alive','upgrade'}:
                response_headers.append((key,value.strip()))
        # Paid always-on tasks are unnecessary. Process ready notifications on writes.
        if method=='POST' and status.startswith('2') and self.telegram:
            self.telegram.notify_once()
        start_response(status,response_headers)
        return [data]

    def webhook(self,environ,start_response):
        if environ.get('REQUEST_METHOD')!='POST':
            return self.respond(start_response,'405 Method Not Allowed',{'error':'POST required'})
        supplied=environ.get('HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN','')
        if not hmac.compare_digest(supplied,self.secret):
            return self.respond(start_response,'403 Forbidden',{'error':'Invalid webhook secret'})
        if not self.telegram:
            return self.respond(start_response,'503 Service Unavailable',{'error':'Bot is not configured'})
        try:
            length=int(environ.get('CONTENT_LENGTH') or 0)
            if not 0 < length <= 1024*1024:
                return self.respond(start_response,'413 Content Too Large',{'error':'Update too large'})
            update=json.loads(environ['wsgi.input'].read(length))
            update_id=update['update_id']
            if not isinstance(update_id,int) or update_id<0:
                raise ValueError('Invalid update')
        except (ValueError,KeyError,TypeError):
            return self.respond(start_response,'400 Bad Request',{'error':'Invalid update'})
        duplicate=False
        with self.store.transaction() as db:
            receipt=db.execute('SELECT status,updated FROM webhook_receipts WHERE update_id=?',(update_id,)).fetchone()
            if receipt and receipt['status']=='done':
                duplicate=True
            elif receipt and time.time()-receipt['updated']<60:
                return self.respond(start_response,'503 Service Unavailable',{'error':'Update is being processed'},[('Retry-After','5')])
            else:
                db.execute("INSERT OR REPLACE INTO webhook_receipts VALUES(?,'processing',?)",(update_id,time.time()))
        if duplicate:
            self.telegram.notify_once()
            return self.respond(start_response,'200 OK',{'ok':True})
        try:
            self.telegram.handle(update)
        except TelegramError:
            # Persisted answers remain recoverable with /resume. Avoid duplicate sends.
            self.app.telegram_status='connection_error'
        except Exception:
            with self.store.transaction() as db:
                db.execute('DELETE FROM webhook_receipts WHERE update_id=?',(update_id,))
            return self.respond(start_response,'503 Service Unavailable',{'error':'Retry update later'})
        with self.store.transaction() as db:
            db.execute("UPDATE webhook_receipts SET status='done',updated=? WHERE update_id=?",(time.time(),update_id))
        self.telegram.notify_once()
        return self.respond(start_response,'200 OK',{'ok':True})


def create_application(env_file=None):
    # Imported only here to keep test imports free of filesystem/environment effects.
    from run import load_env
    root=Path(__file__).resolve().parent.parent
    env_file=Path(env_file or os.environ.get('SITEBRIEF_ENV_FILE',root/'.env'))
    load_env(env_file)
    directory=Path(os.environ.get('DATA_DIR',root/'data'))
    directory.mkdir(parents=True,exist_ok=True)
    password=os.environ.get('ADMIN_PASSWORD')
    if not password:
        path=directory/'admin-password.txt'
        if not path.exists():
            path.write_text(secrets.token_urlsafe(24),encoding='utf-8')
            path.chmod(0o600)
        password=path.read_text(encoding='utf-8').strip()
    # DELETE journal works on hosted/network filesystems; no local daemon/lockfile.
    store=Store(directory,journal_mode='DELETE')
    return HostedApplication(store,password,os.environ.get('BOT_TOKEN',''),
        os.environ.get('MANAGER_CHAT_ID',''),os.environ.get('WEBHOOK_SECRET',''),
        os.environ.get('PUBLIC_BASE_URL',''))
