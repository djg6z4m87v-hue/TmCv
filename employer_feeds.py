"""Public employer ATS feeds. Only explicitly configured boards are queried.

No scraping, no bypass of access controls, no guessed board identifiers.
"""
import hashlib
import html
import json
import os
import re
from urllib.parse import urlparse
import requests

class FeedError(Exception):
    pass

DEFAULT_BOARDS = [
    # Add verified employer ATS board IDs to EMPLOYER_BOARDS_JSON.
    # Example: {"name":"Example Firm","provider":"greenhouse","board":"examplefirm"}
]

def configured_boards():
    raw = os.getenv('EMPLOYER_BOARDS_JSON', '[]')
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
        result.append({'name':name.strip(), 'provider':provider, 'board':board})
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
    session = session or requests
    provider, board, name = config['provider'],config['board'],config['name']
    endpoint = (f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true' if provider=='greenhouse'
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
        jobs.append({'source_id':f'{provider}:{board}:{ident}', 'title':str(title)[:200],
                     'company':name,'location':str(location)[:200],'category':'Direct employer',
                     'url':url,'description':description,'salary_min':None,'salary_max':None,
                     'salary_verified':0})
    return jobs

def relevant(job, keywords, location):
    terms=[x.strip().lower() for x in keywords.split(',') if x.strip()]
    haystack=(job['title']+' '+job['description']).lower()
    if terms and not any(term in haystack for term in terms):
        return False
    if location and location.lower() not in (job['location']+' '+job['description']).lower():
        return False
    return True
