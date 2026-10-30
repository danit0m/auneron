"""Standalone synthetic upload tests: no application, DB, secrets or network."""
import asyncio
import importlib.util
import io
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock
from fastapi import HTTPException, UploadFile

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

def load_upload():
    database = types.ModuleType('app.database.database')
    database.get_db = lambda: None
    service = types.ModuleType('app.services.import_service')
    service.import_clients = lambda *args: None
    spec = importlib.util.spec_from_file_location('synthetic_legacy_upload', BACKEND / 'app/api/routes/upload.py')
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {'app.database.database': database, 'app.services.import_service': service}):
        with mock.patch('pathlib.Path.mkdir'):
            spec.loader.exec_module(module)
    return module

class UploadThreadTests(unittest.IsolatedAsyncioTestCase):
    async def test_copy_and_import_run_off_event_loop_and_remove_owned_tempfile(self):
        module = load_upload()
        payload = b'cliente;valor;vencimento;status\r\nTeste Sintetico;1,00;2026-01-01;aberto'
        file = UploadFile(file=io.BytesIO(payload), filename='synthetic.csv')
        entered, release = threading.Event(), threading.Event()
        imported, threads = [], []
        def importer(path, db):
            imported.append(Path(path))
            threads.append(threading.get_ident())
            self.assertEqual(Path(path).read_bytes(), payload)
            entered.set()
            if not release.wait(.75):
                raise ValueError('event loop failed to release worker')
            return {'importados': 1}
        module.import_clients = importer
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            module.UPLOAD_DIR = Path(directory)
            task = asyncio.create_task(module.upload_file(file, object()))
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(.002)
                self.assertTrue(entered.is_set())
                self.assertFalse(task.done(), 'Blocking importer must not run on event loop')
                release.set()
                result = await asyncio.wait_for(task, 1)
                self.assertEqual(result['resultado'], {'importados': 1})
                self.assertNotEqual(threads[0], threading.get_ident())
                self.assertFalse(imported[0].exists())
                self.assertEqual(list(Path(directory).iterdir()), [])
            finally:
                release.set()
                if not task.done():
                    await task
        self.assertTrue(file.file.closed)

    async def test_import_failure_closes_input_and_removes_only_owned_tempfile(self):
        module = load_upload()
        def importer(*args):
            raise ValueError('synthetic validation failure')
        module.import_clients = importer
        file = UploadFile(file=io.BytesIO(b'synthetic'), filename='synthetic.csv')
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            module.UPLOAD_DIR = Path(directory)
            sentinel = Path(directory) / 'unrelated.txt'
            sentinel.write_text('keep', encoding='utf-8')
            with self.assertRaises(HTTPException) as caught:
                await module.upload_file(file, object())
            self.assertEqual(caught.exception.status_code, 400)
            self.assertEqual(list(Path(directory).iterdir()), [sentinel])
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        self.assertTrue(file.file.closed)

    async def test_copy_failure_also_removes_owned_tempfile(self):
        module = load_upload()
        file = UploadFile(file=io.BytesIO(b'synthetic'), filename='synthetic.csv')
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            module.UPLOAD_DIR = Path(directory)
            with mock.patch.object(module.shutil, 'copyfileobj', side_effect=OSError('synthetic copy failure')):
                with self.assertRaises(OSError):
                    await module.upload_file(file, object())
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertTrue(file.file.closed)

    async def test_invalid_extension_does_not_import(self):
        module = load_upload()
        called = []
        module.import_clients = lambda *args: called.append(True)
        file = UploadFile(file=io.BytesIO(b'synthetic'), filename='synthetic.exe')
        with self.assertRaises(HTTPException) as caught:
            await module.upload_file(file, object())
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(called, [])
        self.assertTrue(file.file.closed)

if __name__ == '__main__':
    unittest.main()
