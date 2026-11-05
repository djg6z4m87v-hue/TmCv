from datetime import datetime, timedelta
from unittest.mock import Mock
from lxml import html
import pytest
import employer_web as web
from jobs import SearchResults


def test_salary_is_read_from_published_field_only():
    assert web.annual_salary('£35,570 - £40,444 per annum') == (35570, 40444)
    for label in ['£30,000 estimated', '£32,000 per annum pro rata', '£14 per hour', 'Grade 7', '£30,000 per month']:
        assert web.annual_salary(label) == (None, None)


def test_parse_council_detail_with_labelled_fields(monkeypatch):
    future = (datetime.now() + timedelta(days=7)).strftime('%d %B %Y')
    page = f'''<div id="vac_desc"><h1>Executive Officer</h1>
    <label role="term" id="salary">Salary</label><div role="definition" aria-labelledby="salary">£35,570 - £40,444 per annum</div>
    <label role="term" id="base">Work Base</label><div role="definition" aria-labelledby="base">Nottingham</div>
    <div><strong>Closing Date</strong></div><div>{future} 23:55</div>
    <p>Provide legal casework support and handle confidential records.</p></div>'''
    monkeypatch.setattr(web, 'cached_read', lambda url: (url, html.fromstring(page)))
    job = web.notts_job('https://nottinghamshire.tal.net/vacancy')
    assert job['salary_verified'] == 1 and job['salary_min'] == 35570
    assert job['company'] == 'Nottinghamshire County Council' and job['location'] == 'Nottingham'
    assert job['deadline'] == (datetime.now() + timedelta(days=7)).date().isoformat()


def test_expired_and_pro_rata_roles_cannot_pass_salary_gate():
    args = ('https://nottinghamshire.tal.net/vacancy','Officer','Council','Nottingham','Casework')
    assert web.row(*args, '£30,000 per annum', '2000-01-01', 'Council') is None
    future = (datetime.now()+timedelta(days=7)).date().isoformat()
    assert web.row(*args,'£30,000 per annum pro rata',future,'Council')['salary_verified'] == 0


def test_cache_limits_duplicate_board_requests(monkeypatch):
    web._CACHE.clear()
    reader = Mock(return_value=('https://myjobscotland.gov.uk/', html.fromstring('<h1>Jobs</h1>')))
    monkeypatch.setattr(web, 'read', reader)
    web.cached_read(web.NOTTS)
    web.cached_read(web.NOTTS)
    reader.assert_called_once_with(web.NOTTS)


def test_source_failure_does_not_hide_other_source(monkeypatch):
    def failed(*args):
        raise web.SourceError('HTTP 503')
    monkeypatch.setattr(web, 'notts_search', failed)
    monkeypatch.setattr(web, 'scotland_search', lambda *args: ([{'source_id': 'web:1','title':'Revenues Assistant'}], {'source': 'myjobscotland','status':'available','listed':19}))
    rows, sources = web.search('revenues officer','United Kingdom')
    assert len(rows) == 1
    assert any(s['status'] == 'unavailable' for s in sources)
    assert any(s['status'] == 'available' for s in sources)


def test_scottish_results_are_not_returned_as_nottingham_roles():
    rows, status = web.scotland_search('revenues','Nottingham',10)
    assert rows == [] and status['status'] == 'outside selected area'


def test_official_fetch_never_follows_unknown_redirect(monkeypatch):
    response = Mock(status_code=302, headers={'Location':'https://127.0.0.1/metadata'})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    get = Mock(return_value=response)
    monkeypatch.setattr(web.requests,'get',get)
    with pytest.raises(web.SourceError, match='Unexpected employer link'):
        web.read(web.NOTTS)
    get.assert_called_once()


def test_category_and_location_are_preserved_after_search(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path/'search.db'))
    from database import init_db
    init_db()
    from app import app
    app.config.update(TESTING=True, CSRF_ENABLED=False)
    import app as module
    monkeypatch.setattr(module,'search_jobs',lambda *args:SearchResults([], [{'source':'Council','status':'available','listed':80}]))
    with app.test_client() as client:
        response=client.post('/import',data={'category':'Revenues / recovery','location':'Nottingham'})
    body=html.fromstring(response.data)
    assert body.xpath('//select[@name="category"]/option[@selected]/text()') == ['Revenues / recovery']
    assert body.xpath('//select[@name="location"]/option[@selected]/text()') == ['Nottingham']
    assert b'80 listings checked' in response.data
    assert b'No matching' not in response.data  # No claim that no UK roles exist.


def test_debt_recovery_does_not_match_nature_recovery():
    terms, _ = web.topic('debt recovery officer')
    assert not any(t in 'local nature recovery strategy officer' for t in terms)
    assert any(t in 'revenues & collections assistant' for t in terms)


def test_wider_alternatives_are_labelled(monkeypatch):
    monkeypatch.setattr(web, 'notts_search', lambda *args: ([], {'source':'Council','status':'available','listed':50}))
    def scotland(q, loc, count):
        return ([{'source_id':'web:alternative','title':'Revenues Assistant'}] if loc == 'United Kingdom' else [],
                {'source':'myjobscotland','status':'available' if loc == 'United Kingdom' else 'outside selected area'})
    monkeypatch.setattr(web,'scotland_search',scotland)
    rows, diagnostics=web.search('revenues officer','Nottingham')
    assert rows[0]['search_scope'] == 'UK-wide alternative'
