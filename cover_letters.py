"""CV-grounded local drafts, available without a paid AI connection."""
import re
from matching import tokens

THEMES = (
    ('casework', r'case|paralegal|litigation|legal', 'supporting accurate case preparation and administration'),
    ('communication', r'client|customer|communicat|correspond|interview', 'communicating clearly with clients and colleagues'),
    ('compliance', r'complian|regulat|confidential|data protection', 'handling information carefully and following procedures'),
    ('research', r'research|advice|advising', 'researching issues and explaining information clearly'),
    ('administration', r'administrat|record|deadline|excel|organis', 'keeping records organised and meeting deadlines'),
    ('recovery', r'debt|recover|payment|collection|revenu', 'supporting recovery work with a considered approach to customers'),
    ('contracts', r'contract|procure|commercial', 'supporting organised commercial administration'),
)


def draft(job, applicant):
    cv = applicant.get('cv_text', '').strip()
    name = applicant.get('name', '').strip()
    if len(cv) < 100 or not name:
        raise ValueError('Save your full name and current CV in Applications before generating a personalised letter.')
    description = re.sub(r'<[^>]+>', ' ', job.get('description', ''))
    if len(description.strip()) < 100:
        raise ValueError('Add the full vacancy description so the letter can connect your experience to this employer’s requirements.')
    # Evidence is copied from the saved CV, never inferred from the vacancy.
    chunks = [re.sub(r'^[\s•*\-]+', '', s).strip() for s in re.split(r'\n+|(?<=[.!?])\s+', cv)]
    chunks = [s for s in chunks if 35 <= len(s) <= 650 and not re.search(r'@|https?://|\b07\d{9}\b', s)]
    target = tokens(job.get('title', '') + ' ' + description)
    ranked = sorted(dict.fromkeys(chunks), key=lambda s: len(tokens(s) & target), reverse=True)
    evidence = [s for s in ranked if tokens(s) & target][:3]
    if not evidence:
        raise ValueError('The saved CV does not provide enough relevant evidence for this vacancy. Review the match before writing a letter.')
    selected = [purpose for _, pattern, purpose in THEMES if re.search(pattern, cv, re.I) and re.search(pattern, description, re.I)][:3]
    contribution = '; '.join(selected) or 'bringing the experience described in my CV to the advertised responsibilities'
    paragraphs = ['Dear Hiring Team,',
        f'I am applying for the {job["title"]} position at {job["company"]}. The advertised responsibilities interest me because they connect with my background and would give me the opportunity to contribute through {contribution}.']
    for fact in evidence:
        paragraphs.append('Relevant experience from my CV includes: “' + fact.rstrip('.') + '”.')
    # Use advert text explicitly as a quote, so it is never presented as applicant experience.
    requirements = [s.strip() for s in re.split(r'\n+|(?<=[.!?])\s+', description) if 40 <= len(s.strip()) <= 400]
    requirements.sort(key=lambda s: len(tokens(s) & tokens(' '.join(evidence))), reverse=True)
    if requirements:
        paragraphs.append(f'Your advert highlights: “{requirements[0]}” This is an aspect of the role I would be keen to support at {job["company"]}, drawing on the experience above and learning your team’s approach.')
    paragraphs += ['I would welcome the opportunity to discuss how my experience could support your team. Thank you for considering my application.', 'Yours faithfully,\n' + name]
    return '\n\n'.join(paragraphs)
