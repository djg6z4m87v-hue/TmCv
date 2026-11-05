import io
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
import applications as wf
from database import connect, init_db

URL = 'https://jobs.lever.co/example/12345678-1234-1234-1234-123456789abc'
LETTER = 'Dear Hiring Team,\n\n' + 'I bring legal administration and supervised legal advice experience to this legal support role. ' * 5 + '\nBrian Example'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'workflow.db'))
    monkeypatch.setenv('SECRET_KEY', 'test-secret-not-for-production')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('APP_USERNAME', raising=False)
    monkeypatch.delenv('APP_PASSWORD', raising=False)
    init_db()
    from docx import Document
    doc = Document()
    doc.add_paragraph('Brian Example. LLB Law 2:1. Legal administrator, legal research, client interviews, debt recovery. ' * 5)
    data = io.BytesIO()
    doc.save(data)
    wf.save_cv('CV.docx', data.getvalue())
    wf.put_settings({'applicant_profile': json.dumps({'name': 'Brian Example', 'email': 'brian@example.org', 'phone': '07000000000', 'answers': {}}),
                     'profile_confirmed': '1', 'ai_consent': '1', 'automation_enabled': '1', 'auto_submit': '1', 'daily_target': '10'})
    with connect() as db:
        db.execute('''INSERT INTO jobs(source_id,title,company,location,url,description,salary_min,salary_max,salary_verified)
            VALUES ('test:1','Legal Assistant','Example','Nottingham',?, ?,27000,32000,1)''', (URL, 'We need a Legal Assistant to support client communication, legal research and case administration. Graduates with supervised legal advice experience are welcome. ' * 3))
    return wf.enqueue(1)


def ready(app_id):
    with connect() as db:
        db.execute("UPDATE applications SET state='Ready',cover_letter=?,materials_hash=?,profile_hash=? WHERE id=?", (LETTER, wf.digest(LETTER), wf.profile_fingerprint(), app_id))


def test_deduplicate_aliases_and_completed_jobs(setup):
    assert wf.enqueue(1, URL + '/apply?source=other') == setup
    with connect() as db:
        db.execute("UPDATE jobs SET status='Applied' WHERE id=1")
    with pytest.raises(ValueError, match='completed'):
        wf.enqueue(1)


def test_salary_floor_and_agency_filter(setup):
    app = wf.get_application(setup)
    app.update(salary_min=26000, salary_max=40000)
    assert '£27,000' in wf.eligibility(app)
    app.update(salary_min=27000, description='On behalf of our client, we recruit legal assistants.')
    assert 'agency' in wf.eligibility(app)


def test_cv_is_private_and_api_key_encrypted(setup):
    key = 'sk-test-private-value-not-a-live-key'
    wf.save_ai_key(key)
    assert key not in wf.settings()['ai_key_encrypted']
    assert wf.ai_key() == key
    assert wf.cv_document()['content'].startswith(b'PK')


def test_generation_missing_key_does_not_call_network(setup):
    http = Mock()
    wf.prepare(setup, http=http)
    assert wf.get_application(setup)['state'] == 'Needs input'
    http.post.assert_not_called()


def test_grounded_materials_and_missing_requirements(setup):
    wf.save_ai_key('sk-test-private-value-not-a-live-key')
    http = Mock()
    http.post.return_value.status_code = 200
    http.post.return_value.json.return_value = {'choices': [{'message': {'content': json.dumps({'cover_letter': LETTER, 'suitable': True, 'missing_information': [], 'rationale': 'Matched legal support.'})}}]}
    wf.prepare(setup, http=http)
    assert wf.get_application(setup)['state'] == 'Ready'
    assert http.post.call_args.kwargs['allow_redirects'] is False
    data = json.loads(http.post.call_args.kwargs['json']['messages'][1]['content'])
    assert 'cv_text' in data['applicant']
    http.post.return_value.json.return_value['choices'][0]['message']['content'] = json.dumps({'cover_letter': LETTER, 'suitable': False, 'missing_information': ['Five years of litigation experience'], 'rationale': 'Essential requirement missing.'})
    wf.prepare(setup, http=http)
    assert wf.get_application(setup)['state'] == 'Needs input'
    assert 'Five years' in wf.get_application(setup)['reason']


