# Careerpilot

Single-owner UK job search, application queue and supported submission worker.
Built with Flask, SQLite, Playwright Chromium and the OpenAI API.

## Application workflow

Open `/applications` from the vacancies dashboard.

1. Save your name, email, phone and a readable PDF/DOCX CV (maximum 5 MB).
2. Add your own OpenAI API key and explicitly authorise sending your CV, profile
   and vacancy descriptions to OpenAI. API usage is billed to that account.
   Keys entered in the app are encrypted using a key derived from `SECRET_KEY`.
   Keep that secret stable across deployments. An optional server-managed
   `OPENAI_API_KEY` overrides the saved connection.
3. Confirm your profile. Enable the worker. Choose automatic submission or
   individual material approval, and optionally daily discovery after 08:00 UK.
4. Add saved vacancies to the queue. Paste a direct employer link when a search
   result links to an aggregator. Include the full advert and verified starting
   salary. Minimum starting salary is £27,000; maximum is ten attempts per UK day.
5. The worker checks essential requirements against the CV and prepares a tailored
   cover letter. A low-level matching score is a heuristic, not a hiring prediction.
6. Standard **Lever-hosted forms** are supported at `jobs.lever.co` and
   `jobs.eu.lever.co`. The browser fills known contact fields, uploads the CV and
   letter, and uses only exact saved answers for other fields. It verifies that the
   displayed employer and job title match the saved vacancy before submitting.
7. Missing information, unsupported portals, login, declarations and human checks
   are handed back to the owner. It never solves a challenge or invents an answer.
   Narrative questions can be sent for grounded AI answer drafts by preparing the
   application again. Those drafts require explicit review/approval before use.
8. A visible employer confirmation is required before a browser submission is
   marked Submitted and the job status becomes Applied. The receipt records the
   confirmation, timestamp, URL, CV hash and material hash. There is no success
   claim based only on a click, HTTP response or shortlist.

Queue claims are SQLite transactions. Canonical URLs and job IDs are unique.
The worker persists an attempt **before** clicking Submit. A crash or timeout
after that point becomes Unknown; it is never automatically resubmitted. Check
email/employer records and record the confirmation or skip it. Manual status
changes elsewhere on the dashboard are owner records, not browser receipts.

Changing applicant details/CV invalidates prepared material fingerprints. Pausing
automation is rechecked at the final submission boundary. Daily limits include
uncertain outcomes. Browser requests are restricted to the provider and its
known assets. The browser does not inherit ChatGPT tools, logins or accounts.

This version does **not** automate every employer site. Greenhouse, Workday,
councils, NHS, Civil Service and other unsupported portals are manual handoffs.
An AI key and a confirmed complete profile are required before tailoring can
run. No live employer applications were submitted during development tests.

## Deploy / run

Production uses the Dockerfile: installs Chromium and its system dependencies,
runs all tests (including intercepted-browser fixtures), and launches
`python production.py`. That supervisor starts two Gunicorn web workers plus
one persistent application worker. It stops the container if either exits.
Use **one replica** with a persistent volume at `/data` and set
`DATABASE_PATH=/data/careerpilot.db`. SQLite is not shared across replicas.
The worker must run beside the web process to access the same persistent queue.

Required production variables:

- `SECRET_KEY`: strong stable Flask/encryption secret.
- `APP_USERNAME`, `APP_PASSWORD`: both required for private owner access over HTTPS.
- `DATABASE_PATH`: persistent SQLite location.
- `WEB_SEARCH_BACKEND`: optional DDGS engine selection; defaults to `bing,duckduckgo`. No search API key is required.
- `PORT`: assigned by the host (8080 default).

Optional: `OPENAI_API_KEY`, `OPENAI_MODEL` (defaults to `gpt-4.1-mini`).
No API secret, CV or applicant data belongs in the source repository.

Local development (Python 3.12 recommended):

```bash
pip install -r requirements.txt
python -m playwright install --with-deps chromium
export SECRET_KEY='a-local-development-secret'
python production.py
```

Use HTTPS for the application workspace: session cookies are Secure, HttpOnly
and SameSite=Lax. Every browser mutation has a CSRF token. Scheduler endpoints
use their existing separate bearer check. `/health` and installation assets
remain public; CVs, keys, materials and application records are not cached.

Tests:

```bash
python -m pytest -q
```

Browser fixtures intercept all requests and use fictional applicant data.
They check field filling, document upload, missing answers, declarations,
challenges and employer confirmation. Workflow tests cover encrypted secrets,
salary filtering, duplicate claims, pause, profile changes, unknown outcomes,
daily limits, CSRF and private downloads. No test submits a real application.

## Search and legacy integrations

The vacancies dashboard and background discovery search the web using DDGS.
Results are restricted to approved employer and employer-hosted recruitment domains.
Each candidate page is fetched with bounded requests. Only a single JobPosting
record can qualify for automatic import; category pages and pages without
structured salary data remain browse-only. Inaccessible and expired adverts are excluded. Annual GBP baseSalary data must contain a minimum meeting the saved
threshold before automatic import or queuing. Missing salaries are never estimated.
Open web search provides a browser fallback when an engine or employer site blocks
automated access. Search coverage is incomplete, and engines can rate-limit requests.
Adzuna is no longer called. Existing Adzuna jobs stay in storage but are hidden from
the active dashboard/queue picker; existing queued applications need a direct URL.

Daily application discovery searches one query per category, ranks CV keyword
matches and queues up to ten; this is a discovery cap, not a submission promise.
Agency/call-centre indicators and senior-role titles block automated submission.
The independent legacy `daily_runner.py` Railway cron service only logs a
shortlist. The web container's application worker owns the new persistent queue;
do not run additional worker replicas or point the old cron at a different DB.

For employer feeds, configure `EMPLOYER_BOARDS_JSON` with verified Greenhouse or
Lever board identifiers (objects containing `name`, `provider`, `board`). Public
feeds use `PUBLIC_JOB_FEEDS_JSON` with verified RSS/Atom URLs and
`PUBLIC_FEED_ALLOWED_HOSTS`. Undisclosed salaries remain unverified and require
manual verification before applying. No universal public-sector feed is claimed.
Official employer search and manual vacancy saving remain available.

Legacy `/tasks/daily` requires `Authorization: Bearer <CRON_SECRET>` and imports
configured public feeds plus optional deadline notifications. SMTP uses
`ALERT_EMAIL_TO`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`,
`SMTP_PASSWORD`; none are needed for browser application submission.

## iPhone

Open the HTTPS dashboard in Safari, then Share → Add to Home Screen. The
application workspace works in the Home Screen app. Browsing, downloads and
writes require internet; no personal data or writes are cached offline.
