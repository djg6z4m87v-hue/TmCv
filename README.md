# Careerpilot — clean rebuild

A mobile-first Flask application for discovering UK jobs via Adzuna, matching against a CV, tracking applications, and downloading editable cover-letter drafts.

## Features and boundaries
- Searches a selected category (two queries) and UK location on demand. No scheduled background search.
- Imports only vacancies whose Adzuna API salary is **explicitly non-predicted** and whose `salary_max` meets the minimum. This is not a guarantee the employer confirmed the salary; verify the vacancy itself.
- Keyword matching is a simple heuristic, **not AI**. Scores are indicative, not probabilities.
- Stores jobs, notes, CV text, and preferences in SQLite. Does not automatically apply to jobs or send email.
- Cover letters contain placeholders for employer-specific research; review before use.
- Job description content is treated as untrusted data and rendered as text.

## Run locally
Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env  # reference only; .env is not automatically loaded
export ADZUNA_APP_ID='your-id'
export ADZUNA_APP_KEY='your-key'
export SECRET_KEY='generate-a-random-secret'
python app.py
```

Open http://localhost:5000. The app can start without Adzuna keys, but importing jobs will display a credentials error.

## Test before deployment
```bash
python -m compileall -q app.py jobs.py matching.py database.py
python -m pytest -q
```

## Deploy to Render (GitHub)
1. Upload **all files** from this folder to a new private GitHub repository (keep the folder contents at the repository root).
2. On Render create a **Web Service** from the repository, Python runtime.
3. Build command: `pip install -r requirements.txt`.
4. Start command: `gunicorn 'app:app' --bind 0.0.0.0:$PORT --workers 2 --timeout 60`.
5. Add environment variables `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `SECRET_KEY`, `APP_USERNAME`, `APP_PASSWORD`, and `DATABASE_PATH`.
6. For persistent data, attach a persistent disk and set `DATABASE_PATH` to its mount location, e.g. `/data/careerpilot.db`. **Render persistent disks require a compatible paid service**; without a persistent disk, SQLite data may disappear on restart or redeploy. Use **one Gunicorn worker** with a single-instance disk deployment if required by your platform's storage model; the database uses SQLite transactions but is not shared across separate instances.
7. Visit `/health`, then the dashboard, then try a single import. Inspect logs if the API rejects credentials.

**Security:** Enable `APP_USERNAME` and `APP_PASSWORD` together for public hosting. Basic authentication must be served over HTTPS. It is a simple access barrier, not a multi-user account system. Keep credentials out of GitHub and avoid sharing logs containing secrets. Replace the default `SECRET_KEY` in production. The health endpoint is intentionally public.

## Configuration
- `ADZUNA_APP_ID` and `ADZUNA_APP_KEY`: Adzuna UK API credentials.
- `SECRET_KEY`: Flask signing key.
- `DATABASE_PATH`: SQLite path; defaults to `careerpilot.db` in project folder.
- `APP_USERNAME` and `APP_PASSWORD`: optional Basic Auth; set both.
- `PORT`: host-assigned port for Gunicorn.

## Notes
- Import runs synchronously and can take up to 40 seconds per selected category (two requests). The host timeout is 60 seconds; if searches are slow, retry or increase the timeout.
- UK-wide searches use no `where` filter; remote/hybrid is not reliably identified or filtered.
- This is a standalone rebuild. It does **not** migrate data from the old Careerpilot database automatically.
- The code uses parameterised SQL, Jinja autoescaping, bounded form fields, and request timeouts. For production use, add CSRF protection and stronger account/session management before exposing write endpoints broadly.

## Direct employer website search

The dashboard includes an **Employer websites** section. Select a supported employer and
enter a job title to open a search restricted to that employer's official domain.
This is a targeted external web search, **not** an automated crawler or a live
vacancy API. Search indexing can be incomplete or outdated; verify every vacancy
and closing date on the employer's official site. You can save a vacancy by
pasting its HTTPS URL, title, employer, and optional details. Employer vacancies
without verified salary data remain visible and are marked **Salary not verified**.
To add more employers, edit the `EMPLOYERS` mapping in `app.py` with verified
official domains. Do not add unverified third-party job boards as employers.

## Direct employer website search

