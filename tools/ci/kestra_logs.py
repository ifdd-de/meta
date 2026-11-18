"""Print the task logs of the latest executions of the Git flows of Kestra.

Runs inside the kestra container of wf (python3 there, standard library only):

    docker compose -f docker-compose-production.yml exec -T kestra python3 - < kestra_logs.py

Uses the Kestra admin credentials from the container's environment.
"""

import base64
import json
import os
import urllib.request

API = "http://localhost:8080/api/v1/main"
AUTH = base64.b64encode(
    f"{os.environ['IFDD_WF_ADMIN_USER']}:{os.environ['IFDD_WF_ADMIN_PASSWORD']}".encode()
).decode()


def get(path):
    request = urllib.request.Request(API + path, headers={"Authorization": f"Basic {AUTH}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


for flow in ("git_restore", "git_push"):
    try:
        found = get(f"/executions/search?namespace=system&flowId={flow}&size=3")
    except Exception as error:  # noqa: BLE001 - diagnostics only
        print(f"{flow}: search failed: {error}")
        continue
    for execution in found.get("results", []):
        state = execution.get("state", {}).get("current")
        print(f"== system.{flow} {execution['id']} {state}")
        for entry in get(f"/logs/{execution['id']}"):
            print(f"{entry.get('level')} {entry.get('taskId')}: {entry.get('message')}"[:600])
