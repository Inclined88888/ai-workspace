"""Persisted outbox drafts deserve receipts; claimed IDs alone do not."""


def test_saved_outbox_draft_is_verified(client):
    from app import receipts
    from app.teammates import outbox
    d = outbox.draft('email', 'sam@example.com', 'Ten minutes late', 'Call')
    args = {'channel': 'email', 'to': 'sam@example.com', 'body': 'Ten minutes late'}
    ok, evidence = receipts.verify('draft_message', args, {'draft_id': d['id']})
    assert ok is True
    assert 'not sent' in evidence
    assert receipts.unbacked_claim('I created the draft.', [{'verified': ok, 'effect': 'write', 'tool': 'draft_message'}])[0] == ''


def test_missing_or_wrong_recipient_draft_not_verified(client):
    from app import receipts
    from app.teammates import outbox
    d = outbox.draft('email', 'other@example.com', 'Wrong recipient')
    args = {'channel': 'email', 'to': 'sam@example.com', 'body': 'Ten minutes late'}
    assert receipts.verify('draft_message', args, {'draft_id': -123})[0] is False
    assert receipts.verify('draft_message', args, {'draft_id': d['id']})[0] is False