Choose an employer and keywords in the new dashboard section. The app opens a
Google search restricted to the employer's official domain. This is **not**
a live vacancy API or automated scraping; search results may be incomplete or
outdated. Verify listings on the employer's own site. Paste a vacancy's HTTPS
URL and details to save it to Careerpilot. Jobs without salary data are marked
"Salary not verified" and are not automatically filtered out. Add verified
employer domains in the `EMPLOYERS` mapping in `app.py`.

## Automatic direct-employer feed import (new)

Set `EMPLOYER_BOARDS_JSON` in Render environment variables to a JSON list of **verified** public ATS board identifiers, for example:

```json
[{"name":"Your Verified Employer","provider":"greenhouse","board":"verified-board-id"},{"name":"Another Verified Employer","provider":"lever","board":"verified-lever-id"}]
```

Find the board identifier from the employer's actual careers URL and verify that its public Greenhouse or Lever feed responds before adding it. These names and IDs are placeholders, not verified employers. The dashboard **Import employer jobs** button checks all configured feeds, filters by optional keywords and location, scores against your CV and saves new listings without duplicates. Jobs with undisclosed salaries are retained and clearly marked unverified. This is a manual one-click refresh, not a background scheduler. A failed board is reported without blocking other boards.

**Coverage limits:** Greenhouse and Lever only. Many UK councils, NHS Jobs, Civil Service Jobs and law firms use different portals; retain the official-site search and manual-save options for them. No login, scraping or automatic applications. Verify deadlines and salary on the employer's listing. Render free services and ephemeral filesystems can lose SQLite data on redeploy; configure a persistent disk or managed database before relying on saved applications. Protect your dashboard with `APP_USERNAME` and `APP_PASSWORD` and use a strong `SECRET_KEY`.

## Official public-feed integration and scheduled checks

Careerpilot now supports **verified RSS/Atom vacancy feeds** from public employers, where available. There is **no universal public vacancy feed** for all councils, NHS Jobs or Civil Service Jobs. Do not enter a careers HTML page as an RSS feed. Use the existing official employer search for sites that do not publish feeds.

Configure `PUBLIC_JOB_FEEDS_JSON` with official, independently verified RSS/Atom URLs and explicitly allow their hostnames:

```text
PUBLIC_JOB_FEEDS_JSON=[{"name":"Example Council","url":"https://careers.example.gov.uk/jobs.rss"}]
PUBLIC_FEED_ALLOWED_HOSTS=careers.example.gov.uk
```

The URL above is illustrative, **not an actual verified feed**. Feeds are requested over HTTPS without redirects, capped in size and restricted to explicitly approved hostnames. The public feed importer supports RSS 2.0 and Atom, saves titles, descriptions and vacancy URLs, and leaves undisclosed salaries unverified. Some feeds may not include vacancy-specific information or might link to an aggregator; verify each listing.

### Scheduled daily import and deadline reminders

Set a strong `CRON_SECRET` in your hosting environment. Configure an external scheduler (for example, a Render cron job or GitHub Actions scheduled workflow) to send an HTTPS **POST** to `https://YOUR-APP.example/tasks/daily` with header `Authorization: Bearer YOUR_CRON_SECRET`. Never commit secrets to GitHub. The scheduled endpoint imports configured official public feeds and checks tracked deadlines. Greenhouse/Lever feeds still use the dashboard manual import. If no scheduler calls the endpoint, **nothing runs automatically**.

The dashboard displays application deadlines due within seven days and overdue deadlines. For optional email notifications set `ALERT_EMAIL_TO`, `SMTP_HOST`, `SMTP_PORT` (defaults to 465), `SMTP_FROM`, and, if needed, `SMTP_USER` and `SMTP_PASSWORD`. Notifications are sent at most once per day after successful SMTP delivery. Emails are not sent if SMTP settings are missing. Configure a persistent database to retain notification state and tracked jobs. Use your email provider's supported SMTP credentials and HTTPS for the scheduler.

**Deployment caution:** A public-facing single-user application should be hosted privately with strong credentials. The original codebase uses Basic Auth and has no CSRF protection; add CSRF protection before using it for sensitive production workflows. Automated scraping of employer websites and automatic application submissions are not implemented.
