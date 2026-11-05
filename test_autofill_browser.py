"""Exercise the actual browser helper against local fixtures, never employer sites."""
from pathlib import Path
from test_lever_browser import page

SCRIPT = Path(__file__).with_name('static').joinpath('autofill.js').read_text()
DATA = {'host':'employer.example.org','first':'Brian','last':'Example','name':'Brian Example','email':'brian@example.org','phone':'07000000000','letter':'A grounded tailored letter.','answers':{'Notice period':'3 months','Salary expectation':'99999'}}


def test_helper_fills_and_preserves_review_fields(page):
    page.route('**/*',lambda r:r.fulfill(content_type='text/html',body='''<form><label>First name<input name="first_name" required></label><label>Surname<input name="last_name"></label><label>Email<input type="email"></label><label>Phone<input type="tel" value="existing"></label><label>Cover letter<textarea></textarea></label><label>Salary expectation<input name="salary" required></label><label>I certify this declaration<input type="checkbox" required></label><label>Notice period<input name="notice"></label><button>Submit</button></form>'''))
    page.goto('https://employer.example.org/apply')
    page.on('dialog',lambda d:d.dismiss())
    page.add_script_tag(content=SCRIPT)
    result=page.evaluate('data=>careerpilotFill(data)',DATA)
    assert result['filled']==5
    assert page.locator('[name=first_name]').input_value()=='Brian'
    assert page.locator('textarea').input_value()==DATA['letter']
    assert page.locator('input[type=tel]').input_value()=='existing'
    assert page.locator('[name=salary]').input_value()==''
    assert not page.locator('input[type=checkbox]').is_checked()
    assert page.locator('[name=notice]').input_value()=='3 months'
    assert page.locator('form').count()==1


def test_helper_rejects_different_website(page):
    page.route('**/*',lambda r:r.fulfill(content_type='text/html',body='<label>Email<input type="email"></label>'))
    page.goto('https://other.example.org/apply')
    page.on('dialog',lambda d:d.dismiss())
    page.add_script_tag(content=SCRIPT)
    assert page.evaluate('data=>careerpilotFill(data)',DATA)['blocked']
    assert page.locator('input').input_value()==''
