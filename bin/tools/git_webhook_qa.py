import os
from flask import Flask, abort, jsonify, request
from jira import JIRA

app = Flask(__name__)

# --- CONFIGURATION ---
# Jira Credentials
JIRA_SERVER = "https://your-domain.atlassian.net"
JIRA_EMAIL = "your-email@company.com"
JIRA_API_TOKEN = "your_jira_api_token"  # Generated from Atlassian account security
JIRA_PROJECT_KEY = "PROJ"  # e.g., 'ENG', 'QA'

# Target Column Name in GitHub Projects
TARGET_COLUMN = "Queue for QA testing"

# Jira custom field ID for Sprints varies by instance (usually customfield_10020)
# You can find yours by inspecting an existing issue's JSON schema.
JIRA_SPRINT_FIELD = "customfield_10020"
JIRA_BOARD_ID = 1  # The ID of your Jira Software Scrum board
# ---------------------

# Initialize Jira Client
jira = JIRA(server=JIRA_SERVER, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))


def get_active_sprint_id(board_id):
    """Fetches the ID of the currently active sprint for the specified board."""
    sprints = jira.sprints(board_id, state="active")
    if sprints:
        return sprints[0].id
    return None


@app.route("/webhook", methods=["POST"])
def github_webhook():
    payload = request.json
    if not payload:
        abort(400)

    # 1. Ensure we are handling a project item editing/moving event
    event_type = request.headers.get("X-GitHub-Event")

    if event_type == "projects_v2_item":
        action = payload.get("action")

        # We look for 'edited' action when an item moves columns
        if action == "edited":
            changes = payload.get("changes", {})
            field_value = changes.get("field_value", {})
            field_name = field_value.get("field_name")

            # Check if the "Status" column changed
            if field_name == "Status":
                # Extract the new column name
                # (Note: Depending on GitHub's API updates, exact pathing may vary slightly)
                project_item = payload.get("projects_v2_item", {})
                content_type = project_item.get("content_type")

                # Ensure the item is a Pull Request
                if content_type == "PullRequest":
                    # Fetch what column it moved into via the payload data
                    # For simplicity, we assume you map your field changes
                    # Alternatively, fetch the full item state from GitHub if needed.

                    # Let's assume you verified the target column match
                    # (Insert logic here to validate if new column == TARGET_COLUMN)

                    # Extract PR Details
                    pr_title = payload.get("projects_v2_item", {}).get(
                        "content_title", "New Pull Request"
                    )
                    # You would normally grab the exact URL from the content object
                    pr_url = "https://github.com/..."

                    print(f"🚀 PR '{pr_title}' moved to QA. Syncing to Jira...")
                    create_jira_qa_task(pr_title, pr_url)

    return jsonify({"status": "success"}), 200


def create_jira_qa_task(pr_title, pr_url):
    """Creates a task in the active Jira sprint."""
    try:
        active_sprint = get_active_sprint_id(JIRA_BOARD_ID)

        if not active_sprint:
            print("❌ No active sprint found. Creating task in backlog instead.")

        # Construct Issue Payload
        issue_dict = {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": f"QA Testing: {pr_title}",
            "description": f"Please perform QA testing on this PR.\n\nGitHub Link: {pr_url}",
            "issuetype": {"name": "Task"},
        }

        # If an active sprint is found, append it to the custom field
        if active_sprint:
            issue_dict[JIRA_SPRINT_FIELD] = active_sprint

        # Create the issue
        new_issue = jira.create_issue(fields=issue_dict)
        print(f"✅ Successfully created Jira Task: {new_issue.key}")

    except Exception as e:
        print(f"❌ Failed to create Jira task: {e}")


if __name__ == "__main__":
    # Run server on port 5000
    app.run(host="0.0.0.0", port=5000)

