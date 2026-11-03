#!/usr/bin/env python3
"""Executable Part A baseline, no providers or production-data writes.

Run with /Users/azad_mavi/.venvs/aiw/bin/python scripts/audit/check_core.py
  --output docs/audit/core-baseline.json
Verdicts concern each explicit evidence.claim, not an unspecified audit blanket.
UNKNOWN is a real result, never substituted with a guessed inventory. Scripted
model replies test orchestration only, not inference quality. No app lifespan is
started: background sync, catalog refresh and keychain migration stay disabled.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = Path('/Users/azad_mavi/.hermes/cache/scratch')
FINDINGS = (1, 2, 4, 5, 7)
COMMAND = f'{sys.executable} scripts/audit/check_core.py'


def site(obj):
    lines, first = inspect.getsourcelines(obj)
    return {'callable': f'{obj.__module__}.{obj.__qualname__}',
            'path': str(Path(inspect.getsourcefile(obj)).relative_to(ROOT)),
            'line': first, 'end_line': first + len(lines) - 1}


def hits(path, pattern):
    return [{'path': path, 'line': n, 'text': line.strip()}
            for n, line in enumerate((ROOT / path).read_text().splitlines(), 1)
            if re.search(pattern, line)]


class QuietUI:
    color = False
    def ask(self, *_):
        return 'y'
    def __getattr__(self, name):
        return lambda *a, **kw: None


class ScriptedModel:
    model = 'audit-scripted'
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
    def complete(self, system, messages, tools, on_text=None):
        self.calls.append({'messages': json.loads(json.dumps(messages)),
                           'tools': [t['name'] for t in tools]})
        return self.replies.pop(0)


def terminal_agent(folder, replies=(), learning=False):
    from rooks_code.agent import Agent
    from rooks_code.config import Settings
    folder.mkdir(parents=True, exist_ok=True)
    (folder / '.git').mkdir(exist_ok=True)  # bound instruction discovery
    s = Settings(root=folder, provider='workbench', model='audit-scripted',
                 base_url='http://127.0.0.1:9/v1', key='audit-dummy-not-a-provider-key',
                 max_usd=None, local=True, auto_approve=True, stream=False,
                 learn=learning, nudge=False)
    return Agent(s, QuietUI(), model=ScriptedModel(replies))


def backend():
    from app import db
    db.init()  # schema only in isolated DATA_DIR, not a production migration
    from app import compression
    compression.init_compression_table()
    compression.init_summary_table()
    from app.main import app
    from app import companion
    companion._published = True  # no companion-discovery file in user's home
    return app


def check_1(work):
    from rooks_code.agent import Agent
    from rooks_code.providers import Reply
    from app import assistant, llm
    import rooks_code.cli as cli
    import app.assistant_api as api
    a = terminal_agent(work / 'cli-one', [Reply('AUDIT_CLI_OK', [], 1, 1)])
    try:
        answer = a.run('Reply with AUDIT_CLI_OK')
        cli_calls = len(a.model.calls)
    finally:
        a.close()
    seen = []
    async def fake_chat(*args, **kw):
        seen.append('backend-model-boundary')
        return {'message': {'role': 'assistant', 'content': 'AUDIT_WEB_OK'}, 'usd': 0}
    async def consume():
        cid = assistant.new_conversation('web')
        with patch.object(llm, 'chat', fake_chat):
            events = [ev async for ev in assistant.run_turn(assistant.Ctx('web', cid), 'Reply with AUDIT_WEB_OK', 'audit-scripted')]
        return events
    events = asyncio.run(consume())
    assert answer == 'AUDIT_CLI_OK' and cli_calls == 1
    assert seen and any(ev.get('content') == 'AUDIT_WEB_OK' for ev in events)
    cli_tree = ast.parse(inspect.getsource(cli))
    imports = [n for n in ast.walk(cli_tree) if isinstance(n, ast.ImportFrom) and n.module == 'agent']
    api_source = inspect.getsource(api.chat)
    assert imports and 'assistant.run_turn(' in api_source
    assert Agent.run.__module__ != assistant.run_turn.__module__
    return 'PROVEN', {'claim': 'The rooks terminal and Workbench chat have distinct executable agent-loop implementations.',
        'entrypoints': [site(Agent.run), site(assistant.run_turn), site(assistant._run_turn)],
        'dispatch': hits('rooks-code/rooks_code/cli.py', r'from \.agent import Agent|agent = Agent|agent\.run\(')
                    + hits('workbench/backend/app/assistant_api.py', r'assistant\.run_turn'),
        'executed': {'cli_answer': answer, 'cli_model_calls': cli_calls, 'web_model_calls': len(seen), 'web_events': events},
        'limits': 'Model boundaries scripted in-process; proves separate orchestration, not provider behavior or that code is wholly duplicated.'}


def check_2(work):
    from app import assistant
    a = terminal_agent(work / 'inventory', learning=True)
    try:
        cli = sorted(t['name'] for t in a.tool_specs())
        sub = terminal_agent(work / 'sub-inventory')
        try:
            sub.sub = True
            sub_names = sorted(t['name'] for t in sub.tool_specs())
        finally:
            sub.close()
        web_tools = assistant.available_tools(assistant.Ctx('web', assistant.new_conversation('web')))
        web = sorted(t.name for t in web_tools)
        assert all(callable(t.fn) for t in web_tools)
        assert len(cli) == len(set(cli)) and len(web) == len(set(web))
        only_cli, only_web = sorted(set(cli) - set(web)), sorted(set(web) - set(cli))
        assert only_cli or only_web
        return 'PROVEN', {'claim': 'Actual offered terminal and web tool registries differ in a fresh unconfigured workspace.',
            'registries': [site(a.tool_specs), site(assistant.available_tools)],
            'terminal': {'count': len(cli), 'names': cli}, 'web': {'count': len(web), 'names': web},
            'terminal_subagent': sub_names, 'terminal_only': only_cli, 'web_only': only_web,
            'web_callable_implementations': [site(t.fn) for t in web_tools],
            'limits': 'No configured MCP servers, browser/desk services, provider keys or user-installed packs; differences are names, not proof of semantically missing capabilities. Terminal inventory includes actual Learning.specs().'}
    finally:
        a.close()


def check_4(work):
    from rooks_code import extras
    from rooks_code.providers import Reply, ToolCall
    a = terminal_agent(work / 'parallel')
    starts = []
    t0 = time.monotonic()
    try:
        for n in (1, 2):
            filename = a.root / f'interval-{n}.json'
            code = ('import json,time,os,sys; s=time.monotonic(); time.sleep(4); '
                    f'json.dump({{"start":s,"end":time.monotonic(),"pid":os.getpid(),"stdin_isatty":sys.stdin.isatty()}},open({str(filename)!r},"w"))')
            command = shlex.join([sys.executable, '-c', code])
            # Use registered tool preparation and apply, not independent harness Popen.
            starts.append(a.extras.prepare('start_background', {'command': command}).apply()[0])
        for row in a.extras.jobs.jobs.values():
            row['proc'].wait(timeout=12)
        intervals = [json.loads((a.root / f'interval-{n}.json').read_text()) for n in (1, 2)]
        overlap = min(x['end'] for x in intervals) - max(x['start'] for x in intervals)
        statuses = a.extras.prepare('check_background', {}).apply()[0]
        codes = [r['proc'].returncode for r in a.extras.jobs.jobs.values()]
        assert overlap > 0 and codes == [0, 0]
        task_plan = a.extras.prepare('task', {'goal': 'Reply with AUDIT_SUB_OK'})
        from rooks_code.providers import Reply
        a.model = ScriptedModel([Reply('AUDIT_SUB_OK', [], 1, 1)])
        sub_result = task_plan.apply()[0]
        assert sub_result == 'AUDIT_SUB_OK'
        sub_names = a.model.calls[0]['tools']
        return 'REFUTED', {'claim': 'The terminal cannot execute two background shell tasks concurrently (and has no subagent task tool).',
            'background_launches': starts, 'intervals': intervals, 'overlap_seconds': overlap,
            'elapsed_seconds': time.monotonic() - t0, 'exit_codes': codes, 'status': statuses,
            'jobs_impl': site(extras.Jobs.start), 'subagent_impl': site(extras.Extras.subagent),
            'subagent_executed': {'answer': sub_result, 'offered_tools': sub_names},
            'jobs_slash_dispatch': hits('rooks-code/rooks_code/cli.py', r'/jobs|extras.jobs'),
            'limits': 'Jobs are shell processes (stdin DEVNULL), usable for servers/watchers/tests, not model subagents or interactive PTYs. task creates a real nested Agent with scripted model and read-only registry; Extras.subagent synchronously calls sub.run. Concurrent model subagents were not demonstrated.'}
    finally:
        a.close()


def check_5(work):
    from fastapi.testclient import TestClient
    from rooks_code.learning import Learning
    from app import config, db, learn
    from app.main import app
    key = 'audit-bridge-' + 'x' * 32
    token = 'audit-api-' + 'y' * 32
    db.set_setting('cli_token_hash', hashlib.sha256(token.encode()).hexdigest())
    recorded, responses = [], []
    async def fake_after(cid, tool_calls, channel):
        recorded.append({'conversation_id': cid, 'tool_calls': tool_calls, 'channel': channel})
    client = TestClient(app)  # deliberately no lifespan / outbound loops
    a = terminal_agent(work / 'continuity', learning=True)
    session = a.learning.session
    def transport(method, path, body=None):
        response = client.request(method, '/v1/rooks' + path, json=body,
                                  headers={'Authorization': f'Bearer {key}'})
        response.raise_for_status()
        responses.append({'status': response.status_code, 'body': response.json()})
        return response.json()
    messages = [{'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'audit-call', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"marker.txt"}'}}]},
        {'role': 'tool', 'tool_call_id': 'audit-call', 'content': 'AUDIT_MARKER'}]
    try:
        with patch.object(config, 'BROWSER_API_KEY', key), patch.object(learn, 'enabled', return_value=True), \
             patch.object(learn, '_after', fake_after), patch.object(a.learning, '_req', transport):
            for prompt in ('audit first prompt', 'audit second prompt'):
                thread = a.learning.after(prompt, 'audit persisted answer', messages, wait=True)
                assert thread and not thread.is_alive(), 'Learning.after did not finish'
        assert len(responses) == 2, 'Learning.after swallowed an error; inspect transport result'
        cid = responses[0]['body']['conversation_id']
        assert responses[1]['body']['conversation_id'] == cid
        conv = dict(db.one('SELECT * FROM conversations WHERE id = ?', cid))
        rows = [dict(x) for x in db.all('SELECT role,name,content FROM messages WHERE conversation_id = ? ORDER BY id', cid)]
        headers = {'Authorization': f'Bearer {token}', 'X-Workbench': '1'}
        listed = client.get('/api/conversations', headers=headers)
        lookup = client.get(f'/api/conversations/{cid}', headers=headers)
        assert listed.status_code == lookup.status_code == 200
        assert cid in [c['id'] for c in listed.json()]
        assert conv['external_id'] == session and conv['channel'] == 'cli'
        assert len(rows) == 8 and len(recorded) == 2
        return 'REFUTED', {'claim': 'Completed terminal turns do not persist into a session-linked Workbench conversation visible by ID.',
            'session': session, 'transport_responses': responses, 'conversation': conv,
            'persisted_messages': rows, 'list_status': listed.status_code,
            'lookup': {'status': lookup.status_code, 'body': lookup.json()}, 'learning_hook_calls': recorded,
            'entrypoints': [site(Learning.after)],
            'limits': 'Real Learning.after thread -> TestClient transport adapter replacing only urllib I/O -> actual authenticated /v1/rooks/turn -> isolated SQLite -> actual authenticated conversation lookup. Learning mining hook scripted (no provider keys). Persistence happens after completion; live shared session or cross-interface agent resume is not established.'}
    finally:
        a.close()
        client.close()


def check_7(work):
    from app import plan_signin
    result = plan_signin._run([sys.executable, '-c', 'import sys; print("AUDIT_APP_CHILD_OK"); print("isatty="+str(sys.stdin.isatty()))'], timeout=5)
    assert result.returncode == 0 and 'AUDIT_APP_CHILD_OK' in result.stdout
    # Exercise the existing app PTY spawner with a harmless local child instead
    # of a vendor sign-in. Only post-exit credential discovery is replaced.
    pty_probe = None
    if sys.platform != 'win32':
        code = ('import sys; print("AUDIT_PTY_READY", flush=True); '
                'print("isatty="+str(sys.stdin.isatty()), flush=True); '
                'print("echo="+input(), flush=True)')
        with patch.object(plan_signin.SignIn, '_finish', lambda obj: setattr(obj, 'status', 'audit-finished')):
            session = plan_signin.SignIn('audit-local-child', [sys.executable, '-c', code])
            try:
                deadline = time.monotonic() + 5
                while 'AUDIT_PTY_READY' not in session.text and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert 'AUDIT_PTY_READY' in session.text, session.text
                os.write(session.fd, b'audit-input\n')
                session.proc.wait(timeout=5)
                while session.status != 'audit-finished' and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert 'isatty=True' in session.text and 'echo=audit-input' in session.text, session.text
                pty_probe = {'entrypoint': site(plan_signin.SignIn.__init__), 'exit_code': session.proc.returncode,
                             'output': session.text, 'stdin_write': 'audit-input newline',
                             'replacement': 'Only _finish credential discovery disabled; real openpty/Popen/reader exercised.'}
            finally:
                session.stop()
                session.proc.wait(timeout=5)
                if session.fd is not None:
                    os.close(session.fd)
    rust_path = 'desktop/src-tauri/src/lib.rs'
    lib = (ROOT / rust_path).read_text()
    registered = re.search(r'tauri::generate_handler!\[(.*?)\]\)', lib, re.S)
    assert registered, 'Tauri command registry not found'
    commands = re.findall(r'\b(?:commands|sidecars|approvals|anywhere|native)::\w+', registered.group(1))
    files = sorted((ROOT / 'desktop/src-tauri/src').glob('*.rs'))
    interactive_tokens = []
    for path in files:
        interactive_tokens += hits(str(path.relative_to(ROOT)), r'(?i)openpty|forkpty|portable.pty|terminal\.app|iterm|spawn_terminal|open_terminal')
    return 'UNKNOWN', {'claim': 'The desktop app lacks a user-accessible interactive terminal/shell launch capability.',
        'registered_tauri_commands': commands, 'rust_source_files_inspected': [str(p.relative_to(ROOT)) for p in files],
        'interactive_terminal_token_hits': interactive_tokens,
        'actual_app_child_process_probe': {'entrypoint': site(plan_signin._run), 'argv': [sys.executable, '-c', 'print marker and stdin isatty'],
                                          'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr},
        'actual_app_pty_probe': pty_probe,
        'native_spawn_sites': hits('desktop/src-tauri/src/sidecars.rs', r'app.shell\(\).command|\.sidecar\(|\.spawn\('),
        'backend_pty_sites': hits('workbench/backend/app/plan_signin.py', r'pty|Popen|master|slave'),
        'cli_agent_spawn_sites': hits('workbench/backend/app/cli_agents.py', r'create_subprocess_exec|stdin=|stdout=|stderr='),
        'limits': 'App subprocess spawn is executed and works; Tauri sidecar spawn and plan sign-in PTY are present in source, so blanket no-spawn claims are false. Sign-in PTY is purpose-specific, not a general interactive terminal. No desktop UI execution/compiled Tauri command invocation performed; a missing token/command is insufficient to prove absence through every path.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--finding', type=int, choices=FINDINGS)
    args = parser.parse_args()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aiw-core-audit-', dir=SCRATCH) as tmp:
        work = Path(tmp)
        for name in ('home', 'data', 'config', 'cache', 'tmp'):
            (work / name).mkdir()
        # Do not inherit provider credentials, saved app config or user data.
        for key in list(os.environ):
            if any(word in key.upper() for word in ('API_KEY', 'TOKEN', 'SECRET', 'PASSWORD')) or key.startswith(('ROOKS_', 'WORKBENCH_')):
                os.environ.pop(key, None)
        os.environ.update(HOME=str(work / 'home'), DATA_DIR=str(work / 'data'),
            TMPDIR=str(work / 'tmp'), XDG_CONFIG_HOME=str(work / 'config'), XDG_CACHE_HOME=str(work / 'cache'),
            APP_SECRET='audit-' + 'a' * 40, TOOLS_API_KEY='audit-' + 'b' * 40,
            WORKBENCH_SECRET_BACKEND='fernet', STATIC_DIR=str(work / 'no-static'),
            BROWSER_URL='', DESK_URL='', OLLAMA_URL='', SEARXNG_URL='')
        sys.path[:0] = [str(ROOT / 'rooks-code'), str(ROOT / 'workbench/backend')]
        # Fail closed on unexpected network. TestClient is in-process; Jobs local.
        original_connect = socket.socket.connect
        def deny_connect(sock, address):
            raise RuntimeError(f'Audit forbids socket connection: {address!r}')
        socket.socket.connect = deny_connect
        capture = io.StringIO()
        setup_error = None
        with contextlib.redirect_stdout(capture):
            try:
                backend()
            except Exception:
                setup_error = traceback.format_exc()
            results = []
            for number in ((args.finding,) if args.finding else FINDINGS):
                try:
                    if setup_error and number in (1, 2, 5, 7):
                        raise RuntimeError('Backend import/init blocked: ' + setup_error)
                    verdict, evidence = globals()[f'check_{number}'](work)
                except Exception as exc:
                    verdict, evidence = 'UNKNOWN', {'blocked_reason': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc()}
                results.append({'finding': number, 'verdict': verdict, 'evidence': evidence,
                                'command': COMMAND + f' --finding {number}'})
        socket.socket.connect = original_connect
        payload = {'schema_version': 1, 'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   'isolation': {'temporary_root': tmp, 'cleanup': 'TemporaryDirectory removed after checks', 'network': 'socket.connect denied',
                                 'real_provider_calls': False, 'app_lifespan_started': False},
                   'results': results, 'captured_stdout': capture.getvalue()}
    text = json.dumps(payload, indent=2, default=str) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
