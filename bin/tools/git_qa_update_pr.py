import os
import requests

# --- CONFIGURATION ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "your_github_pat_here")
REPO = "your-org-or-username/your-repo-name"
PR_NUMBER = 42  # This can be dynamically passed as an argument to your script

# Unique hidden HTML string to identify your script's specific comment
COMMENT_MARKER = ""

# Data you want to post
SCRIPT_OUTPUT = f"""{COMMENT_MARKER}
### 🧪 Automated Test Results
* **Status:** Passed
* **Coverage:** 87.5%
* **Logs:** [View Build Output](https://ci.example.com/build/123)
"""
# ---------------------

headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json",
}


def get_existing_comment_id(repo, pr_number):
    """Searches PR comments for our unique hidden marker."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    response = requests.get(url, headers=headers)
    response.raise_for_status()

    comments = response.json()
    for comment in comments:
        # Check if our unique tracking tag is hidden in the comment body
        if COMMENT_MARKER in comment.get("body", ""):
            return comment["id"]
    return None


def create_or_update_comment():
    existing_id = get_existing_comment_id(REPO, PR_NUMBER)

    if existing_id:
        # Update existing comment
        print(f"Found existing comment (ID: {existing_id}). Updating...")
        url = f"https://api.github.com/repos/{REPO}/issues/comments/{existing_id}"
        response = requests.patch(url, headers=headers, json={"body": SCRIPT_OUTPUT})
    else:
        # Create a brand new comment
        print("No existing comment found. Creating a new one...")
        url = f"https://api.github.com/repos/{REPO}/issues/{PR_NUMBER}/comments"
        response = requests.post(url, headers=headers, json={"body": SCRIPT_OUTPUT})

    if response.status_code in [200, 201]:
        print("🎉 Successfully posted results to GitHub PR!")
    else:
        print(f"❌ Failed to post comment: {response.status_code} - {response.text}")


if __name__ == "__main__":
    create_or_update_comment()
