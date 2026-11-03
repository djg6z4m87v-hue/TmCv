import re

DEFAULT_CV = '''LLB Law 2:1 University of Derby; legal administration, debt recovery,
case management, client communication, legal research, compliance, Excel,
Word, case preparation, housing, employment, consumer advice, correspondence.'''

STOP = {'the','and','with','for','from','that','this','will','your','you','are','our','have','into','their','they','can','job','role','work','team','experience','required','about','including'}

def tokens(text):
    return {w for w in re.findall(r'[a-z]{3,}', (text or '').lower()) if w not in STOP}

def score(job, cv=DEFAULT_CV):
    cv_words = tokens(cv)
    target = tokens((job.get('title') or '') + ' ' + (job.get('description') or '')[:2500])
    if not target:
        return 0
    overlap = len(cv_words & target)
    title_overlap = len(cv_words & tokens(job.get('title') or ''))
    return min(100, round(100 * (0.7 * overlap / max(1, min(len(cv_words), 16)) + 0.3 * title_overlap / max(1, len(tokens(job.get('title') or ''))))))
