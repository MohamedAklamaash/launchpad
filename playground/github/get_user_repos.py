"""
    Get all repos user ever made a commit
    Author: Mohamed Aklaamaash
    Date: 21/01/2026
"""

import os
import requests
import dotenv

dotenv.load_dotenv(
    dotenv_path="../../.env"
)

token = os.environ.get("GITHUB_TOKEN")
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json"
}

url = "https://api.github.com/user/repos"
params = {
    "visibility": "all",
    "affiliation": "owner,collaborator,organization_member",
    "per_page": 100,
    "page": 1
}

repos = []

while True:
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    data = r.json()
    if not data:
        break
    repos.extend(data)
    params["page"] += 1

for repo in repos:
    print(f"{repo['full_name']} | private={repo['private']} | permissions={repo['permissions']}")
