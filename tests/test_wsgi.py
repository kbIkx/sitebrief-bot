import io
import json
import tempfile
import time
import unittest
from wsgiref.validate import validator
from briefbot.store import Store
from briefbot.wsgi import HostedApplication
from briefbot.telegram import TelegramError


class WSGITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name,journal_mode='DELETE')
        self.secret='webhook-test-secret-'+'a'*32
        self.hosted=HostedApplication(self.store,'correct-test-password-123','fake-token','12',self.secret,'https://test.pythonanywhere.com')
        self.calls=[]
        self.hosted.telegram.call=lambda method,payload=None,**kw:self.calls.append((method,payload)) or {}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def request(self,path='/',body=None,headers=None,scheme='https',method=None):
        raw=json.dumps(body).encode() if body is not None else b''
        env={'REQUEST_METHOD':method or ('POST' if body is not None else 'GET'),'SCRIPT_NAME':'','PATH_INFO':path,
             'QUERY_STRING':'','SERVER_NAME':'test.pythonanywhere.com','SERVER_PORT':'443','SERVER_PROTOCOL':'HTTP/1.1',
             'HTTP_HOST':'test.pythonanywhere.com','CONTENT_LENGTH':str(len(raw)),
             'CONTENT_TYPE':'application/json','REMOTE_ADDR':'127.0.0.1','wsgi.version':(1,0),'wsgi.url_scheme':scheme,
             'wsgi.input':io.BytesIO(raw),'wsgi.errors':io.StringIO(),'wsgi.multithread':False,'wsgi.multiprocess':False,'wsgi.run_once':False}
        env.update(headers or {})
        captured={}
        def start(status,head,exc_info=None):captured.update(status=status,headers=head)
        result=validator(self.hosted)(env,start)
        data=b''.join(result)
        if hasattr(result,'close'):result.close()
        return captured['status'],dict(captured['headers']),data

    def test_static_demo_without_socket_or_background_thread(self):
        status,head,data=self.request()
        self.assertEqual(status,'200 OK')
        self.assertIn(b'<!doctype html>',data)
        self.assertEqual(head['X-Frame-Options'],'DENY')

    def test_http_redirect_and_host_validation(self):
        status,headers,_=self.request(scheme='http')
        self.assertEqual(status,'308 Permanent Redirect')
        self.assertEqual(headers['Location'],'https://test.pythonanywhere.com/')
        self.assertEqual(self.request(headers={'HTTP_HOST':'evil.example'})[0],'400 Bad Request')

    def test_webhook_requires_secret(self):
        update={'update_id':1,'message':{'text':'/start','chat':{'id':12,'type':'private'}}}
        self.assertEqual(self.request('/telegram/webhook',update)[0],'403 Forbidden')
        self.assertEqual(self.calls,[])

    def test_webhook_duplicate_does_not_send_twice(self):
        update={'update_id':1,'message':{'text':'/start','chat':{'id':12,'type':'private'}}}
        head={'HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN':self.secret}
        self.assertEqual(self.request('/telegram/webhook',update,head)[0],'200 OK')
        count=len(self.calls)
        self.assertGreater(count,0)
        self.assertEqual(self.request('/telegram/webhook',update,head)[0],'200 OK')
        self.assertEqual(len(self.calls),count)

    def test_callback_and_state_survive_webhook(self):
        head={'HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN':self.secret}
        for n,action in [(1,'start'),(2,'service:improve')]:
            rev=self.hosted.engine.get('tg:12')['revision']
            update={'update_id':n,'callback_query':{'id':'q','data':str(rev)+'|'+action,'message':{'chat':{'id':12,'type':'private'}}}}
            self.assertEqual(self.request('/telegram/webhook',update,head)[0],'200 OK')
        self.assertEqual(self.hosted.engine.get('tg:12')['node'],'platform')

    def test_in_progress_duplicate_retries_instead_of_dropping(self):
        self.store.db.execute("INSERT INTO webhook_receipts VALUES(10,'processing',?)",(time.time(),))
        status,head,_=self.request('/telegram/webhook',{'update_id':10},{'HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN':self.secret})
        self.assertEqual(status,'503 Service Unavailable')
        self.assertEqual(head['Retry-After'],'5')
        self.assertEqual(self.calls,[])

    def test_stale_processing_receipt_is_recovered(self):
        self.store.db.execute("INSERT INTO webhook_receipts VALUES(10,'processing',?)",(time.time()-120,))
        update={'update_id':10,'message':{'text':'/start','chat':{'id':12,'type':'private'}}}
        self.assertEqual(self.request('/telegram/webhook',update,{'HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN':self.secret})[0],'200 OK')
        self.assertEqual(self.store.db.execute('SELECT status FROM webhook_receipts').fetchone()[0],'done')

    def test_reply_network_failure_preserves_update_and_state(self):
        def lost(*args,**kw):raise TelegramError('network',ambiguous=True)
        self.hosted.telegram.call=lost
        update={'update_id':1,'message':{'text':'/start','chat':{'id':12,'type':'private'}}}
        self.assertEqual(self.request('/telegram/webhook',update,{'HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN':self.secret})[0],'200 OK')
        self.assertEqual(self.hosted.engine.get('tg:12')['screen'],'privacy')

    def test_web_api_cookie_and_csrf_adapter(self):
        status,head,data=self.request('/api/state')
        self.assertEqual(status,'200 OK')
        self.assertIn('Secure',head['Set-Cookie'])
        v=json.loads(data)
        cookie=head['Set-Cookie'].split(';')[0]
        status,_,data=self.request('/api/action',{'action':'start','revision':v['revision']},{'HTTP_COOKIE':cookie,'HTTP_X_REQUESTED_WITH':'SiteBrief','HTTP_ORIGIN':'https://test.pythonanywhere.com'})
        self.assertEqual(status,'200 OK')
        self.assertEqual(json.loads(data)['screen'],'menu')

    def test_admin_login_and_leads_in_wsgi(self):
        self.assertEqual(self.request('/api/admin/leads')[0],'401 Unauthorized')
        status,head,data=self.request('/api/admin/login',{'password':'correct-test-password-123'},{'HTTP_X_REQUESTED_WITH':'SiteBrief'})
        self.assertEqual(status,'200 OK')
        cookie=head['Set-Cookie'].split(';')[0]
        self.assertEqual(self.request('/api/admin/leads',headers={'HTTP_COOKIE':cookie})[0],'200 OK')


if __name__=='__main__':unittest.main()
