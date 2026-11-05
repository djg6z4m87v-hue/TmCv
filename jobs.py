"""Web discovery of direct employer vacancies; no Adzuna integration."""
import hashlib
import html
import ipaddress
import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, urlunparse

import requests
from ddgs import DDGS
from lxml import html as html_parser
from lxml.etree import ParserError

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
# Employer domains and employer-hosted recruitment platforms. No job aggregators.
EMPLOYER_DOMAINS = (
    'gov.uk', 'jobs.nhs.uk', 'nhs.scot', 'trac.jobs', 'jobs.lever.co',
    'jobs.eu.lever.co', 'boards.greenhouse.io', 'job-boards.greenhouse.io',
    'myworkdayjobs.com', 'brownejacobson.com', 'flintbishop.co.uk',
    'geldards.com', 'bradysolicitors.com', 'chattertons.com', 'potterclarkson.com',
)
EXCLUDED = ('recruitment agency', 'on behalf of our client', 'recruitment services',
            'call centre', 'call center', 'telesales')
_CACHE = {}
_LOCK = threading.Lock()


class JobSearchError(Exception):
    pass


class SearchResults(list):
    def __init__(self, rows=(), diagnostics=()):
        super().__init__(rows)
        self.diagnostics = list(diagnostics)


def direct_url(value):
    """Accept only HTTPS URLs on approved employer/recruitment hosts."""
    try:
        p = urlparse(value)
        host = (p.hostname or '').lower()
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            return None
        if not any(host == d or host.endswith('.' + d) for d in EMPLOYER_DOMAINS):
            return None
        # Keep application query identifiers; remove fragments only.
        return urlunparse(('https', host, p.path or '/', '', p.query, ''))
    except (TypeError, ValueError):
        return None


def query_text(query, location):
    terms = f'{query[:150]} {location[:100]} vacancy jobs'
    return terms + ' -site:adzuna.co.uk -site:adzuna.com -site:indeed.com -site:reed.co.uk -site:totaljobs.com -recruitment -agency'


def browser_search_url(query, location):
    return 'https://www.google.com/search?' + urlencode({'q': query_text(query, location)})


