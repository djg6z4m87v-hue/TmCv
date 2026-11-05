import json
from pathlib import Path
from urllib.parse import unquote
import pytest
from cover_letters import draft
from test_app import client

CV = '''Brian Example
I supported debt recovery cases, maintained accurate records and drafted client correspondence at Example Solicitors.
I researched housing and employment issues and prepared supervised letters of advice in a university legal clinic.
I used Excel to track deadlines and organise administration tasks for fee earners.
'''
JOB = dict(title='Debt Recovery Paralegal', company='Example Housing', description='Support our housing team with debt recovery cases, client correspondence and accurate case records. Our team helps residents understand their accounts and resolve disputed balances.')


def save_profile():
    from applications import put_settings
    put_settings({'cv_text': CV, 'applicant_profile': json.dumps({'name':'Brian Example','email':'brian@example.org','phone':'07000000000','answers':{}})})
    from database import connect
    with connect() as db:
        db.execute('INSERT INTO jobs(source_id,title,company,url,description) VALUES (?,?,?,?,?)', ('test:personal',JOB['title'],JOB['company'],'https://employer.example.org/apply?job=42',JOB['description']))


def test_grounded_letters_change_with_employer_and_requirements():
    applicant = dict(name='Brian Example', cv_text=CV)
    letter = draft(JOB,applicant)
    second = draft(dict(JOB,title='Compliance Assistant',company='Example Bank',description='Maintain accurate compliance records and support research into regulatory issues. Prepare correspondence and track deadlines within the compliance team.'),applicant)
    assert letter != second and 'Example Housing' in letter and 'Example Bank' in second
    assert 'debt recovery' in letter and 'Brian Example' in letter
    assert '[' not in letter and 'Flint Bishop' not in letter
    assert 'regulatory qualification' not in second


def test_draft_requires_saved_cv_name_and_full_advert():
    with pytest.raises(ValueError): draft(JOB, {'cv_text': CV})
    with pytest.raises(ValueError): draft(dict(JOB, description=''), {'name':'Brian','cv_text':CV})


def test_download_and_helper_use_saved_profile(client):
    save_profile()
    letter = client.get('/jobs/1/cover-letter')
    assert letter.status_code == 200 and 'Brian Example' in letter.text and '[' not in letter.text
    helper = client.get('/jobs/1/autofill')
    assert helper.status_code == 200
    assert b'Use in Safari on iPhone' in helper.data
    assert b'javascript:' in helper.data
    assert b'api_key' not in helper.data
    assert helper.headers['Cache-Control'] == 'no-store'


def test_application_query_identifiers_survive():
    from applications import canonical_url
    assert canonical_url('https://employer.example.org/apply?job=42&utm_source=alert') == 'https://employer.example.org/apply?job=42'


def test_cv_categories_and_seniority():
    from jobs import matching_queries
    from matching import score
    queries = matching_queries(CV)
    assert 'paralegal' in queries or 'revenues officer' in queries
    assert score({'title':'Senior Legal Counsel','description':'legal case management'}, CV) == 0


def test_private_employer_search_is_on_by_default(monkeypatch):
    import jobs, employer_web, employer_feeds
    monkeypatch.delenv('ENABLE_GENERAL_WEB_SEARCH', raising=False)
    monkeypatch.setattr(employer_web,'search',lambda *a:([],[{'source':'Council','status':'available'}]))
    private = dict(JOB, source_id='private:1', url='https://employer.example.org/apply')
    monkeypatch.setattr(employer_feeds,'search_boards',lambda *a:([private],[{'source':'Private firm','status':'available'}]))
    calls=[]
    monkeypatch.setattr(jobs,'general_web_search',lambda *a:calls.append(a) or [])
    rows=jobs.search_jobs('paralegal','United Kingdom')
    assert rows[0]['company']=='Example Housing' and calls


def test_private_feed_filters_location_and_seniority(monkeypatch):
    import employer_feeds as feeds
    monkeypatch.setattr(feeds,'configured_boards',lambda:[{'name':'Firm'}])
    def job(title, location): return dict(JOB,title=title,location=location)
    monkeypatch.setattr(feeds,'fetch_board',lambda *a:[job('Paralegal','London, UK'),job('Senior Legal Counsel','London, UK'),job('Paralegal','Riga, Latvia')])
    rows,status=feeds.search_boards('paralegal','United Kingdom')
    assert len(rows)==1 and rows[0]['location']=='London, UK'


def test_salary_is_not_inferred_from_benefits_or_missing_pay():
    from employer_feeds import published_salary
    assert published_salary({},'Learning budget £1,000 per year')==(None,None)
    assert published_salary({},'Salary: £28,000 - £31,000 per annum')==(28000,31000)
    assert published_salary({},'Salary competitive')==(None,None)
