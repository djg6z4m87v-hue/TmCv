import os
import secrets
from functools import wraps
from urllib.parse import urlencode, urlparse
import hashlib
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_from_directory
from database import connect, init_db
from jobs import search_jobs, JobSearchError, CATEGORIES, LOCATIONS, browser_search_url
from matching import score, DEFAULT_CV
from employer_feeds import configured_boards, fetch_board, relevant, FeedError
from public_feeds import configured_public_feeds, fetch_public_feed, PublicFeedError
from monitoring import upcoming_deadlines, notify_deadlines



app = Flask(
    __name__,
    template_folder=".",
    static_folder="static",
    static_url_path="/static"
)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "development-only-change-me"
)

if os.getenv('APP_USERNAME') and not os.getenv('APP_PASSWORD') or os.getenv('APP_PASSWORD') and not os.getenv('APP_USERNAME'):
    raise RuntimeError('Set both APP_USERNAME and APP_PASSWORD, or neither')
STATUSES = ('Saved', 'Interested', 'Drafting', 'Applied', 'Interview', 'Offer', 'Rejected', 'Withdrawn')

def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        username, password = os.getenv('APP_USERNAME'), os.getenv('APP_PASSWORD')
        if username and password:
            auth = request.authorization
            if not auth or not (secrets.compare_digest(auth.username or '', username) and secrets.compare_digest(auth.password or '', password)):
                return Response('Authentication required', 401, {'WWW-Authenticate': 'Basic realm="Careerpilot"'})
        return fn(*args, **kwargs)
    return wrapper

# Public installation assets contain no account or application data.
@app.get('/manifest.webmanifest')
def web_manifest():
    return send_from_directory(app.static_folder, 'manifest.webmanifest',
                               mimetype='application/manifest+json')

@app.get('/sw.js')
def service_worker():
    response = send_from_directory(app.static_folder, 'sw.js',
                                   mimetype='application/javascript')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

@app.after_request
def protect_private_responses(response):
    if request.endpoint not in {'static', 'web_manifest', 'service_worker', 'health'}:
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/health')
def health():
    return {'status': 'ok'}

