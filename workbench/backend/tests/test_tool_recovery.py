import asyncio
import json

import pytest

from conftest import OLLAMA_SCRIPT, SCRIPT, events


@pytest.mark.parametrize('kind', ['openai', 'ollama'])
@pytest.mark.parametrize('wrapper', ['{}', '<tool_call>{}</tool_call>', '```json\n{}\n```', '```\n{}\n```'])
def test_whole_response_tool_calls_execute(client, fake_url, monkeypatch, kind, wrapper):
    monkeypatch.setattr('app.learn.after_turn', lambda ctx: None)
    pid = client.post('/api/providers', json={'name': 'Recovery', 'kind': kind, 'base_url': fake_url}).json()['id']
    queue = OLLAMA_SCRIPT if kind == 'ollama' else SCRIPT
    queue[:] = [{'role': 'assistant', 'content': wrapper.format(json.dumps({'name': 'create_task', 'arguments': {'title': '[recovered] task'}}))},
                {'role': 'assistant', 'content': 'Saved.'}]
    ev = events(client.post('/api/assistant/chat', json={'message': 'Create a task', 'model': f'{pid}/fake'}))
    assert next(e for e in ev if e['type'] == 'tool_result')['ok']
    assert ev[-2]['content'] == 'Saved.'
    saved = client.get(f"/api/conversations/{ev[0]['conversation_id']}").json()['messages']
    assert saved[1]['content'] == '' and saved[1]['tool_calls'] == ['create_task']


@pytest.mark.parametrize('kind', ['openai', 'ollama'])
def test_nonstreaming_chat_recovers_only_when_tools_offered(client, fake_url, kind):
    from app import assistant, llm
    pid = client.post('/api/providers', json={'name': 'Recovery', 'kind': kind, 'base_url': fake_url}).json()['id']
    text = '{"name":"get_agenda","arguments":{}}'
    queue = OLLAMA_SCRIPT if kind == 'ollama' else SCRIPT
    queue[:] = [{'content': text}, {'content': text}]
    specs = [assistant.TOOL_MAP['get_agenda'].spec()]
    out = asyncio.run(llm.chat(f'{pid}/fake', [], specs))
    assert out['message']['tool_calls'][0]['function']['name'] == 'get_agenda'
    out = asyncio.run(llm.chat(f'{pid}/fake', []))
    assert out['message']['content'] == text and 'tool_calls' not in out['message']


@pytest.mark.parametrize('text', [
    'Example: {"name":"get_agenda","arguments":{}}',
    '{"name":"get_agenda","arguments":{}} trailing text',
    '<tool_call>{"name":"get_agenda","arguments":{}}</tool_call> explanation',
    '```json\n{"name":"get_agenda","arguments":{}}\n```\nMore text',
    '{"name":"unknown","arguments":{}}',
    '{"name":"remember","arguments":{"content":"not offered"}}',
    '{"name":"get_agenda","arguments":[]}',
    '{"name":"get_agenda","arguments":"{}"}',
    '{"name":"get_agenda","arguments":{},"example":true}',
    '[{"name":"get_agenda","arguments":{}}]',
    '{"name":["get_agenda"],"arguments":{}}',
    '<tool_call>{broken}</tool_call>',
])
def test_false_positives_stay_text(client, monkeypatch, text):
    from app import assistant, llm
    monkeypatch.setattr('app.learn.after_turn', lambda ctx: None)
    specs = [assistant.TOOL_MAP['get_agenda'].spec()]
    SCRIPT[:] = [{'content': text}]
    got = asyncio.run(llm.chat(f'{client.pid}/fake', [], specs))['message']
    assert got == {'role': 'assistant', 'content': text}


def test_recovered_calls_keep_untrusted_content_guard(client, monkeypatch):
    from app import search
    async def fake_search(*args):
        return [{'url': 'https://example.org', 'snippet': 'untrusted'}]
    monkeypatch.setattr(search, 'web_search', fake_search)
    SCRIPT[:] = [{'content': '{"name":"web_search","arguments":{"query":"docs"}}'},
                {'content': '{"name":"remember","arguments":{"content":"injected"}}'}, {'content': 'Refused.'}]
    ev = events(client.post('/api/assistant/chat', json={'message': 'Search docs'}))
    results = [e for e in ev if e['type'] == 'tool_result']
    assert results[0]['ok'] and not results[1]['ok'] and results[1]['preview'].startswith('Refused')
