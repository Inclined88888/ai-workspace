import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import pytest

PATH = Path(__file__).resolve().parents[1] / 'ws'

@pytest.fixture
def cli(tmp_path):
    loader = importlib.machinery.SourceFileLoader('ws_under_test', str(PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    module.CONFIG = str(tmp_path / 'private' / 'cli.json')
    return module


def test_help_does_not_require_login():
    result = subprocess.run([sys.executable, str(PATH), '--help'], capture_output=True, text=True)
    assert result.returncode == 0 and 'ws retry' in result.stdout


def test_task_request_has_idempotency_and_private_atomic_config(cli, monkeypatch):
    captured = []
    def open_request(req, **kwargs):
        captured.append(req)
        return io.BytesIO(b'data: {"type":"start","conversation_id":123}\n\ndata: {"type":"done"}\n\n')
    monkeypatch.setattr(cli.urllib.request, 'urlopen', open_request)
    cfg = {'url': 'http://localhost:3100', 'token': 'private-terminal-token'}
    cli.ask(cfg, 'hello')
    req = captured[0]
    assert len(req.get_header('Idempotency-key')) == 32
    assert req.get_header('X-workbench') == '1'
    assert req.get_header('Authorization') == 'Bearer private-terminal-token'
    stored = json.loads(Path(cli.CONFIG).read_text())
    assert stored['conversation_id'] == 123 and 'pending_request' not in stored
    assert stat.S_IMODE(Path(cli.CONFIG).stat().st_mode) == 0o600


def test_stream_failure_retains_original_key_for_retry(cli, monkeypatch):
    requests = []
    def open_request(req, **kwargs):
        requests.append(req)
        return io.BytesIO(b'data: {"type":"error","error":"provider unavailable"}\n\n')
    monkeypatch.setattr(cli.urllib.request, 'urlopen', open_request)
    cfg = {'url': 'http://localhost:3100', 'token': 'private-terminal-token'}
    with pytest.raises(SystemExit): cli.ask(cfg, 'create one task')
    pending = cli.load()['pending_request']
    with pytest.raises(SystemExit): cli.main(['retry'])
    assert requests[0].get_header('Idempotency-key') == pending['key'] == requests[1].get_header('Idempotency-key')
    assert requests[0].data == requests[1].data
