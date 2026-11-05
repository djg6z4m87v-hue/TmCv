"""Real Chromium + intercepted employer pages. Never contacts an employer."""
import pytest
from applications import digest
from lever_browser import run_form

URL = 'https://jobs.lever.co/example/12345678-1234-1234-1234-123456789abc'
HTML = '''<!doctype html><title>Example — Legal Assistant</title><h1>Example · Legal Assistant</h1>
<form onsubmit="event.preventDefault();document.body.innerHTML='<h1>Thanks for applying</h1><p>Your application has been submitted.</p>'">
<label>Full name<input name="name" required></label><label>Email<input type="email" name="email" required></label><label>Phone<input name="phone" required></label><input type="file" name="resume" required><label>Additional information<textarea name="comments"></textarea></label>{extra}<button>Submit application</button></form>'''


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        yield page
        browser.close()


def exercise(page, extra=''):
    page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=HTML.replace('{extra}', extra)))
    app = {'canonical_url': URL, 'title': 'Legal Assistant', 'company': 'Example', 'cover_letter': 'A verified tailored letter.', 'materials_hash': digest('letter')}
    profile = {'name': 'Example Applicant', 'email': 'applicant@example.org', 'phone': '07000000000', 'answers': {}}
    doc = {'name': 'CV.pdf', 'mime': 'application/pdf', 'content': b'%PDF-test-fixture', 'sha256': 'test-hash'}
    calls = []
    result = run_form(page, app, profile, doc, lambda: calls.append(True) or True)
    return result, calls


def test_browser_fills_uploads_and_records_confirmation(page):
    result, calls = exercise(page)
    assert result['state'] == 'Submitted'
    assert result['receipt']['cv_sha256'] == 'test-hash'
    assert len(calls) == 1


def test_missing_question_stops_before_click(page):
    result, calls = exercise(page, '<label>Notice period<input name="notice" required></label>')
    assert result['state'] == 'Needs input'
    assert result['questions'][0]['question'] == 'Notice period'
    assert calls == []


def test_declaration_stops_before_click(page):
    result, calls = exercise(page, '<label><input type="checkbox" required>I certify this declaration</label>')
    assert result['state'] == 'Needs input' and calls == []


def test_challenge_never_attempts_submission(page):
    result, calls = exercise(page, '<p>Verify you are human</p>')
    assert result['state'] == 'Needs input' and calls == []
