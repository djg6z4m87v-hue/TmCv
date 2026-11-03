import os
import pytest

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path/'test.db'))
    monkeypatch.delenv('APP_USERNAME', raising=False)
    monkeypatch.delenv('APP_PASSWORD', raising=False)
    from database import init_db
    init_db()
    from app import app
    app.config.update(TESTING=True)
    return app.test_client()

def test_home(client):
    r=client.get('/')
    assert r.status_code==200
    assert b'Careerpilot' in r.data

def test_health(client):
    assert client.get('/health').json=={'status':'ok'}

def test_bad_category(client):
    r=client.post('/import', data={'category':'fake','location':'Nottingham'},follow_redirects=True)
    assert b'valid category' in r.data

def test_preferences(client):
    r=client.post('/settings',data={'min_salary':'29000','cv_text':'legal administration'},follow_redirects=True)
    assert b'29000' in r.data

def test_import_dedup_and_predicted_filter(client, monkeypatch):
    import app
    def fake_search(query,location):
        return [dict(source_id='adzuna:1',title='Legal administrator',company='Firm',location=location,url='https://example.org',description='legal case management',salary_min=30000,salary_max=34000,salary_verified=1),dict(source_id='adzuna:2',title='Other',company='Firm',location=location,url='https://example.org',description='',salary_min=None,salary_max=None,salary_verified=0)]
    monkeypatch.setattr(app,'search_jobs',fake_search)
    for _ in range(2):
        client.post('/import',data={'category':'Legal administration','location':'Nottingham'})
    from database import connect
    with connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==1

def test_matching():
    from matching import score
    assert 0<=score({'title':'Legal administrator','description':'legal case management'})<=100


def test_employer_search(client):
    response = client.get('/employer-search?employer=Gedling+Borough+Council&keywords=revenues')
    assert response.status_code == 303
    assert 'site%3Agedling.gov.uk' in response.headers['Location']

def test_employer_save_and_deduplicate(client):
    payload = {'title':'Paralegal','company':'Example Council','url':'https://example.gov.uk/careers/123','location':'Nottingham'}
    for _ in range(2):
        assert client.post('/employer-jobs/add',data=payload).status_code == 302
    from database import connect
    with connect() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE category='Direct employer'").fetchone()[0] == 1

def test_employer_rejects_non_https(client):
    r = client.post('/employer-jobs/add', data={'title':'Role','company':'Employer','url':'http://example.com'},follow_redirects=True)
    assert b'valid HTTPS' in r.data
