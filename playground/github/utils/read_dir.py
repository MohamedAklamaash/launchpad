import requests

def list_repo_tree(owner, repo, path, headers, indent=0):
    r = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
        headers=headers
    )
    r.raise_for_status()
    for item in r.json():
        print("  " * indent + item["name"])
        if item["type"] == "dir":
            list_repo_tree(owner, repo, item["path"], headers, indent + 1)