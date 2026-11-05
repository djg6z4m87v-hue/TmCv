"""Search official employer job boards without relying on public search engines."""
import copy
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as day_end
from urllib.parse import urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from lxml import html
from lxml.etree import ParserError

NOTTS = 'https://nottinghamshire.tal.net/vx/lang-en-GB/mobile-0/appcentre-ext/brand-8/candidate/jobboard/vacancy/1/adv/'
MJS = 'https://myjobscotland.gov.uk/search-jobs'
HOSTS = {'nottinghamshire.tal.net', 'myjobscotland.gov.uk', 'www.myjobscotland.gov.uk'}
_CACHE = {}
_LOCK = threading.Lock()


class SourceError(Exception):
    pass


def read(url):
    # These are fixed, official sources, not arbitrary URLs supplied by visitors.
    for _ in range(3):
        p = urlparse(url)
        if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443):
            raise SourceError('Unexpected employer link')
        try:
            with requests.get(url, timeout=(20, 10), stream=True, allow_redirects=False,
                              headers={'User-Agent': 'Careerpilot/1.0', 'Accept': 'text/html'}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('Location', ''))
                    continue
                if response.status_code != 200:
                    raise SourceError(f'HTTP {response.status_code}')
                body = bytearray()
                for block in response.iter_content(65536):
                    body.extend(block)
                    if len(body) > 2_000_000:
                        raise SourceError('Page too large')
                if not body:
                    raise SourceError('Empty page')
                return url, html.fromstring(bytes(body))
        except (requests.RequestException, ParserError, ValueError) as exc:
            raise SourceError(type(exc).__name__) from exc
    raise SourceError('Too many redirects')


def cached_read(url):
    with _LOCK:
        old = _CACHE.get(url)
        if old and time.monotonic() - old[0] < 600:
            return old[1][0], copy.deepcopy(old[1][1])
    value = read(url)
    with _LOCK:
        if len(_CACHE) > 100:
            _CACHE.clear()
        _CACHE[url] = (time.monotonic(), value)
    return value[0], copy.deepcopy(value[1])


def text(node):
    return ' '.join(' '.join(node.itertext()).split())


def topic(query):
    q = query.lower()
    if any(s in q for s in ('revenu', 'recover', 'debt', 'collect')):
        return ('revenu', 'debt recovery', 'recoveries', 'income', 'collection', 'council tax', 'finance', 'account'), '2087'
    if any(s in q for s in ('contract', 'procure')):
        return ('contract', 'procure', 'commercial'), '2781'
    if any(s in q for s in ('compliance', 'investigat', 'regulatory')):
        return ('compliance', 'investigat', 'regulatory', 'enforcement', 'governance'), '2636'
    if any(s in q for s in ('paralegal', 'legal case')):
        return ('legal', 'paralegal', 'casework', 'litigation'), '2093'
    return ('administrat', 'legal', 'clerical', 'casework', 'executive officer', 'democratic', 'governance'), '2080'


def annual_salary(value):
    # Read only a labelled salary field, never an estimated snippet or grade number.
    value = value.lower()
    if not re.search(r'per annum|per year|annually|annual', value) or re.search(r'pro[ -]?rata|hour|week|month', value):
        return None, None
    amounts = re.findall(r'£\s*([\d,]+(?:\.\d{1,2})?)', value)
    try:
        nums = [float(n.replace(',', '')) for n in amounts]
        if len(nums) not in (1, 2) or not 1000 < nums[0] <= nums[-1] < 1_000_000:
            return None, None
        return nums[0], nums[-1]
    except ValueError:
        return None, None


def closing_date(value):
    for fmt in ('%d %B %Y %H:%M', '%d %b %Y', '%d/%m/%Y', '%Y-%m-%d'):
        try:
            parsed = datetime.strptime(value.strip(), fmt)
            if '%H' not in fmt:
                parsed = datetime.combine(parsed.date(), day_end(23, 59, 59))
            return parsed.replace(tzinfo=ZoneInfo('Europe/London'))
        except ValueError:
            pass
    raise SourceError('Closing date unreadable')


def row(url, title, company, location, description, salary, deadline, source):
    if closing_date(deadline) < datetime.now(ZoneInfo('Europe/London')):
        return None
    if re.search(r'recruitment agency|on behalf of our client|call centre|call center|telesales', title + ' ' + company + ' ' + description, re.I):
        return None
    low, high = annual_salary(salary)
    return {'source_id': 'web:' + hashlib.sha256(url.encode()).hexdigest(), 'title': title[:200],
            'company': company[:200], 'location': location[:200], 'url': url,
            'description': description[:15000], 'salary_min': low, 'salary_max': high,
            'salary_verified': int(low is not None), 'deadline': closing_date(deadline).date().isoformat(),
            'source_name': source}


