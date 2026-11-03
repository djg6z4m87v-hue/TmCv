
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from jobs import search_jobs, CATEGORIES

MIN_SALARY = 27000
DAILY_TARGET = 10

def run():
    now = datetime.now(ZoneInfo("Europe/London"))
    candidates = {}

    for category, queries in CATEGORIES.items():
        for query in queries:
            try:
                jobs = search_jobs(
                    query=query,
                    location="",
                    minimum=MIN_SALARY,
                    count=50
                )
            except Exception as exc:
                print(
                    f"Search failed for {query}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True
                )
                continue

            for job in jobs:
                salary = job.get("salary_max")
                if not job.get("salary_verified"):
                    continue
                if salary is None or salary < MIN_SALARY:
                    continue

                identifier = job.get("source_id") or job.get("url")
                if not identifier:
                    continue

                candidates[identifier] = {
                    "title": job.get("title"),
                    "company": job.get("company"),
                    "location": job.get("location"),
                    "url": job.get("url"),
                    "salary_max": salary,
                    "category": category,
                }

    shortlist = list(candidates.values())

    print(json.dumps({
        "run_date": now.date().isoformat(),
        "target": DAILY_TARGET,
        "candidates_found": len(shortlist),
        "shortlist": shortlist[:DAILY_TARGET],
        "applications_submitted": 0,
    }, indent=2), flush=True)

if __name__ == "__main__":
    run()
