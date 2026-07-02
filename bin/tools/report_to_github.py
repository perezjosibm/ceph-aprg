import sys
import requests

# --- CONFIGURATION ---
# Set your GitHub Personal Access Token (PAT)
GITHUB_TOKEN = "your_github_pat_token_here"
REPO = "ceph/ceph"

# Unique hidden HTML tag to identify your script's specific comment
COMMENT_MARKER = ""
# ---------------------

if len(sys.argv) < 2:
    print("Usage: python report_to_github.py <PR_NUMBER>")
    sys.exit(1)

PR_NUMBER = sys.argv[1]

# Translate your Redmine layout into GitHub Markdown
# The marker is placed at the absolute top of the string
MD_SUMMARY = f"""{COMMENT_MARKER}
### 🧪 QA Run: [my-run-date](https://qa.com/runs/my-run-date)

* **Flavor:** `crimson-debug`
* **List of PRs:** https://github.com/ceph/ceph/pull/{PR_NUMBER}

#### ⚠️ Failures, unrelated:

| Jobs | Tracker | Details |
| :--- | :--- | :--- |
| 327631, 327718, 327577, 327664, 327687, 327600 | [Issue #73169](https://tracker.ceph.com/issues/73169) | wait_for_recovery is failing with few PGs unable to complete recovery |
| 327584, 327642 | [Issue #76469](https://tracker.ceph.com/issues/76469) | pgs not deep-scrubbed in time (PG_NOT_DEEP_SCRUBBED) |
| 327700 | [Issue #75957](https://tracker.ceph.com/issues/75957) | `void BlueStore::_txc_add_transaction(...)` abort (unexpected error) |
"""

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


def get_existing_comment_id():
    """Finds the ID of a comment containing our hidden marker."""
    url = f"https://api.github.com/repos/{REPO}/issues/{PR_NUMBER}/comments"
    response = requests.get(url, headers=headers)
    response.raise_for_status()

    for comment in response.json():
        if COMMENT_MARKER in comment.get("body", ""):
            return comment["id"]
    return None


def post_or_update():
    try:
        comment_id = get_existing_comment_id()

        if comment_id:
            # Edit existing comment
            print(f"Updating existing comment ID: {comment_id}...")
            url = f"https://api.github.com/repos/{REPO}/issues/comments/{comment_id}"
            response = requests.patch(url, headers=headers, json={"body": MD_SUMMARY})
        else:
            # Post a fresh comment
            print("No previous comment found. Posting a new one...")
            url = f"https://api.github.com/repos/{REPO}/issues/{PR_NUMBER}/comments"
            response = requests.post(url, headers=headers, json={"body": MD_SUMMARY})

        if response.status_code in [200, 201]:
            print(f"✅ Summary successfully posted to PR #{PR_NUMBER}!")
        else:
            print(f"❌ Failed: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Connection Error: {e}")


if __name__ == "__main__":
    post_or_update()

