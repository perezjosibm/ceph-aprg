from flask import Flask, jsonify, request
import subprocess

app = Flask(__name__)


@app.route("/jenkins-build-complete", methods=["POST"])
def jenkins_trigger():
    payload = request.json
    if not payload:
        return jsonify({"error": "No payload"}), 400

    # Extract the PR number sent by Jenkins
    pr_number = payload.get("pr_number")
    build_status = payload.get("status")

    if pr_number:
        print(
            f"🔔 Jenkins reported build complete for PR #{pr_number} with status: {build_status}"
        )

        # Kick off your local QA reporting script
        # (Assuming your script is named report_to_github.py)
        subprocess.Popen(["python3", "report_to_github.py", str(pr_number)])

        return jsonify({"status": "Triggered local script"}), 200

    return jsonify({"error": "Missing PR number"}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)

