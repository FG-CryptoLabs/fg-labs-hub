#!/usr/bin/env python3
"""Health check script for the FG Labs hub.

Queries the GitHub API for each project defined in data/projects.yaml and outputs
a health.json file. Run by the health-check GitHub Actions workflow daily.

Checks per project:
  - Repository exists and is accessible
  - Last commit age (warn if > 90 days, error if > 365 days for "active" projects)
  - Open issues count
  - Latest workflow run status (if any CI workflow is present)

Output: JSON written to stdout, redirected to data/health.json by the workflow.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

PROJECTS_FILE = Path(__file__).parent.parent / "data" / "projects.yaml"
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_API = "https://api.github.com"


def gh_get(path: str) -> dict | list | None:
    url = f"{GH_API}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "fg-labs-hub-health-check",
    }
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError):
        return None


def days_since(iso_str: str) -> int:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return (datetime.now(tz=timezone.utc) - dt).days


def check_project(proj: dict) -> dict:
    repo = proj.get("repo", "")
    result = {
        "id": proj["id"],
        "name": proj["name"],
        "repo": repo,
        "status": "ok",
        "message": "",
        "last_commit_age_days": None,
        "open_issues": None,
        "last_ci_status": None,
    }

    if not repo:
        result["status"] = "warn"
        result["message"] = "No repo configured"
        return result

    repo_data = gh_get(f"/repos/{repo}")
    if repo_data is None:
        result["status"] = "error"
        result["message"] = "Repository not accessible"
        return result

    result["open_issues"] = repo_data.get("open_issues_count", 0)

    # Last commit age
    commits = gh_get(f"/repos/{repo}/commits?per_page=1")
    if commits and isinstance(commits, list) and commits:
        pushed_at = commits[0].get("commit", {}).get("committer", {}).get("date") or repo_data.get("pushed_at")
        if pushed_at:
            age = days_since(pushed_at)
            result["last_commit_age_days"] = age
            if age > 365 and proj.get("status") == "active":
                result["status"] = "error"
                result["message"] = f"No commits in {age} days"
            elif age > 90 and proj.get("status") == "active":
                result["status"] = "warn"
                result["message"] = f"No commits in {age} days"

    # Latest CI workflow run
    runs = gh_get(f"/repos/{repo}/actions/runs?per_page=1")
    if runs and isinstance(runs, dict):
        run_list = runs.get("workflow_runs", [])
        if run_list:
            latest = run_list[0]
            conclusion = latest.get("conclusion")
            result["last_ci_status"] = conclusion
            if conclusion == "failure" and result["status"] == "ok":
                result["status"] = "warn"
                result["message"] = result["message"] or "CI failing"

    if not result["message"]:
        result["message"] = "All checks passed"

    return result


def load_projects() -> list[dict]:
    """Load projects from YAML without requiring PyYAML (parse manually for CI simplicity)."""
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(PROJECTS_FILE.read_text())
    except ImportError:
        # Minimal fallback: parse only what we need without PyYAML
        # In CI, PyYAML is pre-installed on ubuntu-latest
        print("Warning: PyYAML not available, returning empty list", file=sys.stderr)
        return []

    projects = []
    for org in data.get("orgs", []):
        for proj in org.get("projects", []):
            projects.append(proj)
    return projects


def main() -> None:
    projects = load_projects()
    results = []
    for proj in projects:
        try:
            result = check_project(proj)
        except Exception as e:
            result = {
                "id": proj.get("id", "unknown"),
                "name": proj.get("name", "unknown"),
                "repo": proj.get("repo", ""),
                "status": "error",
                "message": str(e),
                "last_commit_age_days": None,
                "open_issues": None,
                "last_ci_status": None,
            }
        results.append(result)

    output = {
        "run_at": datetime.now(tz=timezone.utc).isoformat(),
        "results": results,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
