import os
import requests
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, PlainTextResponse
import dotenv
import random

from utils.read_dir import list_repo_tree
dotenv.load_dotenv(
    dotenv_path="../../.env"
)

app = FastAPI()

CLIENT_ID = os.environ["GITHUB_CLIENT_ID"]
CLIENT_SECRET = os.environ["GITHUB_CLIENT_SECRET"]
REDIRECT_URI = os.environ["GITHUB_REDIRECT_URI"]


@app.get("/login/github")
def login_github():
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        "&scope=repo%20read:org"
    )
    return RedirectResponse(url)

@app.get("/callback/github")
def callback_github(request: Request):
    code = request.query_params.get("code")
    token_res = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
    )
    token_res.raise_for_status()
    token = token_res.json().get("access_token")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    repos = []
    page = 1
    while True:
        r = requests.get(
            "https://api.github.com/user/repos",
            headers=headers,
            params={"visibility": "all", "affiliation": "owner,collaborator,organization_member", "per_page": 100, "page": page},
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        repos.extend(data)
        page += 1

    output = []
    for repo in repos:
        line = f"{repo['full_name']} | private={repo['private']} | permissions={repo['permissions']}"
        print(line)
        output.append(line)
    
    repo = random.choice(repos)
    owner = repo["owner"]["login"]
    name = repo["name"]

    print(f"\nSelected repo: {owner}/{name}\n")
    list_repo_tree(owner, name, "", headers)
    # users are github authenticated , so redirect to a proper page in client if needed anytime later
    return PlainTextResponse("\n".join(output))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
