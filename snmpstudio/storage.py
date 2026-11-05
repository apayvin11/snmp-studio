# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import tempfile
import os
from .models import Configuration

MAX_CONFIG = 8 * 1024 * 1024


def atomic_write(path, text, expected=None):
    path = Path(path)
    if expected is not None and path.exists() and path.read_bytes() != expected:
        raise RuntimeError('File changed on disk. Reload it or use Save As.')
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Store:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), json TEXT NOT NULL)')
        self.db.commit()

    def load(self):
        row = self.db.execute('SELECT json FROM config WHERE id=1').fetchone()
        return Configuration.from_dict(json.loads(row[0])) if row else Configuration()

    def save(self, config):
        config.validate()
        with self.db:
            self.db.execute('INSERT INTO config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET json=excluded.json', (json.dumps(config.to_dict(), ensure_ascii=False),))

    def export(self, config, path):
        config.validate()
        atomic_write(path, json.dumps(config.to_dict(), indent=2, ensure_ascii=False)+'\n')

    @staticmethod
    def read_import(path):
        if Path(path).stat().st_size > MAX_CONFIG:
            raise ValueError('Configuration file exceeds 8 MiB')
        return Configuration.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))

    def backup(self, path):
        with sqlite3.connect(str(path)) as target:
            self.db.backup(target)

    def close(self):
        self.db.close()


class Secrets:
    """Secrets are held in memory, optionally persisted by a system keyring."""
    def __init__(self):
        self.session = {}

    def get(self, device, name):
        key = f'{device}:{name}'
        if key in self.session:
            return self.session[key]
        try:
            import keyring
            value = keyring.get_password('SNMPStudio', key) or ''
        except Exception:
            value = ''
        self.session[key] = value
        return value

    def set(self, device, name, value, persist=False):
        key = f'{device}:{name}'
        self.session[key] = value
        try:
            import keyring
            if persist:
                keyring.set_password('SNMPStudio', key, value)
            else:
                # Clear an older persisted value when switching to session-only.
                try:
                    keyring.delete_password('SNMPStudio', key)
                except keyring.errors.PasswordDeleteError:
                    pass
            return True
        except Exception:
            return not persist
