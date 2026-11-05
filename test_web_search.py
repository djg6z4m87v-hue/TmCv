import json
from unittest.mock import Mock
import pytest
import jobs

URL = 'https://jobs.nhs.uk/candidate/jobadvert/role-123?language=en'


def page(post):
    return URL, ('<html><script type="application/ld+json">' + json.dumps(post) + '</script></html>').encode()


def posting(**overrides):
    return dict({'@type': 'JobPosting', 'title': 'Legal administrator',
                 'description': '<p>Legal casework and records.</p>',
                 'hiringOrganization': {'name': 'Example NHS Trust'},
                 'baseSalary': {'currency': 'GBP', 'value': {'minValue': 28000, 'maxValue': 31000, 'unitText': 'YEAR'}},
                 'jobLocation': {'address': {'addressLocality': 'Nottingham'}}}, **overrides)


def test_reads_published_salary_and_preserves_query(monkeypatch):
    monkeypatch.setattr(jobs, 'fetch_page', lambda url: page(posting()))
    result = jobs._inspect({'href': URL, 'title': 'Search title', 'body': ''}, 'UK')
    assert result['url'] == URL
    assert result['salary_verified'] == 1 and result['salary_min'] == 28000
    assert result['title'] == 'Legal administrator'
    assert result['company'] == 'Example NHS Trust'
    assert result['location'] == 'Nottingham'


@pytest.mark.parametrize('salary', [None, {'currency': 'USD', 'value': {'value': 30000, 'unitText': 'YEAR'}},
                                     {'currency': 'GBP', 'value': {'value': 20, 'unitText': 'HOUR'}},
                                     {'currency': 'GBP', 'value': {'value': 'NaN', 'unitText': 'YEAR'}}])
def test_never_estimates_missing_or_nonannual_salary(salary, monkeypatch):
    monkeypatch.setattr(jobs, 'fetch_page', lambda url: page(posting(baseSalary=salary)))
    result = jobs._inspect({'href': URL, 'title': 'Role £40,000 estimated'}, 'UK')
    assert result['salary_verified'] == 0 and result['salary_min'] is None


def test_expired_unreadable_agency_and_aggregator_are_excluded(monkeypatch):
    monkeypatch.setattr(jobs, 'fetch_page', lambda url: page(posting(validThrough='2020-01-01')))
    assert jobs._inspect({'href': URL, 'title': 'Role'}, 'UK') is None
    monkeypatch.setattr(jobs, 'fetch_page', lambda url: None)
    assert jobs._inspect({'href': URL, 'title': 'Role'}, 'UK') is None
    assert jobs._inspect({'href': 'https://www.adzuna.co.uk/jobs/details/123', 'title': 'Role'}, 'UK') is None
    assert jobs._inspect({'href': URL, 'title': 'On behalf of our client'}, 'UK') is None


def test_engine_failure_is_clear_and_does_not_call_adzuna(monkeypatch):
    jobs._CACHE.clear()
    engine = Mock()
    engine.text.side_effect = RuntimeError('provider unavailable')
    monkeypatch.setattr(jobs, 'DDGS', lambda **kwargs: engine)
    with pytest.raises(jobs.JobSearchError, match='Open web search'):
        jobs.search_jobs('paralegal', 'London')
    assert '-site:adzuna.co.uk' in engine.text.call_args_list[0].args[0]


def test_cache_dedup_and_external_hosts_not_fetched(monkeypatch):
    jobs._CACHE.clear()
    engine = Mock()
    engine.text.return_value = [{'href': URL, 'title': 'Role'}, {'href': URL, 'title': 'Duplicate'},
                               {'href': 'https://www.adzuna.co.uk/jobs/details/123', 'title': 'Aggregator'}]
    monkeypatch.setattr(jobs, 'DDGS', lambda **kwargs: engine)
    reader = Mock(return_value=page(posting()))
    monkeypatch.setattr(jobs, 'fetch_page', reader)
    first = jobs.search_jobs('legal', 'London')
    first[0]['title'] = 'Changed'
    second = jobs.search_jobs('legal', 'London')
    assert second[0]['title'] == 'Legal administrator'
    reader.assert_called_once_with(URL)
    engine.text.assert_called_once()


def test_redirect_to_private_or_unapproved_host_is_not_followed(monkeypatch):
    monkeypatch.setattr(jobs, '_public_host', lambda host: True)
    response = Mock(status_code=302, headers={'Location': 'https://127.0.0.1/secret'})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    get = Mock(return_value=response)
    monkeypatch.setattr(jobs.requests, 'get', get)
    assert jobs.fetch_page(URL) is None
    get.assert_called_once()
    assert get.call_args.kwargs['allow_redirects'] is False


def test_unsupported_and_encoded_hosts_rejected():
    for url in ['http://jobs.nhs.uk/job', 'https://jobs.nhs.uk.evil.test/job',
                'https://jobs.nhs.uk@evil.test/job', 'https://jobs.nhs.uk:8443/job',
                'https://127.0.0.1/job']:
        assert jobs.direct_url(url) is None
