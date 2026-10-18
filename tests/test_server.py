import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import server

ROOT = Path(__file__).resolve().parents[1]


class SharedListsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.origin = f'http://127.0.0.1:{cls.port}'
        cls.base = cls.origin + '/test-hidden-path-123456'
        cls.env = dict(os.environ, DATA_DIR=cls.temp.name, PORT=str(cls.port), HOST='127.0.0.1', APP_PATH='test-hidden-path-123456')
        cls.start()

    @classmethod
    def start(cls):
        cls.process = subprocess.Popen([sys.executable, 'server.py'], cwd=ROOT, env=cls.env, stdout=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urlopen(cls.base + '/api/state', timeout=.2):
                    return
            except OSError:
                time.sleep(.03)
        raise RuntimeError('Server did not start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.temp.cleanup()

    def request(self, path='/api/state', payload=None, nickname='Alice', root=False):
        headers = {'Content-Type':'application/json', 'X-Nickname':nickname}
        request = Request((self.origin if root else self.base) + path,
                          data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if 'application/json' in response.headers.get('Content-Type','') else raw

    def act(self, action, **data):
        status, response = self.request('/api/action', {'action':action, **data})
        self.assertEqual(status, 200, response)
        return response

    def new(self, kind='simple', completion='strike'):
        result = self.act('list.create', title='Test', kind=kind, completion=completion)
        return next(l for l in result['state']['lists'] if l['id'] == result['result']['id'])

    def edit(self, value, action, **data):
        result = self.act(action, id=value['id'], version=value['version'], **data)
        return next(l for l in result['state']['lists'] if l['id'] == value['id'])

    def test_hidden_path_covers_assets_and_api(self):
        for path in ('/', '/api/state', '/api/action', '/app.js', '/static/index.html', '/test-hidden-path-123456/../server.py'):
            self.assertEqual(self.request(path, root=True)[0], 404)
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/app.js')[0], 200)

    def test_generated_access_path_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(server, 'DATA', Path(directory)), patch.dict(os.environ, {'APP_PATH':''}):
                first = server.initialize()
                second = server.initialize()
                self.assertEqual(first, second)
                self.assertGreaterEqual(len(first), 33)
                self.assertEqual((Path(directory) / 'access-path').stat().st_mode & 0o777, 0o600)

    def test_shared_identity_persistence_and_conflicts(self):
        value = self.new()
        self.assertEqual(value['groups'], [])
        self.assertEqual(value['createdBy'], 'Alice')
        stale = dict(value)
        value = self.edit(value, 'item.add', text='Milk', category='')
        status, response = self.request(nickname='Bob')
        self.assertEqual(status, 200)
        self.assertTrue(any(l['id'] == value['id'] for l in response['lists']))
        status, _ = self.request('/api/action', {'action':'list.trash', 'id':value['id'], 'version':stale['version'], 'trashed':True})
        self.assertEqual(status, 409)
        self.process.terminate()
        self.process.wait(timeout=5)
        self.start()
        self.assertTrue(any(l['id'] == value['id'] for l in self.request()[1]['lists']))

    def test_completion_modes_and_item_restore(self):
        for mode in ('strike', 'trash', 'delete'):
            value = self.new(completion=mode)
            value = self.edit(value, 'item.add', text='Do it')
            item_id = value['items'][0]['id']
            value = self.edit(value, 'item.complete', itemId=item_id)
            if mode == 'delete':
                self.assertEqual(value['items'], [])
            elif mode == 'strike':
                self.assertTrue(value['items'][0]['done'])
                value = self.edit(value, 'item.complete', itemId=item_id)
                self.assertFalse(value['items'][0]['done'])
            else:
                self.assertTrue(value['items'][0]['trashed'])
                value = self.edit(value, 'item.trash', itemId=item_id, trashed=False)
                self.assertFalse(value['items'][0]['trashed'])

    def test_groups_categories_and_order(self):
        self.act('group.create', name='House')
        response = self.act('group.create', name='Shopping')
        groups = response['state']['groups']
        ids = [g['id'] for g in groups if g['name'] in ('House','Shopping')]
        value = self.new(kind='categorized')
        value = self.edit(value, 'list.update', title='Shared', kind='categorized', completion='trash', groups=ids)
        self.assertEqual(len(value['groups']), 2)
        value = self.edit(value, 'category.add', name='Food')
        value = self.edit(value, 'item.add', text='Bread', category='Food')
        value = self.edit(value, 'category.delete', name='Food')
        self.assertEqual(value['items'][0]['category'], '')
        value = self.edit(value, 'list.update', title='Shared', kind='ordered', completion='trash', groups=ids)
        value = self.edit(value, 'item.add', text='Water')
        value = self.edit(value, 'item.move', itemId=value['items'][1]['id'], direction='up')
        self.assertEqual([i['text'] for i in value['items']], ['Water','Bread'])
        response = self.act('group.delete', id=ids[0])
        value = next(l for l in response['state']['lists'] if l['id'] == value['id'])
        self.assertEqual(len(value['groups']), 1)
        value = self.edit(value, 'list.trash', trashed=True)
        self.assertTrue(value['trashed'])
        value = self.edit(value, 'list.trash', trashed=False)
        self.assertFalse(value['trashed'])

    def test_import_roundtrip_and_atomic_invalid_import(self):
        value = self.new(kind='categorized')
        value = self.edit(value, 'category.add', name='Tools')
        value = self.edit(value, 'item.add', text='Hammer', category='Tools')
        value = self.edit(value, 'item.trash', itemId=value['items'][0]['id'], trashed=True)
        payload = {'format':'klop', 'schemaVersion':1, 'groups':[], 'lists':[value]}
        response = self.act('import', data=payload)
        self.assertEqual(response['result']['imported'], 1)
        copy = response['state']['lists'][-1]
        self.assertNotEqual(copy['id'], value['id'])
        self.assertEqual(copy['categories'], ['Tools'])
        self.assertTrue(copy['items'][0]['trashed'])
        before = self.request()[1]
        invalid = json.loads(json.dumps(payload))
        invalid['groups'] = [{'id':'fresh', 'name':'Must roll back'}]
        invalid['lists'].append({'title':'Invalid'})
        self.assertEqual(self.request('/api/action', {'action':'import', 'data':invalid})[0], 400)
        self.assertEqual(self.request()[1], before)

    def test_invalid_requests_and_deletion(self):
        self.assertEqual(self.request('/api/action', {'action':'list.create', 'title':'   ', 'kind':'simple', 'completion':'strike'})[0], 400)
        self.assertEqual(self.request('/api/action', {'action':'import', 'data':[]})[0], 400)
        self.assertEqual(self.request('/api/action', {'action':'list.create'}, nickname='')[0], 400)
        value = self.new()
        response = self.act('list.delete', id=value['id'], version=value['version'])
        self.assertFalse(any(l['id'] == value['id'] for l in response['state']['lists']))


if __name__ == '__main__':
    unittest.main()
