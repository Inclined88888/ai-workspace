import asyncio
import json
import threading

import pytest

from conftest import CALLS, OLLAMA_SCRIPT, SCRIPT, events


def chunk(delta, **kw):
    return {"choices": [{"index": 0, "delta": delta, **kw}]}


def test_openai_fragmented_parallel_calls_and_usage(client, monkeypatch):
    monkeypatch.setattr('app.learn.after_turn', lambda ctx: None)
    CALLS.clear()
    SCRIPT[:] = [
        [chunk({"reasoning_content": "Planning"}), chunk({"content": "I will check."}),
         chunk({"tool_calls": [{"index": 1, "id": "b", "function": {"name": "search_", "arguments": '{"query":'}},
                               {"index": 0, "id": "a", "function": {"name": "get_", "arguments": '{"days":'}}]}),
         chunk({"tool_calls": [{"index": 0, "function": {"name": "agenda", "arguments": '7}'}},
                               {"index": 1, "function": {"name": "notes", "arguments": '"plan"}'}}]}),
         chunk({}, finish_reason="tool_calls"), {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 9, "cost": 0.02}}],
        [chunk({"content": "All "}), chunk({"content": "done."}), chunk({}, finish_reason="stop")],
    ]
    ev = events(client.post('/api/assistant/chat', json={'message': 'Check my agenda and notes'}))
    assert [e['content'] for e in ev if e['type'] == 'delta'] == ['I will check.', 'All ', 'done.']
    assert {'type': 'thought', 'content': 'Planning'} in ev
    assert [e['name'] for e in ev if e['type'] == 'tool_call'] == ['get_agenda', 'search_notes']
    assert all(e['ok'] for e in ev if e['type'] == 'tool_result')
    assert ev[-2] == {'type': 'message', 'content': 'All done.'}
    assert ev[-1]['cost_cents'] == 2
    sent = CALLS[1]['messages']
    calls = next(m['tool_calls'] for m in sent if m.get('tool_calls'))
    assert [c['id'] for c in calls] == ['a', 'b']
    assert json.loads(calls[1]['function']['arguments']) == {'query': 'plan'}
    assert [m['tool_call_id'] for m in sent if m['role'] == 'tool'] == ['a', 'b']
    assert all(c['stream'] for c in CALLS)


def test_native_streaming_reasoning_tools_and_final_message(client, fake_url):
    pid = client.post('/api/providers', json={'name': 'Stream', 'kind': 'ollama', 'base_url': fake_url}).json()['id']
    OLLAMA_SCRIPT[:] = [
        [{'message': {'thinking': 'Planning', 'tool_calls': [{'function': {'name': 'get_agenda', 'arguments': {'days': 1}}}]}},
         {'done': True, 'prompt_eval_count': 11, 'eval_count': 3}],
        [{'message': {'content': '<thi'}}, {'message': {'content': 'nk>Hidden</th'}},
         {'message': {'content': 'ink>Hello '}}, {'message': {'content': 'there'}}, {'done': True}],
    ]
    ev = events(client.post('/api/assistant/chat', json={'message': 'Get agenda', 'model': f'{pid}/qwen3:4b'}))
    assert ''.join(e['content'] for e in ev if e['type'] == 'delta') == 'Hello there'
    assert any(e['type'] == 'thought' and e['content'] == 'Planning' for e in ev)
    assert next(e for e in ev if e['type'] == 'tool_result')['ok']
    assert ev[-2] == {'type': 'message', 'content': 'Hello there'}
    saved = client.get(f"/api/conversations/{ev[0]['conversation_id']}").json()['messages']
    assert saved[-1]['content'] == 'Hello there'


def test_delta_arrives_before_provider_finishes(client):
    from app import llm
    gate = threading.Event()
    SCRIPT[:] = [[chunk({'content': 'First'}), {'_gate': gate}, chunk({'content': ' last'}), chunk({}, finish_reason='stop')]]

    async def consume():
        result = []
        async for e in llm.chat_stream(f'{client.pid}/fake-model', [{'role': 'user', 'content': 'hi'}]):
            result.append(e)
            if e.get('delta') == 'First':
                gate.set()
        return result

    got = asyncio.run(consume())
    assert got[0] == {'delta': 'First'} and got[-1]['message']['content'] == 'First last'


@pytest.mark.parametrize('reply', [
    [chunk({'content': 'Partial'})],
    [chunk({'content': 'Partial'}), {'error': {'message': 'generation failed'}}],
])
def test_failed_stream_is_not_saved_or_reported_as_success(client, reply):
    SCRIPT[:] = [reply]
    ev = events(client.post('/api/assistant/chat', json={'message': 'hi'}))
    assert any(e['type'] == 'delta' for e in ev)
    assert ev[-1]['type'] == 'error'
    assert not any(e['type'] in ('message', 'done') for e in ev)
    saved = client.get(f"/api/conversations/{ev[0]['conversation_id']}").json()['messages']
    assert not any(m['role'] == 'assistant' for m in saved)


def test_ask_keeps_final_answer_contract(client):
    from app import assistant, db
    SCRIPT[:] = [[chunk({'content': 'Hi '}), chunk({'content': 'there'}), chunk({}, finish_reason='stop')]]
    cid = assistant.new_conversation('telegram')
    final, ev = asyncio.run(assistant.ask(assistant.Ctx('telegram', cid), 'hi'))
    assert final == 'Hi there' and ev[-1]['type'] == 'done'
    db.run('DELETE FROM conversations WHERE id = ?', cid)
