
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from jobs import search_jobs, CATEGORIES, JobSearchError

MIN_SALARY = 27000
DAILY_TARGET = 10
LOCATION = "United Kingdom"

def run():
    now = datetime.now(ZoneInfo("Europe/London"))
    candidates = {}
    errors = []

    for category, queries in CATEGORIES.items():
        for query in queries:
            try:
                jobs = search_jobs(
                    query=query,
                    location=LOCATION,
                    count=50
                )
            except JobSearchError as exc:
                errors.append(f"{query}: {exc}")
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
                    "source_id": identifier,
                    "title": job.get("title"),
                    "company": job.get("company"),
                    "location": job.get("location"),
                    "url": job.get("url"),
                    "salary_max": salary,
                    "category": category,
                }

    shortlist = sorted(
        candidates.values(),
        key=lambda job: job["salary_max"],
        reverse=True
    )[:DAILY_TARGET]

    result = {
        "run_date": now.date().isoformat(),
        "timezone": "Europe/London",
        "target": DAILY_TARGET,
        "minimum_salary": MIN_SALARY,
        "location": LOCATION,
        "candidates_found": len(candidates),
        "shortlist": shortlist,
        "applications_submitted": 0,
        "errors": errors,
    }

    print(json.dumps(result, indent=2), flush=True)

    if errors and not candidates:
        raise RuntimeError("All searches failed or no candidates were returned; inspect errors above.")

if __name__ == "__main__":
    run()
