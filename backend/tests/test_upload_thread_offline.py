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
from starlette.datastructures import Headers

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
            with mock.patch.object(file.file, 'read', side_effect=OSError('synthetic copy failure')):
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

class _EndlessSource(io.RawIOBase):
    """Fonte sem fim: registra cada pedido de leitura e o total entregue."""

    def __init__(self):
        self.requests, self.delivered = [], 0

    def readable(self):
        return True

    def read(self, size=-1):
        self.requests.append(size)
        if size is None or size < 0:
            raise AssertionError('unbounded read')
        self.delivered += size
        return b'x' * size


class UploadCeilingTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, module, file, directory):
        module.UPLOAD_DIR = Path(directory)
        return await module.upload_file(file, object())

    async def test_exact_limit_is_accepted_and_imported_off_loop(self):
        module = load_upload()
        payload = b'a' * module.MAX_UPLOAD_BYTES
        seen, threads = [], []
        def importer(path, db):
            seen.append(Path(path).stat().st_size)
            threads.append(threading.get_ident())
            return {'importados': 0}
        module.import_clients = importer
        file = UploadFile(file=io.BytesIO(payload), filename='synthetic.csv')
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            result = await self._run(module, file, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(result['status'], 'sucesso')
        self.assertEqual(seen, [module.MAX_UPLOAD_BYTES])
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertTrue(file.file.closed)

    async def test_limit_plus_one_is_413_without_import_echo_or_tempfile(self):
        module = load_upload()
        called = []
        module.import_clients = lambda *args: called.append(True)
        secret = b'Fulana Sigilosa;'
        payload = secret + b'a' * (module.MAX_UPLOAD_BYTES + 1 - len(secret))
        # Content-Length/size mentirosos não importam: o teto vem da leitura.
        file = UploadFile(file=io.BytesIO(payload), filename='synthetic.csv', size=10,
                          headers=Headers({'content-length': '10'}))
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            with self.assertRaises(HTTPException) as caught:
                await self._run(module, file, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(caught.exception.status_code, 413)
        self.assertNotIn('Fulana', str(caught.exception.detail))
        self.assertEqual(called, [])
        self.assertTrue(file.file.closed)

    async def test_reads_are_chunked_and_never_exceed_limit_plus_one(self):
        module = load_upload()
        called = []
        module.import_clients = lambda *args: called.append(True)
        source = _EndlessSource()
        file = UploadFile(file=source, filename='synthetic.csv')
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            with self.assertRaises(HTTPException) as caught:
                await self._run(module, file, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(source.delivered, module.MAX_UPLOAD_BYTES + 1)
        self.assertLessEqual(max(source.requests), module.UPLOAD_CHUNK_BYTES)
        self.assertTrue(all(size > 0 for size in source.requests))
        self.assertEqual(called, [])
        self.assertTrue(file.file.closed)

    async def test_ceiling_does_not_read_content_length(self):
        import ast
        tree = ast.parse((BACKEND / 'app/api/routes/upload.py').read_text(encoding='utf-8'))
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        strings = [n.value.lower() for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertFalse({'headers', 'size', 'copyfileobj', 'readall'} & attributes)
        self.assertFalse([s for s in strings if 'content-length' in s])

    async def test_writers_busy_is_503_retry_after_and_tempfile_removed(self):
        module = load_upload()
        def importer(path, db):
            raise module.AccountWritersBusy()
        module.import_clients = importer
        file = UploadFile(file=io.BytesIO(b'cliente;valor\r\n'), filename='synthetic.csv')
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            with self.assertRaises(HTTPException) as caught:
                await self._run(module, file, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
        exc = caught.exception
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.headers, {'Retry-After': '5'})
        self.assertEqual(exc.detail['code'], 'account_writers_busy')
        self.assertTrue(exc.__suppress_context__)
        self.assertIsNone(exc.__cause__)
        self.assertTrue(file.file.closed)

    async def test_other_failures_keep_their_mapping(self):
        module = load_upload()
        def importer(path, db):
            raise RuntimeError('synthetic database failure')
        module.import_clients = importer
        file = UploadFile(file=io.BytesIO(b'x'), filename='synthetic.csv')
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            with self.assertRaises(RuntimeError):
                await self._run(module, file, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertTrue(file.file.closed)


class UploadAsgiTests(unittest.TestCase):
    def test_asgi_multipart_exact_ok_plus_one_413_busy_503(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        module = load_upload()
        app = FastAPI()
        app.include_router(module.router)
        app.dependency_overrides[module.get_db] = lambda: object()
        outcomes = []
        def importer(path, db):
            if outcomes and outcomes[-1] == 'busy':
                raise module.AccountWritersBusy()
            return {'size': Path(path).stat().st_size}
        module.import_clients = importer
        limit = module.MAX_UPLOAD_BYTES
        with tempfile.TemporaryDirectory(prefix='auneron_synthetic_upload_') as directory:
            module.UPLOAD_DIR = Path(directory)
            client = TestClient(app, raise_server_exceptions=False)
            ok = client.post('/upload/', files={'file': ('s.csv', b'a' * limit, 'text/csv')})
            big = client.post('/upload/', files={'file': ('s.csv', b'a' * (limit + 1), 'text/csv')})
            outcomes.append('busy')
            busy = client.post('/upload/', files={'file': ('s.csv', b'a', 'text/csv')})
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual((ok.status_code, ok.json()['resultado']), (200, {'size': limit}))
        self.assertEqual(big.status_code, 413)
        self.assertLess(len(big.content), 200)
        self.assertEqual(busy.status_code, 503)
        self.assertEqual(busy.headers.get('retry-after'), '5')
        self.assertEqual(busy.json()['detail']['code'], 'account_writers_busy')


if __name__ == '__main__':
    unittest.main()
