import pytest
from public_feeds import configured_public_feeds, fetch_public_feed, PublicFeedError

def test_feed_allowlist(monkeypatch):
    monkeypatch.setenv('PUBLIC_JOB_FEEDS_JSON', '[{"name":"Council","url":"https://jobs.example.org/rss"}]')
    monkeypatch.setenv('PUBLIC_FEED_ALLOWED_HOSTS', 'jobs.example.org')
    assert len(configured_public_feeds()) == 1
    monkeypatch.setenv('PUBLIC_FEED_ALLOWED_HOSTS', 'other.example.org')
    with pytest.raises(PublicFeedError):
        configured_public_feeds()

def test_feed_parse(monkeypatch):
    class Response:
        content = b'<rss><channel><item><title>Legal Officer</title><link>https://jobs.example.org/123</link><description>Legal support</description></item></channel></rss>'
        def raise_for_status(self): pass
    monkeypatch.setattr('public_feeds.requests.get', lambda *args, **kwargs: Response())
    jobs = fetch_public_feed({'name':'Council','url':'https://jobs.example.org/rss'})
    assert len(jobs) == 1 and jobs[0]['title'] == 'Legal Officer'
    assert jobs[0]['salary_verified'] == 0

def test_scheduler_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    from database import init_db
    init_db()
    from app import app
    client = app.test_client()
    monkeypatch.setenv('CRON_SECRET', 'secret-token')
    assert client.post('/tasks/daily').status_code == 401
    assert client.post('/tasks/daily', headers={'Authorization':'Bearer wrong'}).status_code == 401
