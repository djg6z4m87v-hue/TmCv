"""Narrow Lever hosted-form adapter. Challenges and unknown fields are handoffs."""
import re
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from applications import lever_url

CONFIRMATION = re.compile(r'(your application (?:has been|was) (?:successfully )?submitted|thanks for applying|thank you for applying|application received|application submitted)', re.I)
CHALLENGE = re.compile(r'(verify (?:that )?you are human|complete the captcha|security verification|checking your browser|sign in to (?:apply|continue))', re.I)
LEGAL = re.compile(r'(terms (?:of|and)|legally|certify|declaration|signature)', re.I)


def network_allowed(url):
    p = urlparse(url)
    if p.scheme not in ('https', 'data', 'blob', 'about'):
        return False
    if p.scheme != 'https':
        return p.scheme in ('data', 'blob', 'about')
    host = p.hostname or ''
    return (host == 'lever.co' or host.endswith('.lever.co') or
            host in {'fonts.googleapis.com', 'fonts.gstatic.com', 'www.google.com', 'www.gstatic.com'}) and not p.username and not p.password and p.port in (None, 443)


def blocked(reason, questions=None, unknown=False):
    return {'state': 'Unknown' if unknown else 'Needs input', 'reason': reason, 'questions': questions or []}


def inspect_fields(page):
    return page.locator('input,textarea,select').evaluate_all('''els => els.map((e,i) => {
      const box=e.closest('.application-question, .application-additional, .question, fieldset');
      const own=(e.labels ? Array.from(e.labels).map(l=>l.innerText).join(' ') : '') || e.getAttribute('aria-label');
      const label=(own || (box && box.querySelector('.application-label,legend,label')?.innerText) || e.name || e.id || '').trim();
      return {index:i,name:e.name,id:e.id,type:e.type,tag:e.tagName.toLowerCase(),label,
        required:e.required || e.getAttribute('aria-required')==='true' || /required|\\*/i.test(box?.querySelector('.application-label,legend')?.innerText || ''),
        visible:!!e.getClientRects().length,disabled:e.disabled,value:e.value,
        options:e.tagName==='SELECT' ? Array.from(e.options).map(o=>({value:o.value,label:o.text})) : []};
    })''')


def fill_form(page, applicant, document, letter):
    fields = inspect_fields(page)
    answers = applicant.get('answers', {})
    unknown, files, letter_used = [], 0, False
    # Demographics are never inferred. Unknown fields require a saved exact answer.
    basic = {'name': applicant.get('name'), 'email': applicant.get('email'),
             'phone': applicant.get('phone'), 'org': applicant.get('current_company'),
             'urls[LinkedIn]': applicant.get('linkedin'), 'location': applicant.get('location')}
    groups = {}
    for f in fields:
        if f['type'] == 'radio':
            groups.setdefault(f['name'], []).append(f)
    for f in fields:
        if f['disabled'] or f['type'] in ('hidden', 'submit', 'button') or (not f['visible'] and f['type'] != 'file'):
            continue
        loc = page.locator('input,textarea,select').nth(f['index'])
        label = f['label']
        value = answers.get(label)
        if f['type'] == 'file':
            if f['name'] == 'resume':
                loc.set_input_files({'name': document['name'], 'mimeType': document['mime'], 'buffer': document['content']})
                files += 1
            elif re.search('cover.?letter', label, re.I):
                loc.set_input_files({'name': 'Cover_letter.txt', 'mimeType': 'text/plain', 'buffer': letter.encode()})
                letter_used = True
            elif f['required']:
                unknown.append({'question': label, 'reason': 'Additional document required.'})
            continue
        if f['name'] in basic:
            value = basic[f['name']]
        elif f['name'] == 'comments' or re.search('cover.?letter|additional information', label, re.I):
            value = letter
            letter_used = True
        if f['type'] in ('checkbox', 'radio'):
            if LEGAL.search(label):
                unknown.append({'question': label, 'reason': 'Complete this declaration on the employer website.'})
                continue
            if f['type'] == 'radio':
                if value is None:
                    if f['required']:
                        unknown.append({'question': label, 'reason': 'Choose an exact answer.', 'options': [x['value'] for x in groups[f['name']]]})
                elif str(value) == f['value']:
                    loc.check()
                elif str(value) not in [x['value'] for x in groups[f['name']]]:
                    unknown.append({'question': label, 'reason': 'Saved answer does not match an option.'})
            elif value is not None:
                if not isinstance(value, bool):
                    unknown.append({'question': label, 'reason': 'Use true or false for this checkbox.'})
                else:
                    loc.set_checked(value)
                    if f['required'] and not value:
                        unknown.append({'question': label, 'reason': 'This required checkbox cannot be left unchecked.'})
            elif f['required']:
                unknown.append({'question': label, 'reason': 'This required checkbox needs your choice.'})
            continue
        if value is not None and str(value).strip():
            if LEGAL.search(label):
                unknown.append({'question': label, 'reason': 'Complete this declaration yourself.'})
            elif f['tag'] == 'select':
                matches = [o for o in f['options'] if str(value) in (o['label'], o['value'])]
                if len(matches) != 1:
                    unknown.append({'question': label, 'options': [o['label'] for o in f['options']], 'reason': 'Choose an exact option.'})
                else:
                    loc.select_option(matches[0]['value'])
            else:
                loc.fill(str(value))
        elif f['required']:
            unknown.append({'question': label, 'reason': 'No verified answer saved.'})
    if files != 1:
        unknown.append({'question': 'CV upload', 'reason': 'Could not identify a single supported CV field.'})
    if not letter_used:
        unknown.append({'question': 'Cover letter', 'reason': 'This form needs a different way to attach the tailored letter.'})
    return unknown


