import os
from flask import Flask, abort, jsonify, request
from jira import JIRA
import requests

app = Flask(__name__)

# --- CONFIGURATION ---
# GitHub Configuration
GITHUB_TOKEN = "your_github_pat_token"  # Needed if your repo is private

# Target Classic Project Column ID (Find this in GitHub Webhook Recent Deliveries)
TARGET_COLUMN_ID = 12345678  # Replace with your actual numeric column ID

# Jira Configuration
JIRA_SERVER = "https://your-domain.atlassian.net"
JIRA_EMAIL = "your-email@company.com"
JIRA_API_TOKEN = "your_jira_api_token"
JIRA_PROJECT_KEY = "PROJ"
JIRA_BOARD_ID = 1
JIRA_SPRINT_FIELD = "customfield_10020"
# ---------------------

jira = JIRA(server=JIRA_SERVER, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))


def get_active_sprint_id(board_id):
    sprints = jira.sprints(board_id, state="active")
    return sprints[0].id if sprints else None


@app.route("/webhook", methods=["POST"])
def github_classic_webhook():
    payload = request.json
    if not payload:
        abort(400)

    event_type = request.headers.get("X-GitHub-Event")

    # 1. Capture the Classic Project Card event
    if event_type == "project_card":
        action = payload.get("action")
        project_card = payload.get("project_card", {})
        current_column_id = project_card.get("column_id")

        # 2. Check if the card was 'moved' into your specific QA Column ID
        if action == "moved" and current_column_id == TARGET_COLUMN_ID:
            content_url = project_card.get("content_url")

            # Ensure the card is actually linked to an Issue or PR
            if content_url:
                print("📦 Card moved to QA Column. Fetching PR details...")

                # 3. Call GitHub API to get PR title and HTML URL
                headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
                gh_response = requests.get(content_url, headers=headers)

                if gh_response.status_code == 200:
                    gh_data = gh_response.json()

                    # Filter out regular issues; ensure it's a Pull Request
                    if "pull_request" in gh_data or "/pulls/" in content_url:
                        pr_title = gh_data.get("title")
                        pr_url = gh_data.get("html_url")

                        print(f"🚀 Triggering Jira for PR: {pr_title}")
                        create_jira_qa_task(pr_title, pr_url)
                else:
                    print(
                        f"❌ Failed to fetch PR info from GitHub: {gh_response.status_code}"
                    )

    return jsonify({"status": "success"}), 200


def create_jira_qa_task(pr_title, pr_url):
    try:
        active_sprint = get_active_sprint_id(JIRA_BOARD_ID)
        issue_dict = {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": f"QA Testing: {pr_title}",
            "description": f"Please perform QA testing on this PR.\n\nGitHub Link: {pr_url}",
            "issuetype": {"name": "Task"},
        }

        if active_sprint:
            issue_dict[JIRA_SPRINT_FIELD] = active_sprint

        new_issue = jira.create_issue(fields=issue_dict)
        print(f"✅ Successfully created Jira Task: {new_issue.key}")
    except Exception as e:
        print(f"❌ Failed to create Jira task: {e}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