def _public_host(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (OSError, ValueError):
        return False


def fetch_page(url):
    """Bounded GETs; validate each redirect and never forward credentials."""
    for _ in range(4):
        url = direct_url(url)
        if not url or not _public_host(urlparse(url).hostname):
            return None
        try:
            with requests.get(url, timeout=(3, 6), allow_redirects=False, stream=True,
                              headers={'User-Agent': 'Careerpilot/1.0 vacancy reader', 'Accept': 'text/html'}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers.get('Location', ''))
                    continue
                if response.status_code != 200 or 'html' not in response.headers.get('Content-Type', '').lower():
                    return None
                body = bytearray()
                for block in response.iter_content(32768):
                    body.extend(block)
                    if len(body) > 1_500_000:
                        return None
                return url, bytes(body)
        except requests.RequestException:
            return None
    return None


def _postings(value):
    if isinstance(value, list):
        for item in value:
            yield from _postings(item)
    elif isinstance(value, dict):
        kind = value.get('@type', [])
        if kind == 'JobPosting' or isinstance(kind, list) and 'JobPosting' in kind:
            yield value
        yield from _postings(value.get('@graph', []))


def _text(value):
    if not isinstance(value, str) or not value.strip():
        return ''
    try:
        return ' '.join(html_parser.fromstring(value).text_content().split())
    except (ValueError, TypeError, ParserError):
        return html.unescape(value).strip()


def _salary(post):
    salary = post.get('baseSalary')
    if not isinstance(salary, dict) or salary.get('currency') != 'GBP':
        return None, None
    value = salary.get('value')
    if not isinstance(value, dict) or str(value.get('unitText', '')).upper() not in ('YEAR', 'ANNUAL'):
        return None, None
    try:
        low = float(value.get('minValue', value.get('value')))
        high = float(value.get('maxValue', value.get('value', low)))
        import math
        if not math.isfinite(low) or not math.isfinite(high) or low <= 0 or high < low:
            return None, None
        return low, high
    except (TypeError, ValueError):
        return None, None


def _inspect(result, location):
    url = direct_url(result.get('href', ''))
    if not url:
        return None
    title, description = _text(result.get('title', '')), _text(result.get('body', ''))
    if not title or any(word in (title + ' ' + description).lower() for word in EXCLUDED):
        return None
    page = fetch_page(url)
    if not page:
        return None  # Never offer a known broken or inaccessible page as a vacancy.
    url, body = page
    low = high = None
    company = urlparse(url).hostname
    try:
        root = html_parser.fromstring(body)
        postings = []
        for script in root.xpath('//script[@type="application/ld+json"]/text()'):
            try:
                postings.extend(_postings(json.loads(script)))
            except (ValueError, TypeError):
                continue
        # Category/search pages may list many jobs. Keep these out of the shortlist.
        if len(postings) != 1:
            return {'source_id': 'web:' + hashlib.sha256(url.encode()).hexdigest(),
                    'title': title[:200], 'company': company, 'location': location,
                    'url': url, 'description': description[:15000], 'salary_min': None,
                    'salary_max': None, 'salary_verified': 0, 'result_kind': 'Employer page'}
        post = postings[0]
        expires = post.get('validThrough')
        if expires:
            try:
                end = datetime.fromisoformat(expires.replace('Z', '+00:00'))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                if end < datetime.now(timezone.utc):
                    return None
            except (ValueError, TypeError, AttributeError):
                return None
        title = _text(post.get('title')) or title
        description = _text(post.get('description'))
        organisation = post.get('hiringOrganization') or {}
        if isinstance(organisation, dict):
            company = _text(organisation.get('name')) or company
        place = post.get('jobLocation') or {}
        if isinstance(place, list):
            place = place[0] if place else {}
        address = place.get('address', {}) if isinstance(place, dict) else {}
        if isinstance(address, dict):
            location = ', '.join(str(address.get(k) or '') for k in ('addressLocality', 'addressRegion') if address.get(k)) or location
        low, high = _salary(post)
    except (ValueError, TypeError, KeyError, ParserError):
        return None
    if any(word in (title + ' ' + company + ' ' + description).lower() for word in EXCLUDED):
        return None
    return {'source_id': 'web:' + hashlib.sha256(url.encode()).hexdigest(), 'title': title[:200],
            'company': company[:200], 'location': location[:200], 'url': url,
            'description': description[:15000], 'salary_min': low, 'salary_max': high,
            'salary_verified': int(low is not None)}


def general_web_search(query, location, count=20):
    """Optional general-engine discovery. Official boards are the default source."""
    engine = DDGS(timeout=8)
    results = engine.text(query_text(query, location), region='uk-en', max_results=min(count, 20),
                          backend=os.getenv('WEB_SEARCH_BACKEND', 'bing,duckduckgo'))
    candidates, seen = [], set()
    for result in results:
        url = direct_url(result.get('href', ''))
        if url and url not in seen:
            seen.add(url)
            candidates.append(result)
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [job for job in pool.map(lambda r: _inspect(r, location), candidates[:8]) if job]


def search_jobs(query, location, count=20):
    """Read official employer vacancy pages. Return provider status separately."""
    from employer_web import search
    rows, diagnostics = search(query[:150], location[:100], min(max(count, 1), 20))
    if os.getenv('ENABLE_GENERAL_WEB_SEARCH') == '1':
        try:
            rows.extend(general_web_search(query, location, count))
            diagnostics.append({'source': 'General web engines', 'status': 'available'})
        except Exception:
            diagnostics.append({'source': 'General web engines', 'status': 'unavailable'})
    available = [d for d in diagnostics if d['status'] == 'available']
    applicable = [d for d in diagnostics if d['status'] != 'outside selected area']
    if not available:
        if not applicable:
            raise JobSearchError('No connected employer source covers this location yet. Choose Nottingham or United Kingdom, or use Open web search.')
        raise JobSearchError('The connected employer websites could not be reached. This is a source failure, not a result of your salary filter. Try Open web search.')
    unique = {r['source_id']: r for r in rows}
    return SearchResults(unique.values(), diagnostics)


def startup_search_check():
    """Read-only production check of the same search used by the dashboard."""
    import logging
    for query, location in [('legal administrator', 'Nottingham'), ('revenues officer', 'United Kingdom')]:
        try:
            rows = search_jobs(query, location, count=6)
            logging.getLogger('applications').info('Live search check: location=%s roles=%s salary_verified=%s sources=%s',
                location, len(rows), sum(bool(r['salary_verified']) for r in rows),
                json.dumps(rows.diagnostics, sort_keys=True))
        except Exception as exc:
            logging.getLogger('applications').warning('Live search check failed: location=%s error=%s', location, str(exc))
