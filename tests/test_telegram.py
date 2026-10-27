import tempfile
import json
import time
import unittest
from briefbot.engine import Engine
from briefbot.store import Store
from briefbot.telegram import Telegram, TelegramError, split_text


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.engine = Engine(self.store)
        self.telegram = Telegram('fake-not-a-token', self.store, self.engine)
        self.calls = []
        self.telegram.call = lambda method, payload=None, **kwargs: self.calls.append((method,payload)) or {}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def message(self, text, n=1):
        self.telegram.handle({'update_id':n,'message':{'text':text,'chat':{'id':12,'type':'private'}}})

    def callback(self, action, n=2, rev=None):
        if rev is None: rev=self.engine.get('tg:12')['revision']
        self.telegram.handle({'update_id':n,'callback_query':{'id':'q','data':str(rev)+'|'+action,
            'message':{'chat':{'id':12,'type':'private'}}}})

    def test_start_consent_and_navigation(self):
        self.message('/start')
        self.assertEqual(self.engine.get('tg:12')['screen'], 'privacy')
        self.callback('start')
        self.callback('service:new', 3)
        self.assertEqual(self.engine.get('tg:12')['node'], 'kind')
        self.assertTrue(any(m=='answerCallbackQuery' for m,_ in self.calls))

    def test_stale_callback_does_not_change_state(self):
        self.callback('start')
        self.callback('service:improve',3)
        before=self.engine.get('tg:12')
        self.callback('opt:0',4,rev=0)
        self.assertEqual(self.engine.get('tg:12'),before)

    def test_group_is_ignored(self):
        self.telegram.handle({'update_id':1,'message':{'text':'/start','chat':{'id':12,'type':'group'}}})
        self.assertEqual(self.calls,[])

    def test_text_validation_and_keyboard_limits(self):
        self.callback('start')
        self.callback('service:improve',3)
        self.message('случайный текст',4)
        self.assertEqual(self.engine.get('tg:12')['node'],'platform')
        for method,payload in self.calls:
            if method=='sendMessage':
                self.assertLessEqual(len(payload['text']),4096)
                for row in payload.get('reply_markup',{}).get('inline_keyboard',[]):
                    for button in row:
                        if 'callback_data' in button:
                            self.assertLessEqual(len(button['callback_data'].encode()),64)

    def test_split_long_unicode_text(self):
        chunks=split_text('Длинный текст\n'*2000)
        self.assertTrue(all(len(c)<=3800 for c in chunks))

    def seed_notification(self):
        body={'id':'SB-TEST','service_name':'Тестовая заявка','answers':{}}
        with self.store.transaction() as db:
            db.execute("INSERT INTO leads(id,session_id,draft,body,created) VALUES('SB-TEST','tg:12','draft',?,?)",(json.dumps(body),time.time()))
            db.execute("INSERT INTO outbox(id,lead_id) VALUES('job','SB-TEST')")
        self.telegram.manager_chat='12'

    def test_notification_is_not_repeated_after_success(self):
        self.seed_notification()
        self.assertTrue(self.telegram.notify_once())
        self.assertFalse(self.telegram.notify_once())
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.store.db.execute('SELECT status FROM outbox').fetchone()[0],'sent')

    def test_unknown_notification_result_requires_review(self):
        self.seed_notification()
        def lost(*_,**__):raise TelegramError('network',ambiguous=True)
        self.telegram.call=lost
        self.telegram.notify_once()
        self.assertEqual(self.store.db.execute('SELECT status FROM outbox').fetchone()[0],'review')
        self.assertFalse(self.telegram.notify_once())
        self.assertEqual(len(self.store.leads()),1)

    def test_rate_limit_reschedules_without_losing_lead(self):
        self.seed_notification()
        def limited(*_,**__):raise TelegramError(429,retry_after=30)
        self.telegram.call=limited
        self.telegram.notify_once()
        row=self.store.db.execute('SELECT status,due,attempts FROM outbox').fetchone()
        self.assertEqual(row['status'],'pending')
        self.assertGreater(row['due'],time.time()+25)
        self.assertEqual(row['attempts'],1)
        self.assertFalse(self.telegram.notify_once())

    def test_forbidden_notification_does_not_loop(self):
        self.seed_notification()
        def forbidden(*_,**__):raise TelegramError(403)
        self.telegram.call=forbidden
        self.telegram.notify_once()
        self.assertEqual(self.store.db.execute('SELECT status FROM outbox').fetchone()[0],'failed')
        self.assertFalse(self.telegram.notify_once())

    def test_interrupted_send_is_not_blindly_replayed(self):
        self.seed_notification()
        self.store.db.execute("UPDATE outbox SET status='sending'")
        self.store.close()
        self.store=Store(self.tmp.name)
        self.assertEqual(self.store.db.execute('SELECT status FROM outbox').fetchone()[0],'review')


if __name__ == '__main__': unittest.main()
