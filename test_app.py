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
    app.config.update(TESTING=True, CSRF_ENABLED=False)
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
        return [dict(source_id='web:1',title='Legal administrator',company='Firm',location=location,url='https://example.org',description='legal case management',salary_min=30000,salary_max=34000,salary_verified=1),dict(source_id='web:2',title='Other',company='Firm',location=location,url='https://example.org',description='',salary_min=None,salary_max=None,salary_verified=0)]
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


def test_iphone_install_assets_and_private_data(client, monkeypatch):
    import struct
    manifest = client.get('/manifest.webmanifest')
    assert manifest.status_code == 200
    assert manifest.mimetype == 'application/manifest+json'
    data = manifest.json
    assert data['display'] == 'standalone'
    assert data['start_url'] == '/'
    for icon in data['icons']:
        response = client.get(icon['src'])
        assert response.status_code == 200
        width, height = struct.unpack('>II', response.data[16:24])
        assert f'{width}x{height}' == icon['sizes']
    home = client.get('/')
    assert b'apple-touch-icon' in home.data
    assert b'Add to Home Screen' in home.data
    assert home.headers['Cache-Control'] == 'no-store'
    worker = client.get('/sw.js')
    assert worker.status_code == 200
    assert worker.headers['Service-Worker-Allowed'] == '/'
    assert worker.headers['Cache-Control'] == 'no-cache'
    assert client.get('/static/offline.html').status_code == 200
    # Installation assets remain available while account information stays protected.
    monkeypatch.setenv('APP_USERNAME', 'owner')
    monkeypatch.setenv('APP_PASSWORD', 'test-password')
    assert client.get('/').status_code == 401
    assert client.get('/manifest.webmanifest').status_code == 200
    assert client.get('/sw.js').status_code == 200
    assert client.get('/static/app.py').status_code == 404
    assert client.get('/static/careerpilot.db').status_code == 404



def test_web_fallback_uses_selected_category_and_location(client):
    r = client.get('/web-search?category=Paralegal+%2F+casework&location=London')
    assert r.status_code == 303
    assert 'google.com/search?' in r.location
    assert 'paralegal+London' in r.location
    assert '-site%3Aadzuna.co.uk' in r.location


def test_low_floor_and_missing_salary_browse_only(client, monkeypatch):
    import app
    def search(query, location):
        return [dict(source_id='web:low', title='Paralegal', company='Firm',location=location,
                     url='https://jobs.nhs.uk/job/1',description='Work', salary_min=25000,
                     salary_max=35000, salary_verified=1),
                dict(source_id='web:unknown',title='Legal officer',company='Trust',location=location,
                     url='https://jobs.nhs.uk/job/2',description='Work',salary_min=None,
                     salary_max=None,salary_verified=0)]
    monkeypatch.setattr(app, 'search_jobs', search)
    response = client.post('/import', data={'category':'Legal administration', 'location':'Nottingham'})
    assert b'Web search results' in response.data
    assert b'Legal officer' in response.data
    assert b'excluded from automatic imports' in response.data
    from database import connect
    with connect() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
