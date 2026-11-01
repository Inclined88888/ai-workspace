def test_local_date_query_includes_morning_and_not_previous_evening(client):
    from app import db, services, tools
    old = db.get_setting('timezone', 'UTC')
    db.set_setting('timezone', 'Australia/Sydney')
    created = []
    try:
        for title, start, end in [
            ('Eval morning', '2026-11-06T09:00:00+11:00', '2026-11-06T09:15:00+11:00'),
            ('Eval yesterday', '2026-11-05T23:00:00+11:00', '2026-11-05T23:30:00+11:00'),
            ('Eval tomorrow', '2026-11-07T09:00:00+11:00', '2026-11-07T09:30:00+11:00')]:
            created.append(services.create_event(services.EventIn(title=title, start=start, end=end))['id'])
        titles = {e['title'] for e in tools.list_events('2026-11-06', '2026-11-07')}
        assert 'Eval morning' in titles
        assert 'Eval yesterday' not in titles
        assert 'Eval tomorrow' not in titles
    finally:
        for i in created:
            db.run('DELETE FROM events WHERE id = ?', i)
        db.set_setting('timezone', old)