def test_claim_exclusive_and_interrupt_never_resends(setup):
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: wf.claim_next(), range(2)))
    assert sum(c is not None for c in claims) == 1
    with connect() as db:
        db.execute("UPDATE applications SET state='Submitting',submit_started=CURRENT_TIMESTAMP WHERE id=?", (setup,))
    wf.recover_interrupted()
    assert wf.get_application(setup)['state'] == 'Unknown'
    assert wf.claim_next() is None


def test_unknown_outcome_is_never_applied_or_retried(setup):
    ready(setup)
    calls = []
    def submit(app, applicant, doc, before):
        assert before()
        calls.append(app['id'])
        return {'state': 'Unknown', 'reason': 'No employer receipt.'}
    wf.worker_once(submitter=submit)
    assert wf.get_application(setup)['state'] == 'Unknown'
    with connect() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=1').fetchone()['status'] == 'Saved'
    assert wf.worker_once(submitter=submit) is False
    assert len(calls) == 1


def test_receipt_required_and_profile_change_stops_submission(setup):
    ready(setup)
    wf.put_settings({'applicant_profile': json.dumps({'name': 'Changed', 'email': 'changed@example.org'})})
    submitter = Mock()
    wf.worker_once(submitter=submitter)
    submitter.assert_not_called()
    assert 'profile changed' in wf.get_application(setup)['reason'].lower() or 'name, email' in wf.get_application(setup)['reason']


def test_success_records_receipt_and_daily_cap(setup):
    ready(setup)
    wf.put_settings({'daily_target': '1'})
    def submit(app, applicant, doc, before):
        assert before()
        assert not before()
        return {'state': 'Submitted', 'reason': 'Employer confirmed', 'receipt': {'confirmation': 'Thanks for applying', 'reference': 'test-receipt'}}
    wf.worker_once(submitter=submit)
    assert wf.submission_limit_reached()
    app = wf.get_application(setup)
    assert app['state'] == 'Submitted' and app['job_status'] == 'Applied'
    assert 'test-receipt' in app['receipt']


def test_pause_and_review_mode_do_not_submit(setup):
    ready(setup)
    wf.put_settings({'automation_enabled': '0'})
    assert wf.worker_once(submitter=Mock()) is False
    wf.put_settings({'automation_enabled': '1', 'auto_submit': '0'})
    submitter = Mock()
    wf.worker_once(submitter=submitter)
    submitter.assert_not_called()
    assert 'approve' in wf.get_application(setup)['reason']


def test_owner_routes_csrf_and_no_key_leaks(setup, monkeypatch):
    from app import app
    monkeypatch.setitem(app.config, 'TESTING', True)
    monkeypatch.setitem(app.config, 'CSRF_ENABLED', True)
    monkeypatch.setenv('APP_USERNAME', 'owner')
    monkeypatch.setenv('APP_PASSWORD', 'test-login')
    wf.save_ai_key('sk-test-private-value-not-a-live-key')
    client = app.test_client()
    assert client.get('/applications').status_code == 401
    assert client.get('/applications/cv').status_code == 401
    headers = {'Authorization': 'Basic b3duZXI6dGVzdC1sb2dpbg=='}
    response = client.get('/applications', headers=headers, base_url='https://localhost')
    assert response.status_code == 200
    assert b'sk-test-private-value' not in response.data
    assert client.post('/applications/discover', headers=headers).status_code == 400
    with client.session_transaction() as sess:
        token = sess['csrf']
    assert client.post('/applications/discover', data={'csrf_token': token}, headers=headers, base_url='https://localhost').status_code == 302
    assert client.get('/static/careerpilot.db').status_code == 404


def test_no_attempt_without_consent_or_after_pause(setup):
    ready(setup)
    wf.claim_next()
    wf.put_settings({'automation_enabled': '0'})
    assert wf.begin_submission(setup) is False
    with connect() as db:
        assert db.execute("SELECT COUNT(*) n FROM application_events WHERE state='Attempt'").fetchone()['n'] == 0


def test_supported_urls_only():
    from lever_browser import network_allowed
    assert wf.lever_url(URL + '/apply?utm=tracker') == URL + '/apply'
    assert wf.lever_url('https://jobs.lever.co.evil.example/a/b') is None
    assert not network_allowed('http://127.0.0.1/metadata')
    assert not network_allowed('https://evil.example/collect')
    assert not network_allowed('https://jobs.lever.co@evil.example/collect')
    with pytest.raises(ValueError):
        wf.canonical_url('https://user:password@example.org/job')
