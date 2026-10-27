import base64
import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from briefbot.engine import Engine
from briefbot.store import Store
from briefbot.web import Application, safe_csv


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.app = Application(self.store, Engine(self.store), 'correct-test-password-123')
        self.server = self.app.server('127.0.0.1', 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.close()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json','X-Requested-With': 'SiteBrief', **(headers or {})})
        try:
            with self.client.open(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def login(self):
        status, data = self.request('/api/admin/login', {'password': 'correct-test-password-123'})
        self.assertEqual(status, 200)
        return {'X-CSRF-Token': data['csrf']}

    def test_admin_authorization_and_csrf(self):
        self.assertEqual(self.request('/api/admin/leads')[0], 401)
        self.assertEqual(self.request('/api/admin/login', {'password':'wrong'})[0], 401)
        headers = self.login()
        status, data = self.request('/api/admin/config')
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/admin/config', data)[0], 401)
        self.assertEqual(self.request('/api/admin/config', data, headers)[0], 200)
        self.assertEqual(self.request('/api/admin/logout', {}, headers)[0], 200)
        self.assertEqual(self.request('/api/admin/leads')[0], 401)

    def test_cross_origin_rejected(self):
        self.assertEqual(self.request('/api/admin/login', {'password':'correct-test-password-123'}, {'Origin':'https://evil.example'})[0], 401)

    def test_session_cookie_and_version(self):
        _, v = self.request('/api/state')
        self.assertEqual(v['screen'], 'privacy')
        status, result = self.request('/api/action', {'action':'start','revision':v['revision']})
        self.assertEqual(status, 200)
        self.assertEqual(result['screen'], 'menu')
        self.assertEqual(self.request('/api/action', {'action':'service:new','revision':v['revision']})[0], 409)
        self.assertEqual(self.request('/api/state')[1]['screen'], 'menu')

    def test_upload_restricted_to_file_step(self):
        self.request('/api/state')
        status, _ = self.request('/api/upload', {'revision':0,'name':'test.pdf','data':base64.b64encode(b'test').decode()})
        self.assertEqual(status, 400)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM files').fetchone()[0], 0)

    def test_successful_upload_and_private_download(self):
        _,v=self.request('/api/state')
        for action,text in [('start',''),('service:new',''),('opt:other',''),('','Тестовый проект'),('opt:0',''),('opt:file','')]:
            status,v=self.request('/api/action',{'action':action,'text':text,'revision':v['revision']})
            self.assertEqual(status,200)
        self.assertEqual(v['input'],'file')
        status,v=self.request('/api/upload',{'revision':v['revision'],'name':'brief.txt','data':base64.b64encode(b'Demo brief').decode()})
        self.assertEqual(status,200)
        self.assertEqual(v['node'],'name')
        fid=self.store.db.execute('SELECT id FROM files').fetchone()[0]
        self.assertEqual(self.request('/api/file/'+fid)[0],401)
        self.login()
        with self.client.open(self.base+'/api/file/'+fid) as r:
            self.assertEqual(r.read(),b'Demo brief')

    def test_instance_lock_prevents_second_process(self):
        from pathlib import Path
        from briefbot.locking import acquire_lock
        lock=acquire_lock(Path(self.tmp.name)/'test.lock')
        try:
            with self.assertRaises(RuntimeError):acquire_lock(Path(self.tmp.name)/'test.lock')
        finally:lock.close()

    def test_csv_formula_protection(self):
        for v in ['=1+1','+79991234567','-123','@SUM(A1)','  =cmd()']:
            self.assertTrue(safe_csv(v).startswith("'"))
        self.assertEqual(safe_csv('normal'), 'normal')

    def test_login_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.request('/api/admin/login', {'password':'wrong'})[0], 401)
        self.assertEqual(self.request('/api/admin/login', {'password':'correct-test-password-123'})[0], 429)

    def test_static_security_headers(self):
        with self.client.open(self.base) as response:
            self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
            self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
            self.assertIn(b'<!doctype html>', response.read())


if __name__ == '__main__': unittest.main()
