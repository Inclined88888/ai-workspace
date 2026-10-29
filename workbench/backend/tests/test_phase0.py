"""Phase 0 acceptance: persisted credentials and authenticated discovery."""
import json
from fastapi.testclient import TestClient


def test_persisted_credentials_are_encrypted_and_absent_from_responses(client, monkeypatch):
    from app import db, mail, security
    secret = 'phase0-secret-unique-credential'
    provider = client.post('/api/providers', json={'name': 'Encrypted', 'kind': 'openai',
        'base_url': 'https://api.openai.com/v1', 'api_key': secret}).json()
    calendar = client.post('/api/calendars', json={'name': 'Encrypted', 'kind': 'local', 'password': secret}).json()
    monkeypatch.setattr(mail, 'test', lambda row: None)
    account = client.post('/api/mail/accounts', json={'name': 'Encrypted', 'email': 'owner@example.com',
        'imap_host': 'imap.example.com', 'smtp_host': 'smtp.example.com', 'username': 'owner', 'password': secret}).json()
    client.put('/api/assistant/settings', json={'brave_api_key': secret})
    try:
        values = [db.one('SELECT secret_enc FROM connections WHERE id=?', provider['id'])['secret_enc'],
                  db.one('SELECT password_enc FROM calendars WHERE id=?', calendar['id'])['password_enc'],
                  db.one('SELECT password_enc FROM mail_accounts WHERE id=?', account['id'])['password_enc'],
                  db.get_setting('secret:BRAVE_API_KEY')]
        assert all(value != secret and security.decrypt(value) == secret for value in values)
        for path in ['/api/providers', '/api/calendars', '/api/mail/accounts', '/api/assistant/settings']:
            assert secret not in client.get(path).text
        assert security.encrypt(secret) != security.encrypt(secret)
        corrupted = values[0][:-5] + 'xxxxx'
        assert security.decrypt(corrupted) == ''
    finally:
        client.delete(f"/api/providers/{provider['id']}")
        client.delete(f"/api/calendars/{calendar['id']}")
        client.delete(f"/api/mail/accounts/{account['id']}")
        client.put('/api/assistant/settings', json={'brave_api_key': ''})


def test_api_discovery_auth_and_declared_security(client):
    from app import db, main
    anonymous = TestClient(main.app)
    assert anonymous.get('/api/openapi.json').status_code == 401
    response = client.get('/api/openapi.json')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    spec = response.json()
    assert spec['openapi'].startswith('3.')
    paths = spec['paths']
    assert paths['/api/status']['get']['security'] == []
    assert paths['/api/tasks']['post']['security'] == [{'SessionCookie': []}]
    assert paths['/api/assistant/chat']['post']['security'] == [{'SessionCookie': []}, {'TerminalToken': []}]
    token = client.post('/api/cli-token').json()['token']
    assert anonymous.get('/api/openapi.json', headers={'Authorization': f'Bearer {token}'}).status_code == 200
    assert token not in json.dumps(spec)
    assert db.get_setting('cli_token_hash') != token
    db.set_setting('cli_token_hash', '')
