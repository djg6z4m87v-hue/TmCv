"""Public employer ATS feeds. Only explicitly configured boards are queried.

No scraping, no bypass of access controls, no guessed board identifiers.
"""
import hashlib
import html
import json
import os
import re
import time
import threading
import copy
from urllib.parse import urlparse
import requests

_BOARD_CACHE = {}
_BOARD_LOCK = threading.Lock()


class FeedError(Exception):
    pass

# Verified official employer boards; override or extend via EMPLOYER_BOARDS_JSON.
DEFAULT_BOARDS = [
    {'name': 'Monzo', 'provider': 'greenhouse', 'board': 'monzo'},
    {'name': 'GoCardless', 'provider': 'greenhouse', 'board': 'gocardless'},
    {'name': 'Modulr', 'provider': 'greenhouse', 'board': 'modulrfinance'},
    {'name': 'HSPG', 'provider': 'greenhouse', 'board': 'hspg'},
]

def configured_boards():
    raw = os.getenv('EMPLOYER_BOARDS_JSON', json.dumps(DEFAULT_BOARDS))
    try:
        boards = json.loads(raw)
    except ValueError as exc:
        raise FeedError('EMPLOYER_BOARDS_JSON is not valid JSON') from exc
    if not isinstance(boards, list) or len(boards) > 40:
        raise FeedError('Provide a list of up to 40 employer boards')
    result = []
    for item in boards:
        if not isinstance(item, dict):
            raise FeedError('Each employer board must be an object')
        name, provider, board = item.get('name'), item.get('provider'), item.get('board')
        if not isinstance(name, str) or not name.strip() or len(name)>100 or provider not in ('greenhouse','lever') or not isinstance(board,str) or not re.fullmatch(r'[A-Za-z0-9_-]{2,100}',board):
            raise FeedError('Board requires a name, greenhouse/lever provider, and valid board ID')
        entry = {'name':name.strip(), 'provider':provider, 'board':board}
        if item.get('region') == 'eu':
            entry['region'] = 'eu'
        result.append(entry)
    return result

def plain(value):
    value = re.sub(r'<[^>]*>', ' ', str(value or ''))
    return re.sub(r'\s+', ' ', html.unescape(value)).strip()[:5000]

def safe_url(value):
    if not isinstance(value,str):
        return False
    u = urlparse(value)
    return u.scheme=='https' and bool(u.hostname) and not u.username and not u.password

def fetch_board(config, session=None):
    cache_key = json.dumps(config, sort_keys=True)
    if session is None:
        with _BOARD_LOCK:
            old = _BOARD_CACHE.get(cache_key)
            if old and time.monotonic() - old[0] < 600:
                return copy.deepcopy(old[1])
    use_cache = session is None
    session = session or requests
    provider, board, name = config['provider'],config['board'],config['name']
    api_host = 'boards-api.eu.greenhouse.io' if config.get('region') == 'eu' else 'boards-api.greenhouse.io'
    endpoint = (f'https://{api_host}/v1/boards/{board}/jobs?content=true&pay_transparency=true' if provider=='greenhouse'
                else f'https://api.lever.co/v0/postings/{board}?mode=json')
    try:
        response = session.get(endpoint,timeout=15,headers={'Accept':'application/json','User-Agent':'Careerpilot/1.0'})
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException,ValueError) as exc:
        raise FeedError(f'{name}: {type(exc).__name__} fetching public {provider} feed') from exc
    entries = data.get('jobs',[]) if provider=='greenhouse' and isinstance(data,dict) else data
    if not isinstance(entries,list):
        raise FeedError(f'{name}: unexpected feed response')
    jobs=[]
    for item in entries[:500]:
        if not isinstance(item,dict):
            continue
        if provider=='greenhouse':
            title=item.get('title'); url=item.get('absolute_url'); ident=item.get('id')
            location=(item.get('location') or {}).get('name','')
            description=plain(item.get('content'))
        else:
            title=item.get('text'); url=item.get('hostedUrl'); ident=item.get('id')
            location=(item.get('categories') or {}).get('location','')
            description=plain(item.get('descriptionPlain') or item.get('description'))
        if not ident or not title or not safe_url(url):
            continue
        low, high = published_salary(item, description)
        jobs.append({'source_id':f'{provider}:{board}:{ident}', 'title':str(title)[:200],
                     'company':name,'location':str(location)[:200],'category':'Direct employer',
                     'url':url,'description':description,'salary_min':low,'salary_max':high,
                     'salary_verified':int(low is not None)})
    if use_cache:
        with _BOARD_LOCK:
            _BOARD_CACHE[cache_key] = (time.monotonic(), copy.deepcopy(jobs))
    return jobs

def relevant(job, keywords, location):
    terms=[x.strip().lower() for x in keywords.split(',') if x.strip()]
    haystack=(job['title']+' '+job['description']).lower()
    if terms and not any(term in haystack for term in terms):
        return False
    if location and location.lower() not in (job['location']+' '+job['description']).lower():
        return False
    return True


def published_salary(item, description):
    """Accept only explicitly annual GBP amounts from the employer payload."""
    import math
    ranges = item.get('pay_input_ranges') or []
    for value in ranges:
        if value.get('currency_type') != 'GBP' or str(value.get('unit', '')).lower() not in ('year', 'annual', 'annually'):
            continue
        try:
            low, high = float(value['min_cents']) / 100, float(value['max_cents']) / 100
            if math.isfinite(low) and math.isfinite(high) and 1000 < low <= high < 1_000_000:
                return low, high
        except (KeyError, ValueError, TypeError):
            pass
    # Source is the employer's full advert, never a predicted search snippet.
    from employer_web import annual_salary
    match = re.search(r'(?:salary|pay range)\s*[:–-]?\s*(£[^.;\n]{0,100}(?:per annum|per year|annually|annual))', description, re.I)
    return annual_salary(match.group(1)) if match else (None, None)


def search_boards(query, location, count=20):
    from concurrent.futures import ThreadPoolExecutor
    from employer_web import topic
    words, _ = topic(query)
    boards = configured_boards()
    diagnostics, found = [], []
    def read_board(board):
        try:
            rows = fetch_board(board)
            matches = []
            for job in rows:
                title, place = job['title'].lower(), job['location'].lower()
                if re.search(r'\b(senior|lead|head|director|manager|counsel|engineer|scientist)\b', title):
                    continue
                if not any(word in title for word in words):
                    continue
                if location == 'United Kingdom':
                    if not re.search(r'\buk\b|united kingdom|england|scotland|wales|london|nottingham|derby|leicester|birmingham|leeds|manchester|cardiff|edinburgh|bristol', place):
                        continue
                elif location.lower() not in place and not re.search(r'remote.*(?:uk|united kingdom)|(?:uk|united kingdom).*remote', place):
                    continue
                if re.search(r'recruitment agency|on behalf of our client|call centre|call center|telesales', job['description'], re.I):
                    continue
                matches.append(job)
            return matches[:count], {'source': board['name'], 'status': 'available', 'listed': len(rows), 'matched': len(matches)}
        except FeedError:
            return [], {'source': board['name'], 'status': 'unavailable'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for rows, status in pool.map(read_board, boards):
            found.extend(rows)
            diagnostics.append(status)
    return found, diagnostics