@app.get('/')
@require_auth
def home(web_results=None, web_query='legal administrator', web_location='Nottingham', selected_category='Legal administration', search_diagnostics=None):
    with connect() as db:
        settings = {r['key']: r['value'] for r in db.execute('SELECT key,value FROM settings')}
        status = request.args.get('status', '')
        if status in STATUSES:
            rows = db.execute("SELECT * FROM jobs WHERE source_id NOT LIKE 'adzuna:%' AND status=? ORDER BY match_score DESC, id DESC", (status,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM jobs WHERE source_id NOT LIKE 'adzuna:%' ORDER BY match_score DESC, id DESC").fetchall()
    try:
        feeds = configured_boards()
        feed_error = ''
    except FeedError as exc:
        feeds = []
        feed_error = str(exc)
    return render_template('index.html', feeds=feeds, feed_error=feed_error, jobs=rows, statuses=STATUSES, selected=status, categories=CATEGORIES, locations=LOCATIONS, settings=settings, employers=EMPLOYERS, web_results=web_results, web_query=web_query, web_location=web_location, browser_search_url=browser_search_url, selected_category=selected_category, search_diagnostics=search_diagnostics or [], deadline_alerts=upcoming_deadlines(), public_feeds=configured_public_feeds_safe())

@app.post('/settings')
@require_auth
def settings():
    minimum = request.form.get('min_salary', '27000').strip()
    try:
        minimum_int = max(0, int(minimum))
    except ValueError:
        flash('Minimum salary must be a whole number.', 'error')
        return redirect(url_for('home'))
    cv = request.form.get('cv_text', '').strip()[:12000]
    with connect() as db:
        for k, v in {'min_salary': str(minimum_int), 'cv_text': cv}.items():
            db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (k,v))
    flash('Preferences saved.', 'success')
    return redirect(url_for('home'))

@app.post('/import')
@require_auth
def import_jobs():
    category = request.form.get('category', '')
    location = request.form.get('location', '')
    if category not in CATEGORIES or location not in LOCATIONS:
        flash('Select a valid category and location.', 'error')
        return redirect(url_for('home'))
    with connect() as db:
        settings = {r['key']: r['value'] for r in db.execute('SELECT key,value FROM settings')}
    minimum = int(settings.get('min_salary', '27000'))
    cv = settings.get('cv_text') or DEFAULT_CV
    added = 0
    errors = []
    candidates = {}
    diagnostics = {}
    for query in CATEGORIES[category]:
        try:
            results = search_jobs(query, location)
            for diagnostic in getattr(results, 'diagnostics', []):
                diagnostics[diagnostic['source']] = diagnostic
        except JobSearchError as exc:
            errors.append(f'{query}: {exc}')
            continue
        with connect() as db:
            for j in results:
                candidates[j['source_id']] = j
                if j.get('search_scope') == 'UK-wide alternative' and location != 'United Kingdom':
                    continue
                # Automatic imports need a published annual salary floor.
                if not j['salary_verified'] or j['salary_min'] is None or j['salary_min'] < minimum:
                    continue
                j.setdefault('deadline', '')
                j['category'] = category
                j['match_score'] = score(j, cv)
                cur = db.execute('''INSERT OR IGNORE INTO jobs
                    (source_id,title,company,location,category,url,description,salary_min,salary_max,salary_verified,match_score,deadline)
                    VALUES (:source_id,:title,:company,:location,:category,:url,:description,:salary_min,:salary_max,:salary_verified,:match_score,:deadline)''', j)
                added += cur.rowcount
    wider = any(j.get('search_scope') == 'UK-wide alternative' for j in candidates.values())
    if wider:
        flash('No matching local adverts were found in the connected sources. Showing wider UK alternatives; select United Kingdom to save eligible matches.', 'success')
    if errors and not candidates:
        flash('Search could not complete. ' + ' '.join(dict.fromkeys(errors)), 'error')
    else:
        flash(f'Found {len(candidates)} live employer adverts, including related roles; saved {added} new jobs meeting your salary minimum.', 'success')
    return home(web_results=list(candidates.values()), web_query=CATEGORIES[category][0], web_location=location, selected_category=category, search_diagnostics=list(diagnostics.values()))

@app.get('/web-search')
@require_auth
def open_web_search():
    category = request.args.get('category', 'Legal administration')
    location = request.args.get('location', 'Nottingham')
    if category not in CATEGORIES or location not in LOCATIONS:
        return 'Choose a valid category and location.', 400
    return redirect(browser_search_url(CATEGORIES[category][0], location), code=303)

@app.post('/jobs/<int:job_id>/update')
@require_auth
def update_job(job_id):
    status = request.form.get('status', '')
    if status not in STATUSES:
        flash('Invalid status.', 'error')
        return redirect(url_for('home'))
    notes = request.form.get('notes', '')[:4000]
    deadline = request.form.get('deadline', '')[:10]
    with connect() as db:
        db.execute('UPDATE jobs SET status=?,notes=?,deadline=? WHERE id=?', (status,notes,deadline,job_id))
    flash('Job updated.', 'success')
    return redirect(url_for('home'))

@app.get('/jobs/<int:job_id>/cover-letter')
@require_auth
def cover_letter(job_id):
    with connect() as db:
        job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if job is None:
        return 'Job not found', 404
    # Draft only: no fabricated employer projects or automated submissions.
    letter = f'''Dear Hiring Team,\n\nI am writing to apply for the {job['title']} position at {job['company']}. I hold a 2:1 LLB from the University of Derby and have experience in legal administration, debt recovery, case management and client communication.\n\nAt Flint Bishop Solicitors, I supported case administration, correspondence, accurate records and reporting deadlines. Through supervised legal advice work, I developed research, interviewing and written communication skills across a range of matters. I would bring this attention to detail and client-focused approach to your team.\n\nI am interested in {job['company']} because [add a specific, verified reason based on the organisation's work or projects]. My experience would be useful in [connect your experience to a requirement from the job description].\n\nThank you for considering my application.\n\nYours faithfully,\n[Your name]'''
    return Response(letter, mimetype='text/plain; charset=utf-8', headers={'Content-Disposition': f'attachment; filename="careerpilot-letter-{job_id}.txt"'})


EMPLOYERS = {
    'Nottingham City Council': 'nottinghamcity.gov.uk',
    'Nottinghamshire County Council': 'nottinghamshire.gov.uk',
    'Derby City Council': 'derby.gov.uk',
    'Leicester City Council': 'leicester.gov.uk',
    'Gedling Borough Council': 'gedling.gov.uk',
    'Rushcliffe Borough Council': 'rushcliffe.gov.uk',
    'Browne Jacobson': 'brownejacobson.com',
    'Flint Bishop': 'flintbishop.co.uk',
    'Geldards': 'geldards.com',
    'NHS Jobs': 'jobs.nhs.uk',
    'Civil Service Jobs': 'civilservicejobs.service.gov.uk',
}

@app.get('/employer-search')
@require_auth
def employer_search():
    employer = request.args.get('employer', '')
    keywords = request.args.get('keywords', '').strip()[:100]
    if employer not in EMPLOYERS:
        flash('Choose an employer from the list.', 'error')
        return redirect(url_for('home'))
    query = f'site:{EMPLOYERS[employer]} (jobs OR careers OR vacancies) {keywords}'
    return redirect('https://www.google.com/search?' + urlencode({'q': query}), code=303)

@app.post('/employer-jobs/add')
@require_auth
def add_employer_job():
    title = request.form.get('title', '').strip()[:200]
    company = request.form.get('company', '').strip()[:200]
    url = request.form.get('url', '').strip()[:2000]
    location = request.form.get('location', '').strip()[:200]
    description = request.form.get('description', '').strip()[:5000]
    parsed = urlparse(url)
    if not title or not company or parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        flash('Enter a title, employer and valid HTTPS vacancy URL.', 'error')
        return redirect(url_for('home'))
    with connect() as db:
        cv_row = db.execute("SELECT value FROM settings WHERE key='cv_text'").fetchone()
        cv = cv_row['value'] if cv_row and cv_row['value'] else DEFAULT_CV
        source_id = 'employer:' + hashlib.sha256(url.encode()).hexdigest()
        cur = db.execute("""INSERT OR IGNORE INTO jobs
            (source_id,title,company,location,category,url,description,salary_verified,match_score)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (source_id,title,company,location,'Direct employer',url,description,0,score({'title':title,'description':description},cv)))
    flash('Employer vacancy saved.' if cur.rowcount else 'Vacancy already saved.', 'success')
    return redirect(url_for('home'))

@app.post('/employer-feeds/import')
@require_auth
def import_employer_feeds():
    keywords = request.form.get('keywords','').strip()[:250]
    location = request.form.get('location','').strip()[:100]
    try:
        boards = configured_boards()
    except FeedError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('home'))
    if not boards:
        flash('No employer ATS boards configured. Set EMPLOYER_BOARDS_JSON.', 'error')
        return redirect(url_for('home'))
    with connect() as db:
        cv_row = db.execute("SELECT value FROM settings WHERE key='cv_text'").fetchone()
        cv = cv_row['value'] if cv_row and cv_row['value'] else DEFAULT_CV
    added = 0
    checked = 0
    errors = []
    for board in boards:
        try:
            jobs = fetch_board(board)
            checked += len(jobs)
        except FeedError as exc:
            errors.append(str(exc))
            continue
        with connect() as db:
            for job in jobs:
                if not relevant(job, keywords, location):
                    continue
                job['match_score'] = score(job, cv)
                cur = db.execute("""INSERT OR IGNORE INTO jobs
                    (source_id,title,company,location,category,url,description,salary_min,salary_max,salary_verified,match_score)
                    VALUES (:source_id,:title,:company,:location,:category,:url,:description,:salary_min,:salary_max,:salary_verified,:match_score)""",job)
                added += cur.rowcount
    flash(f'Checked {checked} employer listings; saved {added} new matches.' +
          (' Feed errors: '+'; '.join(errors) if errors else ''), 'error' if errors else 'success')
    return redirect(url_for('home'))


def configured_public_feeds_safe():
    try:
        return configured_public_feeds()
    except PublicFeedError:
        return []

def run_public_feed_import(keywords='', location=''):
    feeds = configured_public_feeds()
    with connect() as db:
        cv_row = db.execute("SELECT value FROM settings WHERE key='cv_text'").fetchone()
        cv = cv_row['value'] if cv_row and cv_row['value'] else DEFAULT_CV
    checked = added = 0
    errors = []
    for feed in feeds:
        try:
            jobs = fetch_public_feed(feed)
            checked += len(jobs)
        except PublicFeedError as exc:
            errors.append(str(exc))
            continue
        with connect() as db:
            for job in jobs:
                if not relevant(job, keywords, location):
                    continue
                job['match_score'] = score(job, cv)
                cur = db.execute("""INSERT OR IGNORE INTO jobs
                    (source_id,title,company,location,category,url,description,salary_min,salary_max,salary_verified,match_score)
                    VALUES (:source_id,:title,:company,:location,:category,:url,:description,:salary_min,:salary_max,:salary_verified,:match_score)""", job)
                added += cur.rowcount
    return checked, added, errors

@app.post('/public-feeds/import')
@require_auth
def import_public_feeds():
    try:
        checked, added, errors = run_public_feed_import(request.form.get('keywords','')[:250], request.form.get('location','')[:100])
    except PublicFeedError as exc:
        flash(str(exc), 'error')
        return redirect(url_for('home'))
    flash(f'Checked {checked} official-feed listings; imported {added} new jobs.' + (' Errors: ' + '; '.join(errors) if errors else ''), 'error' if errors else 'success')
    return redirect(url_for('home'))

@app.post('/tasks/daily')
def daily_tasks():
    token = os.getenv('CRON_SECRET', '')
    provided = request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    if not token or not secrets.compare_digest(provided, token):
        return {'error': 'Unauthorized'}, 401
    results = {'public_feeds_checked': 0, 'public_feeds_added': 0, 'feed_errors': []}
    try:
        checked, added, errors = run_public_feed_import()
        results.update(public_feeds_checked=checked, public_feeds_added=added, feed_errors=errors)
    except PublicFeedError as exc:
        results['feed_errors'].append(str(exc))
    try:
        results['deadline_notifications'] = notify_deadlines()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        app.logger.warning('Deadline notification failed: %s', type(exc).__name__)
        results['notification_error'] = 'Notification delivery failed; check server configuration'
    return results

from application_routes import register as register_application_routes
register_application_routes(app, require_auth)
init_db()

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG') == '1')