def notts_job(link):
    url, root = cached_read(link)
    container = root.xpath('//*[@id="vac_desc"]')
    title = root.xpath('//h1')
    if not container or not title:
        raise SourceError('Vacancy format changed')
    fields = {}
    for label in container[0].xpath('.//label[@role="term"]'):
        key = text(label)
        values = container[0].xpath('.//*[@role="definition" and @aria-labelledby=$label]', label=label.get('id'))
        if values:
            fields[key] = text(values[0])
    for node in container[0].xpath('.//script|.//style|.//form[contains(@class,"apply-form")]'):
        node.drop_tree()
    closing = re.search(r'Closing Date\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4}(?:\s+\d{2}:\d{2})?)', text(container[0]))
    deadline = fields.get('Closing Date') or (closing.group(1) if closing else '')
    return row(url, text(title[0]), 'Nottinghamshire County Council', fields.get('Work Base', 'Nottinghamshire'),
               fields.get('', text(container[0])), fields.get('Salary', ''), deadline, 'Nottinghamshire County Council')


def notts_search(query, location, count):
    if location not in ('Nottingham', 'United Kingdom'):
        return [], {'source': 'Nottinghamshire County Council', 'status': 'outside selected area', 'listed': 0}
    _, root = cached_read(NOTTS)
    listings = root.xpath('//tr[contains(@class,"search_res")]')
    if not listings and not re.search(r'0 Vacancies|No vacancies', text(root), re.I):
        raise SourceError('Vacancy list format changed')
    words, _ = topic(query)
    links = []
    for listing in listings:
        anchor = listing.xpath('.//a[contains(@href,"/candidate/so/")]')
        if anchor and any(word in text(anchor[0]).lower() for word in words):
            links.append(anchor[0].get('href'))
    jobs, failed = [], 0
    def inspect(link):
        try:
            return notts_job(link)
        except SourceError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        for job in pool.map(inspect, list(dict.fromkeys(links))[:min(count, 12)]):
            if job:
                jobs.append(job)
            else:
                failed += 1
    return jobs, {'source': 'Nottinghamshire County Council', 'status': 'available', 'listed': len(listings),
                  'matched': len(links), 'readable': len(jobs), 'unreadable_or_closed': failed}


def scotland_job(link):
    url, root = cached_read(link)
    from jobs import _postings, _salary, _text
    posts = []
    for script in root.xpath('//script[@type="application/ld+json"]/text()'):
        try:
            posts.extend(_postings(json.loads(script)))
        except (ValueError, TypeError):
            pass
    if len(posts) != 1:
        raise SourceError('Vacancy format changed')
    post = posts[0]
    expires = str(post.get('validThrough', ''))[:10]
    place = post.get('jobLocation') or {}
    if isinstance(place, list):
        place = place[0] if place else {}
    address = place.get('address', {}) if isinstance(place, dict) else {}
    location = ', '.join(str(address.get(k) or '') for k in ('addressLocality', 'addressRegion') if address.get(k)) or 'Scotland'
    for data in root.xpath('//script[@type="application/json"]/text()'):
        try:
            config = json.loads(data)
            mapped = config.get('jobMapLocations', [])
            if mapped:
                places = json.loads(mapped[0]).get('locations', [])
                if places:
                    location = places[0].get('display_name') or places[0].get('locality') or location
        except (ValueError, TypeError, AttributeError, IndexError):
            pass
    organisation = post.get('hiringOrganization') or {}
    job = row(url, _text(post.get('title', '')), _text(organisation.get('name', '')), location,
              _text(post.get('description', '')), '', expires, 'myjobscotland — council vacancies')
    if job:
        low, high = _salary(post)
        job.update(salary_min=low, salary_max=high, salary_verified=int(low is not None))
    return job


def scotland_search(query, location, count):
    if location != 'United Kingdom':
        return [], {'source': 'myjobscotland', 'status': 'outside selected area', 'listed': 0}
    words, category = topic(query)
    url = MJS + '?' + urlencode({'categories': category})
    _, root = cached_read(url)
    anchors = root.xpath('//a[contains(@href,"/jobs/")]')
    links = [a.get('href') for a in anchors if any(w in text(a).lower() for w in words)]
    links.sort(key=lambda link: sum(w in next((text(a).lower() for a in anchors if a.get('href') == link), '') for w in words) - 3 * bool(re.search(r'manager|head |director|senior', link, re.I)), reverse=True)
    jobs = []
    def inspect(link):
        try:
            return scotland_job(link)
        except (SourceError, TypeError, ValueError, AttributeError):
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [j for j in pool.map(inspect, list(dict.fromkeys(links))[:min(count, 12)]) if j]
    return jobs, {'source': 'myjobscotland', 'status': 'available', 'listed': len(anchors),
                  'matched': len(links), 'readable': len(jobs)}


def search(query, location, count=20):
    results, diagnostics = {}, []
    providers = [notts_search, scotland_search]
    def run(provider):
        try:
            return provider(query, location, count)
        except SourceError as exc:
            return [], {'source': provider.__name__, 'status': 'unavailable', 'reason': str(exc)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for jobs, status in pool.map(run, providers):
            diagnostics.append(status)
            for job in jobs:
                results[job['source_id']] = job
    if not results and location != 'United Kingdom':
        try:
            broader, status = scotland_search(query, 'United Kingdom', count)
            status['scope'] = 'UK-wide alternatives'
            diagnostics.append(status)
            for job in broader:
                job['search_scope'] = 'UK-wide alternative'
                results[job['source_id']] = job
        except SourceError:
            diagnostics.append({'source': 'myjobscotland', 'status': 'unavailable', 'scope': 'UK-wide alternatives'})
    return list(results.values()), diagnostics
