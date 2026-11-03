"""Adzuna UK integration. Estimated salaries are NEVER treated as verified."""
import os
import requests

API = 'https://api.adzuna.com/v1/api/jobs/gb/search/1'
CATEGORIES = {
    'Legal administration': ['legal administrator', 'legal support officer'],
    'Paralegal / casework': ['paralegal', 'legal caseworker'],
    'Compliance': ['compliance officer', 'compliance coordinator'],
    'Contracts': ['contracts administrator', 'contracts officer'],
    'Investigations': ['investigations officer', 'regulatory officer'],
    'Revenues / recovery': ['revenues officer', 'debt recovery officer'],
    'Public sector': ['council administrator', 'public sector caseworker'],
}
LOCATIONS = ['Nottingham', 'Derby', 'Leicester', 'Birmingham', 'London', 'United Kingdom']

class JobSearchError(Exception):
    pass

def search_jobs(query, location, count=20):
    app_id, key = os.getenv('ADZUNA_APP_ID'), os.getenv('ADZUNA_APP_KEY')
    if not app_id or not key:
        raise JobSearchError('Adzuna credentials missing. Configure ADZUNA_APP_ID and ADZUNA_APP_KEY.')
    params = {'app_id': app_id, 'app_key': key, 'what': query, 'results_per_page': min(count, 50), 'content-type': 'application/json'}
    if location != 'United Kingdom':
        params['where'] = location
    try:
        response = requests.get(API, params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 'unknown'
        labels = {400: 'Invalid search', 401: 'Invalid API credentials', 403: 'API access denied', 429: 'API rate limit exceeded'}
        raise JobSearchError(f'Adzuna HTTP {status}: {labels.get(status, "Request failed")}') from exc
    except (requests.RequestException, ValueError) as exc:
        raise JobSearchError(f'Adzuna request failed: {type(exc).__name__}') from exc
    jobs = []
    for j in data.get('results', []):
        if not j.get('id') or not j.get('title') or not j.get('redirect_url'):
            continue
        # Adzuna salary_is_predicted is typically '1'/'0' or bool; absent is not verified.
        predicted = j.get('salary_is_predicted')
        verified = predicted in (0, '0', False) and predicted is not None
        jobs.append({
            'source_id': 'adzuna:' + str(j['id']), 'title': j['title'],
            'company': (j.get('company') or {}).get('display_name', ''),
            'location': (j.get('location') or {}).get('display_name', location),
            'url': j['redirect_url'], 'description': j.get('description') or '',
            'salary_min': j.get('salary_min') if verified else None,
            'salary_max': j.get('salary_max') if verified else None,
            'salary_verified': int(verified),
        })
    return jobs
