#!/usr/bin/env python3
"""Shared lists. Python standard library only."""
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data'))


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    path = os.environ.get('APP_PATH', '').strip('/')
    if not path:
        secret_file = DATA / 'access-path'
        if not secret_file.exists():
            try:
                with secret_file.open('x') as f:
                    os.chmod(secret_file, 0o600)
                    f.write(secrets.token_urlsafe(24))
            except FileExistsError:
                pass
        path = secret_file.read_text().strip()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{16,128}', path):
        raise ValueError('APP_PATH: нужны 16–128 букв, цифр, дефисов или подчёркиваний')
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS lists (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL)')
    return '/' + path


def connect():
    db = sqlite3.connect(DATA / 'lists.sqlite3', timeout=15)
    db.row_factory = sqlite3.Row
    return db


def uid():
    return str(uuid4())


def string(value, maximum=200, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ValueError('Проверьте текст: он пустой или слишком длинный')
    return value.strip()


def enum(value, choices):
    if value not in choices:
        raise ValueError('Недопустимое значение')
    return value


def boolean(value):
    if type(value) is not bool:
        raise ValueError('Ожидалось логическое значение')
    return value


def array(value, maximum=10000):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('Недопустимый размер списка')
    return value


def mark(value, nickname):
    value['version'] = value.get('version', 0) + 1
    value['updatedAt'] = datetime.now(timezone.utc).isoformat()
    value['updatedBy'] = nickname


def store_list(db, value):
    db.execute('INSERT OR REPLACE INTO lists VALUES (?, ?)', (value['id'], json.dumps(value, ensure_ascii=False)))


def state(db):
    return {'format': 'klop', 'schemaVersion': 1,
            'groups': [dict(row) for row in db.execute('SELECT * FROM groups ORDER BY name')],
            'lists': [json.loads(row['value']) for row in db.execute('SELECT value FROM lists ORDER BY rowid')]}


def import_data(db, payload, nickname):
    if not isinstance(payload, dict) or payload.get('format') != 'klop' or payload.get('schemaVersion') != 1:
        raise ValueError('Нужен JSON, экспортированный из этого приложения (версия 1)')
    group_map = {}
    seen_groups = set()
    for group in array(payload.get('groups'), 500):
        old_id, name = string(group['id']), string(group['name'], 60)
        if old_id in seen_groups:
            raise ValueError('Повторяющийся ID группы')
        seen_groups.add(old_id)
        existing = db.execute('SELECT id FROM groups WHERE name=?', (name,)).fetchone()
        group_map[old_id] = existing['id'] if existing else uid()
        if not existing:
            db.execute('INSERT INTO groups VALUES (?, ?)', (group_map[old_id], name))
    count = 0
    for source in array(payload.get('lists'), 1000):
        categories = [string(x, 100) for x in array(source['categories'], 200)]
        if len(set(categories)) != len(categories):
            raise ValueError('Категории не должны повторяться')
        value = {'id': uid(), 'title': string(source['title']),
                 'kind': enum(source['kind'], ('simple', 'categorized', 'ordered')),
                 'completion': enum(source['completion'], ('strike', 'trash', 'delete')),
                 'groups': list(dict.fromkeys(group_map[string(g)] for g in array(source['groups'], 500))),
                 'categories': categories, 'trashed': boolean(source['trashed']),
                 'createdBy': string(source.get('createdBy', nickname), 40), 'items': []}
        for item in array(source['items']):
            category = string(item.get('category', ''), 100, True)
            if category and category not in categories:
                raise ValueError('Неизвестная категория пункта')
            value['items'].append({'id': uid(), 'text': string(item['text'], 1000),
                                   'category': category, 'done': boolean(item['done']),
                                   'trashed': boolean(item['trashed'])})
        mark(value, nickname)
        store_list(db, value)
        count += 1
    return {'imported': count}


def action(db, data, nickname):
    operation = data['action']
    if operation == 'import':
        return import_data(db, data['data'], nickname)
    if operation in ('group.create', 'group.rename', 'group.delete'):
        if operation == 'group.create':
            db.execute('INSERT INTO groups VALUES (?, ?)', (uid(), string(data['name'], 60)))
        elif operation == 'group.rename':
            db.execute('UPDATE groups SET name=? WHERE id=?', (string(data['name'], 60), string(data['id'])))
        else:
            group_id = string(data['id'])
            db.execute('DELETE FROM groups WHERE id=?', (group_id,))
            for row in db.execute('SELECT value FROM lists').fetchall():
                value = json.loads(row['value'])
                if group_id in value['groups']:
                    value['groups'].remove(group_id)
                    mark(value, nickname)
                    store_list(db, value)
        return {}
    if operation == 'list.create':
        value = {'id': uid(), 'title': string(data['title']),
                 'kind': enum(data['kind'], ('simple', 'categorized', 'ordered')),
                 'completion': enum(data['completion'], ('strike', 'trash', 'delete')),
                 'groups': [], 'categories': [], 'items': [], 'trashed': False, 'createdBy': nickname}
    else:
        row = db.execute('SELECT value FROM lists WHERE id=?', (string(data['id']),)).fetchone()
        if not row:
            raise LookupError('Список уже удалён')
        value = json.loads(row['value'])
        if data.get('version') != value['version']:
            raise RuntimeError('Список изменён на другом устройстве. Данные обновлены — повторите действие.')
        if operation == 'list.delete':
            db.execute('DELETE FROM lists WHERE id=?', (value['id'],))
            return {}
        if operation == 'list.trash':
            value['trashed'] = boolean(data['trashed'])
        elif operation == 'list.update':
            value['title'] = string(data['title'])
            value['kind'] = enum(data['kind'], ('simple', 'categorized', 'ordered'))
            value['completion'] = enum(data['completion'], ('strike', 'trash', 'delete'))
            groups = list(dict.fromkeys(string(g) for g in array(data['groups'], 500)))
            existing = {g['id'] for g in db.execute('SELECT id FROM groups')}
            if not set(groups) <= existing:
                raise ValueError('Группа уже удалена')
            value['groups'] = groups
        elif operation == 'category.add':
            category = string(data['name'], 100)
            if category in value['categories']:
                raise ValueError('Такая категория уже есть')
            value['categories'].append(category)
        elif operation == 'category.delete':
            category = string(data['name'], 100)
            value['categories'].remove(category)
            for item in value['items']:
                if item['category'] == category:
                    item['category'] = ''
        elif operation == 'item.add':
            category = string(data.get('category', ''), 100, True)
            if category and category not in value['categories']:
                raise ValueError('Категория уже удалена')
            value['items'].append({'id': uid(), 'text': string(data['text'], 1000),
                                   'category': category, 'done': False, 'trashed': False})
        elif operation.startswith('item.'):
            item = next((i for i in value['items'] if i['id'] == data['itemId']), None)
            if not item:
                raise LookupError('Пункт уже удалён')
            if operation == 'item.complete':
                if value['completion'] == 'delete':
                    value['items'].remove(item)
                elif value['completion'] == 'trash':
                    item['trashed'] = True
                else:
                    item['done'] = not item['done']
            elif operation == 'item.trash':
                item['trashed'] = boolean(data['trashed'])
            elif operation == 'item.delete':
                value['items'].remove(item)
            elif operation == 'item.update':
                item['text'] = string(data['text'], 1000)
                category = string(data.get('category', item['category']), 100, True)
                if category and category not in value['categories']:
                    raise ValueError('Неизвестная категория')
                item['category'] = category
            elif operation == 'item.move':
                direction = enum(data['direction'], ('up', 'down'))
                visible = [i for i in value['items'] if not i['trashed']]
                position = visible.index(item)
                target = position + (-1 if direction == 'up' else 1)
                if 0 <= target < len(visible):
                    a, b = value['items'].index(item), value['items'].index(visible[target])
                    value['items'][a], value['items'][b] = value['items'][b], value['items'][a]
            else:
                raise ValueError('Неизвестное действие')
        else:
            raise ValueError('Неизвестное действие')
    mark(value, nickname)
    store_list(db, value)
    return {'id': value['id']}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # The secret URL must not appear in request logs.

    def send(self, status, body, mime='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == BASE:
            self.send_response(302)
            self.send_header('Location', BASE + '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        assets = {BASE + '/': ('index.html', 'text/html; charset=utf-8'),
                  BASE + '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  BASE + '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path in assets:
            file, mime = assets[path]
            self.send(200, (ROOT / 'static' / file).read_bytes(), mime)
        elif path == BASE + '/api/health':
            try:
                with connect() as db:
                    db.execute('SELECT id FROM lists LIMIT 1').fetchone()
                self.send(200, {'healthy': True})
            except sqlite3.Error:
                self.send(503, {'healthy': False})
        elif path == BASE + '/api/state':
            with connect() as db:
                self.send(200, state(db))
        else:
            self.send(404, {'error': 'Не найдено'})

    def do_POST(self):
        if urlsplit(self.path).path != BASE + '/api/action':
            return self.send(404, {'error': 'Не найдено'})
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return self.send(403, {'error': 'Недопустимый источник запроса'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': 'Ожидался JSON'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 5 * 1024 * 1024:
                return self.send(413, {'error': 'Максимальный размер JSON — 5 МБ'})
            data = json.loads(self.rfile.read(length))
            nickname = string(unquote(self.headers.get('X-Nickname', '')), 40)
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                result = action(db, data, nickname)
                current = state(db)
            self.send(200, {'result': result, 'state': current})
        except RuntimeError as error:
            self.send(409, {'error': str(error)})
        except LookupError:
            self.send(400, {'error': 'Запись не найдена или JSON содержит неизвестные ссылки'})
        except sqlite3.IntegrityError:
            self.send(400, {'error': 'Группа с таким именем уже есть'})
        except (ValueError, TypeError, AttributeError) as error:
            self.send(400, {'error': str(error) if isinstance(error, ValueError) else 'Некорректные данные'})
        except sqlite3.Error:
            self.send(503, {'error': 'Хранилище временно недоступно. Повторите действие.'})


if __name__ == '__main__':
    BASE = initialize()
    port = int(os.environ.get('PORT', '8080'))
    print(f'Приложение: http://localhost:{port}{BASE}/', flush=True)
    ThreadingHTTPServer((os.environ.get('HOST', '0.0.0.0'), port), Handler).serve_forever()
