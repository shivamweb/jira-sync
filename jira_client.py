import requests
from requests.auth import HTTPBasicAuth


class JiraClient:
    def __init__(self, base_url: str, email: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(email, token)
        self.session.headers.update({"Accept": "application/json"})

    def _url(self, path: str) -> str:
        return f"{self.base_url}/rest/api/3{path}"

    @staticmethod
    def _check(r):
        if not r.ok:
            raise requests.HTTPError(
                f"{r.status_code} {r.reason} for {r.request.method} {r.url}: {r.text}",
                response=r,
            )

    def create_issue(self, project_key, summary, description_adf, issue_type):
        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary or "(no summary)",
                "issuetype": {"name": issue_type},
            }
        }
        if description_adf:
            payload["fields"]["description"] = description_adf
        r = self.session.post(self._url("/issue"), json=payload)
        self._check(r)
        return r.json()["key"]

    def update_issue(self, key: str, fields: dict):
        r = self.session.put(self._url(f"/issue/{key}"), json={"fields": fields})
        self._check(r)

    def get_transitions(self, key: str):
        r = self.session.get(self._url(f"/issue/{key}/transitions"))
        self._check(r)
        return r.json().get("transitions", [])

    def transition_issue(self, key: str, target_status_name: str):
        transitions = self.get_transitions(key)
        match = next(
            (t for t in transitions if t["to"]["name"].lower() == target_status_name.lower()),
            None,
        )
        if not match:
            raise RuntimeError(
                f"no transition available on {key} to status '{target_status_name}'"
            )
        r = self.session.post(
            self._url(f"/issue/{key}/transitions"),
            json={"transition": {"id": match["id"]}},
        )
        self._check(r)

    def add_comment(self, key: str, body_adf):
        r = self.session.post(self._url(f"/issue/{key}/comment"), json={"body": body_adf})
        self._check(r)
        return r.json()["id"]

    def update_comment(self, key: str, comment_id: str, body_adf):
        r = self.session.put(
            self._url(f"/issue/{key}/comment/{comment_id}"), json={"body": body_adf}
        )
        self._check(r)

    def add_attachment(self, key: str, filename: str, content_bytes: bytes, content_type: str):
        # X-Atlassian-Token: no-check is required for the attachment endpoint.
        headers = {"X-Atlassian-Token": "no-check", "Accept": "application/json"}
        files = {"file": (filename, content_bytes, content_type)}
        r = self.session.post(
            self._url(f"/issue/{key}/attachments"), headers=headers, files=files
        )
        self._check(r)
        return r.json()[0]["id"]

    def download_attachment(self, content_url: str) -> bytes:
        r = self.session.get(content_url, allow_redirects=True)
        self._check(r)
        return r.content

    def get_user_display_name(self, account_id: str):
        r = self.session.get(self._url("/user"), params={"accountId": account_id})
        if not r.ok:
            return None
        return r.json().get("displayName")

    def get_attachment_meta(self, attachment_id: str):
        r = self.session.get(self._url(f"/attachment/{attachment_id}"))
        if not r.ok:
            return None
        return r.json()
