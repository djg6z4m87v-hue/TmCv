"""Optional public RSS/Atom vacancy feeds, configured with verified official URLs."""
import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import hashlib
import requests

class PublicFeedError(Exception):
    pass

def configured_public_feeds():
    try:
        data = json.loads(os.getenv('PUBLIC_JOB_FEEDS_JSON', '[]'))
    except ValueError as exc:
        raise PublicFeedError('PUBLIC_JOB_FEEDS_JSON is invalid JSON') from exc
    if not isinstance(data, list) or len(data) > 30:
        raise PublicFeedError('PUBLIC_JOB_FEEDS_JSON must be a list of up to 30 feeds')
    feeds = []
    for entry in data:
        if not isinstance(entry, dict):
            raise PublicFeedError('Each feed must be an object')
        url = str(entry.get('url', ''))
        parsed = urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise PublicFeedError('Feeds require verified HTTPS URLs')
        # Explicit allowlist reduces SSRF exposure from configurable feed URLs.
        allowed = [x.strip().lower() for x in os.getenv('PUBLIC_FEED_ALLOWED_HOSTS', '').split(',') if x.strip()]
        if not allowed or parsed.hostname.lower() not in allowed:
            raise PublicFeedError('Add each verified feed hostname to PUBLIC_FEED_ALLOWED_HOSTS')
        feeds.append({'name': str(entry.get('name', 'Official feed'))[:100], 'url': url, 'host': parsed.hostname.lower()})
    return feeds

def _tag(element):
    return element.tag.rsplit('}', 1)[-1]

def _child_text(element, name):
    for child in element:
        if _tag(child) == name:
            return ''.join(child.itertext()).strip()
    return ''

def fetch_public_feed(feed):
    try:
        response = requests.get(feed['url'], timeout=12, allow_redirects=False, headers={'User-Agent': 'Careerpilot/1.0 (public vacancy feed reader)'})
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise PublicFeedError('Feed is too large')
        root = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError) as exc:
        raise PublicFeedError(f"{feed['name']}: feed unavailable or invalid XML") from exc
    entries = [el for el in root.iter() if _tag(el) in ('item', 'entry')]
    jobs = []
    for entry in entries[:150]:
        title = _child_text(entry, 'title')[:200]
        url = _child_text(entry, 'link')
        if not url:
            for el in entry:
                if _tag(el) == 'link' and el.attrib.get('href'):
                    url = el.attrib['href']
                    break
        parsed = urlparse(url)
        if not title or parsed.scheme != 'https' or not parsed.hostname:
            continue
        description = (_child_text(entry, 'description') or _child_text(entry, 'summary') or _child_text(entry, 'content'))[:5000]
        jobs.append({'source_id': 'feed:' + hashlib.sha256(url.encode()).hexdigest(), 'title': title,
                     'company': feed['name'], 'location': '', 'category': 'Official public feed',
                     'url': url, 'description': description, 'salary_min': None, 'salary_max': None,
                     'salary_verified': 0})
    return jobs
