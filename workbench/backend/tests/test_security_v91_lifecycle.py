"""Privacy gates across capture awaits and request cancellation."""
import asyncio
import json
import time

import pytest
from fastapi import HTTPException
from test_computer import paired  # noqa: F401


@pytest.mark.parametrize('url, expected', [
    ('https://api.example.com/v1', False), ('https://8.8.8.8/v1', False),
    ('http://127.0.0.1:11434/v1', True), ('http://192.168.1.2:11434/v1', True),
])
def test_ollama_protocol_does_not_bypass_local_only(monkeypatch, url, expected):
    from app import llm
    monkeypatch.setattr(llm, 'split_ref', lambda ref: ({'kind': 'ollama', 'base_url': url}, 'test'))
    assert llm.is_local('test/model') is expected
    assert llm.is_free({'kind': 'ollama', 'base_url': url}, 'test') is expected


@pytest.mark.parametrize('change', ['off', 'local_only', 'model', 'project'])
def test_capture_rechecks_privacy(client, monkeypatch, change):
    from app import computer, vision
    state = dict(model='1/cloud', mode='session', local_only=False,
                 check={'ok': True}, consent={'model': '1/cloud'}, session_until='9999')
    project = [None]
    monkeypatch.setattr(vision, 'settings', lambda: dict(state))
    monkeypatch.setattr(vision, '_project_id', lambda cid, mid: project[0])
    monkeypatch.setattr(vision, '_local_project', lambda pid: pid == 42)
    monkeypatch.setattr(vision, 'destination', lambda ref: dict(local=False, label='test cloud'))
    monkeypatch.setattr(computer, 'online_device', lambda: {'id': 1})
    sent = []

    async def capture(*args, **kwargs):
        if change == 'project':
            project[0] = 42
        else:
            key, value = {'off': ('mode', 'off'), 'local_only': ('local_only', True),
                          'model': ('model', '2/other')}[change]
            state[key] = value
        return {'ok': True, 'result': {'image': 'PRIVATE-SCREEN'}}

    async def ask(*args):
        sent.append(args)
        return '{}'

    monkeypatch.setattr(computer, 'request', capture)
    monkeypatch.setattr(vision, '_ask', ask)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(vision.locate('Notes', 'Save', 1, None))
    assert exc.value.status_code == 403
    assert sent == []


@pytest.mark.parametrize('dispatched', [False, True])
def test_cancelled_request_cleans_content(client, paired, monkeypatch, dispatched):
    from app import computer, db
    monkeypatch.setattr(computer, 'online_device', lambda: db.one('SELECT * FROM computer_devices WHERE revoked = 0'))

    async def scenario():
        task = asyncio.create_task(computer.request('capture', {'app': 'Notes'}))
        await asyncio.sleep(0)
        aid = max(computer._events)
        if dispatched:
            db.run("UPDATE computer_actions SET status = 'dispatched' WHERE id = ?", aid)
        computer._transient_results[aid] = {'image': 'PRIVATE-SCREEN'}
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert aid not in computer._pending_args
        assert aid not in computer._events
        assert aid not in computer._transient_results
        row = db.one('SELECT status, result FROM computer_actions WHERE id = ?', aid)
        assert row['status'] == ('uncertain' if dispatched else 'cancelled')
        assert not row['result']

    asyncio.run(scenario())


def test_mission_target_scope(client, monkeypatch):
    from app import computer, vision
    monkeypatch.setattr(computer, 'online_device', lambda: {'id': 1})
    monkeypatch.setattr(vision, '_targets', {'test': dict(expires=time.time() + 60,
                       conversation_id=4, mission_id=7, device_id=1)})
    assert vision.target('test', 4, 7)
    with pytest.raises(HTTPException) as exc:
        vision.target('test', 4, None)
    assert exc.value.status_code == 403


@pytest.mark.parametrize('confidence', ['NaN', 'Infinity', '1.5'])
def test_invalid_confidence_creates_no_target(client, monkeypatch, confidence):
    from app import computer, vision
    monkeypatch.setattr(vision, 'usable', lambda pid=None: (True, ''))
    monkeypatch.setattr(vision, 'settings', lambda: {'model': 'test'})
    monkeypatch.setattr(vision, 'destination', lambda ref: dict(local=True, label='test local'))
    monkeypatch.setattr(computer, 'online_device', lambda: {'id': 1})

    async def capture(*args, **kwargs):
        return {'ok': True, 'result': {'image': 'test'}}

    async def ask(*args):
        return json.dumps(dict(found=True, x=.5, y=.5, confidence=confidence))

    monkeypatch.setattr(computer, 'request', capture)
    monkeypatch.setattr(vision, '_ask', ask)
    assert asyncio.run(vision.locate('Notes', 'Save', None, None))['found'] is False
