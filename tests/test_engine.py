import copy
import json
import tempfile
import threading
import unittest
from briefbot.engine import Engine, FlowError, valid_text
from briefbot.scenario import build_config, validate_config
from briefbot.store import Store


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.engine = Engine(self.store)
        self.counter = 0
        self.fixtures = {}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def sid(self):
        self.counter += 1
        return 'web:test-' + str(self.counter)

    def act(self, sid, action='', text='', **kw):
        return self.engine.action(sid, action, text, **kw)

    def begin(self, service='new'):
        sid = self.sid()
        self.act(sid, 'start')
        return sid, self.act(sid, 'service:' + service)

    def complete(self, kind='goods', service='new', overrides=None):
        overrides = overrides or {}
        sid, v = self.begin(service)
        visited = []
        for _ in range(150):
            if v['screen'] == 'review':
                return sid, v, visited
            node = v.get('node')
            if node:
                visited.append(node)
                if v['screen']=='question' and node not in self.fixtures:
                    self.fixtures[node] = json.loads(self.store.db.execute('SELECT body FROM sessions WHERE id=?',(sid,)).fetchone()[0])
            if v['screen'] == 'detail':
                if v['input'] == 'file':
                    v = self.act(sid, text='brief.pdf', attachment={'id': 'test', 'name': 'brief.pdf', 'size': 10, 'source': 'web'})
                else:
                    v = self.act(sid, text='https://example.org Хороший пример' if v['input'] == 'url' else 'Подробности запроса')
            elif v.get('input'):
                text = {'phone': '+7 (999) 123-45-67', 'name': 'Тестовый Клиент', 'url': 'https://example.org'}.get(v['input'], 'Тестовый ответ')
                v = self.act(sid, text=text)
            elif node == 'sections':
                v = self.act(sid, 'done')
            else:
                opts = [o for o in v['options'] if o['action'].startswith('opt:')]
                option = overrides.get(node, 'opt:' + kind if node == 'kind' else opts[0]['action'])
                was_multi = v.get('multi')
                v = self.act(sid, option)
                if was_multi:
                    v = self.act(sid, 'done')
        self.fail('Scenario did not reach review')

    def test_all_main_paths(self):
        for kind, service in [('goods','new'),('services','new'),('both','new'),('other','new'),('','improve'),('','promote')]:
            with self.subTest(kind=kind, service=service):
                sid, v, visited = self.complete(kind, service)
                self.assertEqual(v['screen'], 'review')
                result = self.act(sid, 'submit', revision=v['revision'])
                self.assertEqual(result['screen'], 'success')
                self.assertTrue(result['lead_id'].startswith('SB-'))
        self.assertEqual(len(self.store.leads()), 6)

    def test_conditional_paths(self):
        _, _, goods = self.complete('goods')
        _, _, services = self.complete('services')
        _, _, both = self.complete('both')
        self.assertIn('delivery', goods)
        self.assertNotIn('popups', goods)
        self.assertIn('structure', services)
        self.assertIn('content', services)
        self.assertIn('popups', services)
        self.assertNotIn('delivery', services)
        self.assertNotIn('design', services)
        self.assertIn('priority', both)
        self.assertIn('sphere', both)
        self.assertIn('split', both)

    def test_promotion_shortcuts(self):
        for answer in ['never', 'consult']:
            sid, v, visited = self.complete(service='promote', overrides={'promote_current': 'opt:' + answer})
            self.assertNotIn('promote_helpers', visited)
            self.assertNotIn('geography', visited)
            self.assertIn('name', visited)

    def test_custom_choices_and_upload(self):
        sid, v, _ = self.complete('both', overrides={'sphere':'opt:other','features':'opt:other',
            'variants':'opt:other','reference':'opt:file'})
        answers = {r['key']: r for r in v['summary']}
        self.assertIn('Подробности', answers['features']['value'])
        self.assertIn('brief.pdf', answers['reference']['value'])

    def test_multiselect_multiple_details(self):
        sid, v = self.begin('promote')
        self.act(sid, 'opt:site')
        self.act(sid, 'opt:social')
        v = self.act(sid, 'done')
        self.assertEqual(v['screen'], 'detail')
        with self.assertRaises(FlowError):
            self.act(sid, text='not a url')
        v = self.act(sid, text='https://example.org')
        self.assertEqual(v['screen'], 'detail')
        v = self.act(sid, text='https://example.org/social')
        self.assertEqual(v['node'], 'promote_types')
        self.assertEqual(len(v['draft_summary']), 1)

    def test_exclusive_options(self):
        sid, _ = self.begin('promote')
        self.act(sid, 'opt:site')
        v = self.act(sid, 'opt:none')
        selected = [o['action'] for o in v['options'] if o.get('selected')]
        self.assertEqual(selected, ['opt:none'])
        v = self.act(sid, 'opt:social')
        self.assertEqual([o['action'] for o in v['options'] if o.get('selected')], ['opt:social'])

    def test_consent_required(self):
        sid = self.sid()
        with self.assertRaises(FlowError):
            self.act(sid, 'service:new')

    def test_resume_after_restart(self):
        sid, _ = self.begin()
        self.act(sid, 'opt:goods')
        self.act(sid, 'opt:yes')
        expected = self.engine.get(sid)
        self.store.close()
        self.store = Store(self.tmp.name)
        self.engine = Engine(self.store)
        actual = self.engine.get(sid)
        self.assertEqual(expected, actual)

    def test_stale_and_parallel_requests(self):
        sid, v = self.begin()
        results = []
        def click():
            try:
                self.act(sid, 'opt:goods', revision=v['revision'])
                results.append('ok')
            except FlowError:
                results.append('stale')
        threads = [threading.Thread(target=click) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(results.count('ok'), 1)
        self.assertEqual(results.count('stale'), 4)

    def test_duplicate_event_and_submission(self):
        sid, v, _ = self.complete()
        one = self.act(sid, 'submit', revision=v['revision'], event_id='update-1')
        two = self.act(sid, 'submit', revision=v['revision'], event_id='update-1')
        self.assertEqual(one['lead_id'], two['lead_id'])
        self.assertEqual(len(self.store.leads()), 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 1)

    def test_review_edit_and_back(self):
        sid, v, _ = self.complete()
        v = self.act(sid, 'edit:name')
        self.assertEqual(v['node'], 'name')
        v = self.act(sid, text='Новое Имя')
        self.assertEqual(v['screen'], 'review')
        self.assertEqual(next(r['value'] for r in v['summary'] if r['key']=='name'), 'Новое Имя')
        self.assertTrue(any(r['key'] == 'phone' for r in v['summary']))

    def test_back_discards_downstream_answers(self):
        sid, _ = self.begin()
        self.act(sid, 'opt:goods')
        self.act(sid, 'opt:yes')
        v = self.act(sid, 'back')
        self.assertEqual(v['node'], 'payment')
        self.act(sid, 'back')
        v = self.act(sid, 'opt:services')
        self.assertEqual(v['node'], 'sphere')
        self.assertNotIn('payment', [r['key'] for r in v['draft_summary']])

    def test_sections_categories_and_custom(self):
        # Reach the actual sections screen through the goods route.
        sid, _ = self.begin()
        for action in ['opt:goods','opt:no','opt:no','opt:no','opt:no','done','opt:none','done','opt:0','opt:0']:
            v = self.act(sid, action)
        self.assertEqual(v['node'], 'sections')
        self.act(sid, 'cat:basic')
        self.act(sid, 'section:0')
        self.act(sid, 'done')
        self.act(sid, 'section_other')
        self.act(sid, text='Страница для дилеров')
        v = self.act(sid, 'done')
        self.assertEqual(v['node'], 'design')
        section = next(r for r in v['draft_summary'] if r['key']=='sections')
        self.assertIn('О компании', section['value'])
        self.assertIn('для дилеров', section['value'])

    def test_fresh_draft_requires_confirmation(self):
        sid, _ = self.begin()
        self.act(sid, 'opt:goods')
        v = self.act(sid, 'service:improve')
        self.assertEqual(v['screen'], 'restart_confirm')
        v = self.act(sid, 'back')
        self.assertEqual(v['node'], 'payment')
        self.act(sid, 'service:improve')
        v = self.act(sid, 'confirm_restart')
        self.assertEqual(v['node'], 'platform')
        self.assertEqual(v['draft_summary'], [])

    def test_portfolio_and_contacts(self):
        sid = self.sid()
        self.act(sid, 'start')
        v = self.act(sid, 'portfolio')
        self.assertEqual(len([o for o in v['options'] if o['action'].startswith('portfolio:')]), 8)
        for i in range(7):
            v = self.act(sid, 'portfolio:' + str(i))
            self.assertIn('пока не добавлены', v['text'])
            self.act(sid, 'back')
        self.act(sid, 'portfolio:other')
        v = self.act(sid, text='Медицинские сайты')
        self.assertIn('Медицинские', v['text'])
        v = self.act(sid, 'contact')
        self.assertEqual(v['screen'], 'contact')

    def test_delete_data(self):
        sid, _, _ = self.complete()
        self.act(sid, 'submit')
        self.act(sid, 'delete')
        v = self.act(sid, 'confirm_delete')
        self.assertEqual(v['screen'], 'privacy')
        self.assertEqual(self.store.leads(), [])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)

    def test_config_versions_and_protection(self):
        sid, _ = self.begin()
        version, cfg = self.store.config()
        cfg['nodes']['kind']['title'] = 'Обновлённый вопрос'
        self.store.save_config(validate_config(cfg), version)
        self.assertNotEqual(self.engine.get(sid)['title'], 'Обновлённый вопрос')
        sid2, v = self.begin()
        self.assertEqual(v['title'], 'Обновлённый вопрос')
        bad = copy.deepcopy(cfg)
        bad['nodes']['kind']['options'][0]['next'] = 'review'
        with self.assertRaises(ValueError): validate_config(bad)
        bad = copy.deepcopy(cfg)
        bad['contact_url'] = 'javascript:alert(1)'
        with self.assertRaises(ValueError): validate_config(bad)
        with self.assertRaises(ValueError): self.store.save_config(cfg, version)

    def test_validators(self):
        self.assertEqual(valid_text('8 (999) 123-45-67','phone'), '+79991234567')
        for text, validator in [('111','phone'),('abc','phone'),('a','name'),('javascript:alert(1)','url'),('', 'text')]:
            with self.subTest(text=text):
                with self.assertRaises(FlowError): valid_text(text, validator)
        self.assertEqual(valid_text('https://example.org/ комментарий','url'), 'https://example.org/ комментарий')

    def test_every_scenario_choice_is_accepted(self):
        for kind, service in [('goods','new'),('services','new'),('both','new'),('other','new'),('','improve'),('','promote')]:
            self.complete(kind,service)
        nodes=build_config()['nodes']
        self.assertEqual(set(self.fixtures),set(nodes))
        checked=0
        for key,node in nodes.items():
            if node['kind'] not in {'single','multi'}:
                continue
            for option in node['options']:
                with self.subTest(node=key,option=option['id']):
                    sid=self.sid()
                    fixture=copy.deepcopy(self.fixtures[key])
                    fixture['id']=sid
                    with self.store.transaction() as db:
                        db.execute('INSERT INTO sessions VALUES(?,?,0)',(sid,json.dumps(fixture)))
                    v=self.act(sid,'opt:'+option['id'])
                    if node['kind']=='multi':v=self.act(sid,'done')
                    if v['screen']=='detail':
                        if v['input']=='file':
                            v=self.act(sid,text='test.pdf',attachment={'name':'test.pdf','id':'test','source':'web'})
                        else:
                            v=self.act(sid,text='https://example.org' if v['input']=='url' else 'Уточнение')
                    self.assertNotEqual(v['screen'],'detail')
                    checked+=1
        self.assertGreater(checked,130)


if __name__ == '__main__':
    unittest.main()