def normal(value):
    return ' '.join(re.findall(r'[a-z0-9]+', value.lower()))


def run_form(page, app, applicant, document, before_submit):
    page.goto(lever_url(app['canonical_url']), wait_until='domcontentloaded', timeout=45000)
    if not lever_url(page.url):
        return blocked('The vacancy redirected to an unsupported site. Open it manually.')
    body = page.locator('body').inner_text()
    if CHALLENGE.search(body):
        return blocked('The employer requires a sign-in or human check. Open the vacancy to continue.')
    content = normal(page.title() + ' ' + body)
    if normal(app['title']) not in content or normal(app['company']) not in content:
        return blocked('The employer page does not match the saved job title and employer. Check the vacancy link.')
    page.locator('input[name="name"]').wait_for(state='visible', timeout=15000)
    applicant = dict(applicant, answers={**applicant.get('answers', {}), **json.loads(app.get('approved_answers', '{}'))})
    questions = fill_form(page, applicant, document, app['cover_letter'])
    if questions:
        return blocked('The employer needs additional answers or documents. Save exact answers, then retry, or complete the form manually.', questions)
    # HTML constraint validation is necessary but never sufficient for success.
    valid = page.locator('form').evaluate_all('forms => forms.length===1 && forms[0].checkValidity()')
    if not valid:
        return blocked('The employer form still has invalid or missing fields. Complete it manually.')
    buttons = page.get_by_role('button', name=re.compile(r'^submit application$', re.I))
    if buttons.count() != 1 or not buttons.is_enabled():
        return blocked('The employer submission control is not supported. Complete this application manually.')
    if page.get_by_text(CONFIRMATION).count():
        return blocked('A confirmation-like message was already present. Verify the employer form manually.')
    # Persist BEFORE the click. A timeout/crash after this point is never automatically retried.
    if not before_submit():
        return blocked('Application is paused, already attempted, or the daily limit was reached.')
    try:
        buttons.click(timeout=15000)
        page.get_by_text(CONFIRMATION).first.wait_for(state='visible', timeout=30000)
        if page.locator('input[name="name"]').is_visible():
            return blocked('A confirmation message appeared but the application form is still present. Check the employer receipt.', unknown=True)
        if not lever_url(page.url) and urlparse(page.url).hostname not in {'jobs.lever.co', 'jobs.eu.lever.co'}:
            return blocked('Submission left the supported employer site. Check the employer receipt.', unknown=True)
        confirmation = page.get_by_text(CONFIRMATION).first.inner_text()[:1000]
        return {'state': 'Submitted', 'reason': 'Employer confirmation received.', 'receipt': {
            'confirmation': confirmation, 'url': page.url, 'confirmed_at': datetime.now(timezone.utc).isoformat(),
            'provider': 'Lever', 'cv_sha256': document['sha256'], 'materials_hash': app['materials_hash']}}
    except Exception:
        return blocked('No submission confirmation was received. Check your email and the employer site before marking the outcome. It will not be retried automatically.', unknown=True)


def submit(app, applicant, document, before_submit):
    if not lever_url(app['canonical_url']):
        return blocked('This employer website needs manual completion. Download the prepared materials.')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=False)
        context.route('**/*', lambda route: route.continue_() if network_allowed(route.request.url) else route.abort())
        page = context.new_page()
        page.set_default_timeout(15000)
        try:
            return run_form(page, app, applicant, document, before_submit)
        finally:
            context.close()
            browser.close()
