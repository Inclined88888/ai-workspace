import pytest

from conftest import CALLS, OLLAMA_CALLS, OLLAMA_SCRIPT, SCRIPT, events


@pytest.fixture
def settings(client, monkeypatch):
    from app import db
    saved = {k: db.get_setting(k) for k in ('tool_mode', 'ollama_num_ctx')}
    monkeypatch.setattr('app.learn.after_turn', lambda ctx: None)
    yield
    for key, value in saved.items():
        if value is None:
            db.run('DELETE FROM settings WHERE key = ?', key)
        else:
            db.set_setting(key, value)


@pytest.mark.parametrize('mode,kind,ctx,compact', [
    ('auto', 'ollama', 16384, True), ('auto', 'ollama', 32767, True),
    ('auto', 'ollama', 32768, False), ('auto', 'openai', 4096, False),
    ('compact', 'openai', 32768, True), ('all', 'ollama', 4096, False),
])
def test_mode_selects_provider_tools(client, fake_url, settings, mode, kind, ctx, compact):
    from app import assistant
    assert client.put('/api/assistant/settings', json={'tool_mode': mode}).json()['tool_mode'] == mode
    client.put('/api/settings', json={'ollama_num_ctx': ctx})
    pid = client.post('/api/providers', json={'name': 'Mode', 'kind': kind, 'base_url': fake_url}).json()['id']
    queue, calls = (OLLAMA_SCRIPT, OLLAMA_CALLS) if kind == 'ollama' else (SCRIPT, CALLS)
    queue[:] = [{'content': 'Hello'}]
    calls.clear()
    ev = events(client.post('/api/assistant/chat', json={'message': 'hello', 'model': f'{pid}/model'}))
    assert ev[-1]['type'] == 'done'
    names = {t['function']['name'] for t in calls[0]['tools']}
    if compact:
        assert names == assistant.COMPACT_TOOLS
    else:
        assert {'create_event', 'read_email', 'schedule_task'} <= names


@pytest.mark.parametrize('prompt,extra', [
    ('Read my NOTES', {'read_note', 'create_note', 'append_to_note'}),
    ('Book a meeting', {'create_event', 'list_events'}),
    ('Reply to an email', {'read_email', 'search_email', 'save_email_draft'}),
    ('Schedule this daily', {'schedule_task', 'list_schedules'}),
    ('Forget that memory', {'forget', 'search_memory'}),
    ('Search previous conversations', {'search_history'}),
    ('Save a skill', {'save_skill'}),
    ('Complete a task', {'list_tasks', 'update_task'}),
    ('Use read_note', {'read_note'}),
])
def test_keywords_add_tools(client, settings, prompt, extra):
    from app import assistant, db
    db.set_setting('tool_mode', 'compact')
    SCRIPT[:] = [{'content': 'ok'}]
    CALLS.clear()
    events(client.post('/api/assistant/chat', json={'message': prompt}))
    names = {t['function']['name'] for t in CALLS[0]['tools']}
    assert names == assistant.COMPACT_TOOLS | extra


def test_compact_preserves_availability_guards(client, settings, monkeypatch):
    from app import assistant, config, db
    db.set_setting('tool_mode', 'compact')
    monkeypatch.setattr(config, 'BROWSER_URL', '')
    ctx = assistant.Ctx('schedule', 0)
    names = {t.name for t in assistant.available_tools(ctx, 'schedule daily browse website delegate team')}
    assert not names & {'schedule_task', 'list_schedules', 'browse_web', 'delegate_task', 'list_team'}
    assert names == assistant.COMPACT_TOOLS
    monkeypatch.setattr(config, 'BROWSER_URL', 'http://browser')
    monkeypatch.setattr(config, 'BROWSER_API_KEY', 'configured')
    ctx = assistant.Ctx('agent', 0, agent={'id': 1})
    names = {t.name for t in assistant.available_tools(ctx, 'delegate team browse')}
    assert {'delegate_task', 'list_team', 'browse_web'} <= names


def test_tool_mode_defaults_and_validation(client, settings):
    from app import db
    db.run("DELETE FROM settings WHERE key = 'tool_mode'")
    assert client.get('/api/assistant/settings').json()['tool_mode'] == 'auto'
    assert client.put('/api/assistant/settings', json={'tool_mode': 'invalid'}).status_code == 422
    assert client.get('/api/assistant/settings').json()['tool_mode'] == 'auto'


def test_compact_keeps_delegation_for_company_agents(client, settings):
    from app import assistant, db
    db.set_setting('tool_mode', 'compact')
    boss = {'id': 1, 'name': 'CEO', 'model': ''}
    ctx = assistant.Ctx(channel='agent', conversation_id=0, agent=boss)
    names = {t.name for t in assistant.available_tools(ctx, 'Write the quarterly plan')}
    assert {'delegate_task', 'list_team'} <= names
    names = {t.name for t in assistant.available_tools(assistant.Ctx(channel='web', conversation_id=0), 'Write the quarterly plan')}
    assert not {'delegate_task', 'list_team'} & names


def test_loading_a_skill_offers_the_tools_it_names(client, settings):
    from conftest import tool_call
    from app import db, learn
    db.set_setting('tool_mode', 'compact')
    learn.upsert_skill('inbox-triage', 'Sort the inbox', '1. search_email for unread\n2. read_email each one\n3. summarise', source='user')
    SCRIPT[:] = [tool_call('load_skill', {'name': 'inbox-triage'}), {'content': 'done'}]
    CALLS.clear()
    events(client.post('/api/assistant/chat', json={'message': 'Do my morning triage'}))
    first, second = [{t['function']['name'] for t in c['tools']} for c in CALLS if c.get('tools')][:2]
    assert not {'search_email', 'read_email'} & first
    assert {'search_email', 'read_email'} <= second
    db.run("DELETE FROM skills WHERE name = 'inbox-triage'")
