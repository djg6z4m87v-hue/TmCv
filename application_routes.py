"""Owner-only application setup, queue, review, files and receipts."""
import json
import re
import secrets
import sqlite3
from io import BytesIO
from flask import abort, flash, redirect, render_template, request, send_file, session, url_for
from database import connect
import applications as workflow


def register(app, require_auth):
    app.config['MAX_CONTENT_LENGTH'] = 6 * 1024 * 1024
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    def csrf_token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return session['csrf']

    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.before_request
    def csrf_protection():
        # Scheduler endpoints authenticate separately using a constant-time bearer check.
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and not request.path.startswith('/tasks/') and app.config.get('CSRF_ENABLED', True):
            token = request.form.get('csrf_token', '')
            if not token or not secrets.compare_digest(token, session.get('csrf', '')):
                abort(400, 'This form expired. Reload the page and try again.')

    @app.get('/applications')
    @require_auth
    def application_dashboard():
        config = workflow.settings()
        applicant = workflow.profile(config)
        applicant['answers_text'] = json.dumps(applicant.get('answers', {}), ensure_ascii=False, indent=2)
        with connect() as db:
            rows = [dict(r) for r in db.execute('SELECT a.*,j.title,j.company,j.salary_min,j.salary_max FROM applications a JOIN jobs j ON j.id=a.job_id ORDER BY a.id DESC LIMIT 200')]
            events = db.execute('SELECT e.*,j.title FROM application_events e JOIN applications a ON a.id=e.application_id JOIN jobs j ON j.id=a.job_id ORDER BY e.id DESC LIMIT 30').fetchall()
            counts = {r['state']: r['n'] for r in db.execute('SELECT state,COUNT(*) n FROM applications GROUP BY state')}
            jobs = db.execute("SELECT id,title,company FROM jobs WHERE source_id NOT LIKE 'adzuna:%' AND id NOT IN (SELECT job_id FROM applications) AND status NOT IN ('Applied','Offer','Interview','Rejected','Withdrawn') ORDER BY match_score DESC LIMIT 100").fetchall()
        for row in rows:
            row['questions_data'] = json.loads(row['questions'])
            row['draft_answers_data'] = json.loads(row['draft_answers'])
        return render_template('applications.html', config=config, applicant=applicant, applications=rows,
                               events=events, counts=counts, jobs=jobs, document=workflow.cv_document(),
                               ai_connected=bool(workflow.ai_key(config)), profile_complete=config.get('profile_confirmed') == '1')

    @app.post('/applications/setup')
    @require_auth
    def application_setup():
        try:
            p = {key: request.form.get(key, '').strip()[:500] for key in ('name', 'email', 'phone', 'location', 'current_company', 'linkedin')}
            if not p['name'] or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', p['email']):
                raise ValueError('Enter your full name and valid email address.')
            answers = {}
            for question, answer, kind in zip(request.form.getlist('answer_key'), request.form.getlist('answer_value'), request.form.getlist('answer_type')):
                if question.strip():
                    if kind == 'boolean' and answer.strip().lower() not in ('true', 'false'):
                        raise ValueError('Checkbox answers must be true or false.')
                    answers[question.strip()] = answer.strip().lower() == 'true' if kind == 'boolean' else answer.strip()
            if not isinstance(answers, dict) or len(answers) > 100 or any(not isinstance(k, str) or not isinstance(v, (str, bool)) or len(str(v)) > 6000 for k, v in answers.items()):
                raise ValueError('Saved answers must be a JSON object with question text mapped to text or true/false.')
            p['answers'] = answers
            upload = request.files.get('cv')
            if upload and upload.filename:
                workflow.save_cv(upload.filename, upload.read(5 * 1024 * 1024 + 1))
            if request.form.get('api_key', '').strip():
                if not request.form.get('ai_consent'):
                    raise ValueError('Confirm sending your CV and vacancy details to OpenAI before saving the connection.')
                workflow.save_ai_key(request.form['api_key'].strip())
            minimum = max(27000, int(request.form.get('min_salary', '27000')))
            target = min(10, max(1, int(request.form.get('daily_target', '10'))))
            confirmed = request.form.get('profile_confirmed') == 'on'
            if confirmed and (not workflow.cv_document() or not p['phone']):
                raise ValueError('Upload your CV and enter your phone number before confirming the profile.')
            workflow.put_settings({'applicant_profile': json.dumps(p), 'min_salary': str(minimum), 'daily_target': str(target),
                                   'profile_confirmed': '1' if confirmed else '0',
                                   'ai_consent': '1' if request.form.get('ai_consent') else '0',
                                   'automation_enabled': '1' if request.form.get('automation_enabled') and confirmed else '0',
                                   'auto_submit': '1' if request.form.get('auto_submit') and confirmed else '0',
                                   'daily_discovery': '1' if request.form.get('daily_discovery') and confirmed else '0'})
            flash('Application settings saved. Changes to your profile require queued materials to be prepared again.', 'success')
        except (ValueError, TypeError):
            flash('Check the profile, readable PDF/DOCX CV, AI consent and saved answers. Minimum salary is £27,000; daily target is 1–10.', 'error')
        return redirect(url_for('application_dashboard'))

    @app.post('/applications/disconnect-ai')
    @require_auth
    def disconnect_ai():
        workflow.put_settings({'ai_key_encrypted': '', 'ai_consent': '0', 'automation_enabled': '0', 'auto_submit': '0'})
        flash('Saved AI connection removed and automation paused. Any server-managed connection must be removed in Railway.', 'success')
        return redirect(url_for('application_dashboard'))

    @app.post('/applications/queue')
    @require_auth
    def queue_application():
        try:
            app_id = workflow.enqueue(int(request.form.get('job_id', '0')), request.form.get('direct_url') or None)
            flash(f'Application #{app_id} is in the queue.', 'success')
        except (ValueError, TypeError) as exc:
            flash(str(exc), 'error')
        return redirect(url_for('application_dashboard'))

    @app.post('/applications/discover')
    @require_auth
    def discover_applications():
        workflow.put_settings({'discovery_requested': '1'})
        flash('Job search scheduled. New matches will appear in the queue as the worker processes them.', 'success')
        return redirect(url_for('application_dashboard'))

    @app.post('/applications/<int:app_id>/review')
    @require_auth
    def review_application(app_id):
        action = request.form.get('action', '')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM applications WHERE id=?', (app_id,)).fetchone()
            if not row:
                abort(404)
            if row['state'] in ('Preparing', 'Submitting', 'Submitted'):
                flash('This application is processing or already submitted. Its materials cannot be changed.', 'error')
                return redirect(url_for('application_dashboard'))
            if row['submit_started'] and action not in ('confirm-submitted', 'skip'):
                flash('A submission was already attempted. Check the employer outcome and record it; automatic retries are blocked.', 'error')
                return redirect(url_for('application_dashboard'))
            if action == 'skip':
                workflow.record(app_id, 'Skipped', 'Skipped by you.', db)
            elif action == 'confirm-submitted':
                receipt = request.form.get('receipt', '').strip()[:2000]
                if not receipt:
                    flash('Paste the employer confirmation or reference before recording submission.', 'error')
                else:
                    db.execute('UPDATE applications SET receipt=? WHERE id=?', (json.dumps({'confirmed_by': 'owner', 'confirmation': receipt}), app_id))
                    db.execute("UPDATE jobs SET status='Applied' WHERE id=?", (row['job_id'],))
                    workflow.record(app_id, 'Submitted', 'Submission confirmed by you.', db)
            elif action in ('prepare', 'approve'):
                try:
                    url = workflow.canonical_url(request.form.get('direct_url') or row['canonical_url'])
                    db.execute('UPDATE applications SET canonical_url=? WHERE id=?', (url, app_id))
                except (ValueError, sqlite3.IntegrityError):
                    # A unique canonical URL also prevents the same application through another listing.
                    flash('Use a valid employer URL that is not already in the queue.', 'error')
                    return redirect(url_for('application_dashboard'))
                description = request.form.get('description', '').strip()[:20000]
                if description:
                    if action == 'approve' and description != workflow.get_application(app_id)['description']:
                        flash('Prepare the application again after changing its vacancy description.', 'error')
                        return redirect(url_for('application_dashboard'))
                    db.execute('UPDATE jobs SET description=? WHERE id=?', (description, row['job_id']))
                if request.form.get('salary_confirmed'):
                    try:
                        salary = int(request.form.get('salary_min', '0'))
                        if not 27000 <= salary <= 1000000:
                            raise ValueError()
                    except ValueError:
                        flash('Enter the advertised starting annual salary of £27,000 or above.', 'error')
                        return redirect(url_for('application_dashboard'))
                    db.execute('UPDATE jobs SET salary_min=?,salary_max=CASE WHEN salary_max IS NULL OR salary_max<? THEN ? ELSE salary_max END,salary_verified=1 WHERE id=?', (salary, salary, salary, row['job_id']))
                if action == 'approve':
                    letter = request.form.get('cover_letter', '').strip()[:6500]
                    answer_drafts = json.loads(row['draft_answers'])
                    approved_answers = {k: v.strip()[:6000] for k,v in zip(request.form.getlist('draft_question'), request.form.getlist('draft_answer')) if k in answer_drafts and v.strip()}
                    if len(letter) < 250 or not row['profile_hash']:
                        flash('Prepare and review a complete cover letter before approval.', 'error')
                    else:
                        approved = workflow.digest([letter, row['profile_hash']])
                        db.execute('UPDATE applications SET cover_letter=?,materials_hash=?,approved_hash=?,approved_answers=? WHERE id=?', (letter, workflow.digest(letter), approved, json.dumps(approved_answers), app_id))
                        workflow.record(app_id, 'Ready', 'Materials approved by you. Waiting for the worker.', db)
                else:
                    db.execute("UPDATE applications SET approved_hash='' WHERE id=?", (app_id,))
                    workflow.record(app_id, 'Queued', 'Queued for fresh preparation.', db)
        return redirect(url_for('application_dashboard'))

    @app.get('/applications/cv')
    @require_auth
    def application_cv():
        doc = workflow.cv_document()
        if not doc:
            abort(404)
        return send_file(BytesIO(doc['content']), mimetype=doc['mime'], as_attachment=True, download_name=doc['name'])

    @app.get('/applications/<int:app_id>/materials')
    @require_auth
    def application_materials(app_id):
        row = workflow.get_application(app_id)
        if not row or not row['cover_letter']:
            abort(404)
        return send_file(BytesIO(row['cover_letter'].encode()), mimetype='text/plain', as_attachment=True, download_name=f'Cover_letter_{app_id}.txt')
