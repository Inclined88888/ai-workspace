import runpy
from pathlib import Path

from conftest import SCRIPT, events, tool_call

eval_local = runpy.run_path(str(Path(__file__).resolve().parents[3] / 'scripts/eval_local.py'))


def test_fixed_evaluation_uses_cli_auth_and_real_tools(client, monkeypatch):
    from fastapi.testclient import TestClient
    from app import assistant, main, search

    async def search_stub(query, count=6):
        return [{"title": "Example", "url": "https://example.org", "snippet": "A page"}]

    async def page_stub(url):
        return {"url": url, "text": "A page for evaluation"}

    monkeypatch.setattr(search, "web_search", search_stub)
    monkeypatch.setattr(assistant.TOOL_MAP["read_page"], "fn", page_stub)
    monkeypatch.setattr('app.learn.after_turn', lambda ctx: None)
    token = client.post('/api/cli-token').json()['token']
    anon = TestClient(main.app)
    anon.headers.update({'Authorization': f'Bearer {token}', 'X-Workbench': '1'})
    args = {"web_search": {"query": "docs"}, "read_page": {"url": "https://example.org"},
            "create_task": {"title": "[local eval] Test"}, "get_agenda": {"days": 7}, "search_notes": {"query": "plan"},
            "create_event": {"title": "[local eval] Test", "start": "2030-01-01", "all_day": True},
            "remember": {"content": "[local eval] Test fact"}}
    tasks = eval_local['TASKS']
    assert len(tasks) == 20 and len({t[0] for t in tasks}) == 20
    expected = {prompt: names for _, prompt, names in tasks}

    def send(model, prompt):
        SCRIPT[:] = [*[tool_call(n, args[n]) for n in expected[prompt]], {"role": "assistant", "content": "Summary complete."}]
        return events(anon.post('/api/assistant/chat', json={'message': prompt, 'model': model, 'channel': 'cli'}))

    report = eval_local['evaluate'](f'{client.pid}/fake-model', send)
    assert report['tasks_passed'] == 20
    assert report['tool_calls'] == report['successful_tool_calls'] == 24
    assert report['tool_call_success_rate'] == 1
    assert len({r['conversation_id'] for r in report['tasks']}) == 20


def test_evaluation_does_not_count_claims_missing_results_or_wrong_order():
    score = eval_local['score']
    final = [{'type': 'message', 'content': 'Done'}, {'type': 'done'}]
    assert not score(final, ('remember',))['passed']
    result = lambda n: {'type': 'tool_result', 'name': n, 'ok': True}
    assert not score([result('read_page'), result('web_search'), *final], ('web_search', 'read_page'))['passed']
    assert not score([{'type': 'tool_call', 'name': 'remember'}, *final], ('remember',))['passed']
    report = eval_local['evaluate']('fake', lambda *a: final)
    assert report['task_success_rate'] == report['tool_call_success_rate'] == 0


def test_evaluation_records_failures_and_continues():
    def broken(*args):
        raise OSError('offline')
    report = eval_local['evaluate']('fake', broken)
    assert report['tasks_total'] == 20 and report['tasks_passed'] == 0
    assert all(r['errors'] == ['offline'] for r in report['tasks'])
