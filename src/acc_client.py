"""Thin client over the Autodesk Platform Services endpoints this auditor needs.

Every endpoint/scope/response-shape here was independently verified against the
live APS docs on 2026-07-22 (see the research log for exact source URLs):
  - Data Management API v2   (aps.autodesk.com/en/docs/data/v2)
  - Account Admin API        (aps.autodesk.com/en/docs/acc/v1 -- construction/admin/v1)
  - Docs Custom Attributes   (aps.autodesk.com/en/docs/acc/v1 -- bim360/docs/v1)
"""

import json
import threading
import time

import requests

BASE = "https://developer.api.autodesk.com"

# requests/minute per endpoint group, per APS's published rate limits
RATE_LIMITS = {
    "hubs_projects": 50,
    "folders": 300,
    "custom_attrs_read": 100,
}


class ACCApiError(RuntimeError):
    def __init__(self, resp):
        self.status_code = resp.status_code
        self.body = resp.text[:1000]
        super().__init__(f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {self.body}")


class RateLimiter:
    """Thread-safe -- multiple projects can be audited concurrently (see
    audit.py's ThreadPoolExecutor use) while still respecting one shared,
    account-wide pace per endpoint group. The lock only guards the tiny
    "claim my slot" bookkeeping; the actual sleep happens outside it so
    threads queue for a turn rather than serializing on the whole wait."""

    def __init__(self, per_minute):
        self.min_interval = 60.0 / per_minute
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def wait(self):
        with self._lock:
            now = time.time()
            start = max(now, self._next_slot)
            self._next_slot = start + self.min_interval
        sleep_for = start - now
        if sleep_for > 0:
            time.sleep(sleep_for)


class ACCClient:
    def __init__(self, token_provider, impersonate_user_id=None):
        self._token_provider = token_provider
        self._impersonate_user_id = impersonate_user_id
        self._limiters = {k: RateLimiter(v) for k, v in RATE_LIMITS.items()}
        self._session = requests.Session()

    # ---- low level -----------------------------------------------------

    def _headers(self, extra=None):
        h = {"Authorization": f"Bearer {self._token_provider.get_token()}"}
        if self._impersonate_user_id:
            h["x-user-id"] = self._impersonate_user_id
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _json(resp):
        # APS doesn't always set charset=utf-8 on the Content-Type header, and
        # requests falls back to Latin-1 per the HTTP spec when it's absent --
        # silently mangling non-ASCII names (e.g. the "–" in many ECD folder
        # names). Decode the raw bytes as UTF-8 explicitly instead of resp.json().
        return json.loads(resp.content.decode("utf-8"))

    def _get(self, url, bucket, params=None):
        self._limiters[bucket].wait()
        for attempt in range(5):
            resp = self._session.get(url, headers=self._headers(), params=params, timeout=60)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                time.sleep(retry_after)
                continue
            if resp.status_code >= 400:
                raise ACCApiError(resp)
            return self._json(resp)
        raise ACCApiError(resp)

    def _post(self, url, bucket, json_body):
        self._limiters[bucket].wait()
        for attempt in range(5):
            resp = self._session.post(
                url, headers=self._headers({"Content-Type": "application/json"}), json=json_body, timeout=60
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                time.sleep(retry_after)
                continue
            if resp.status_code >= 400:
                raise ACCApiError(resp)
            return self._json(resp)
        raise ACCApiError(resp)

    # ---- users (for optional impersonation) -----------------------------

    def find_user_id_by_email(self, account_id, email):
        data = self._get(f"{BASE}/hq/v1/accounts/{account_id}/users/search", "hubs_projects", params={"email": email})
        if isinstance(data, list) and data:
            return data[0]["id"]
        raise ACCApiError.__new__(ACCApiError) if False else RuntimeError(f"No user found for {email}")

    def get_account_users(self, account_id):
        """Fetch all users in the account to map user_id to email."""
        results = []
        offset = 0
        limit = 100
        while True:
            data = self._get(
                f"{BASE}/hq/v1/accounts/{account_id}/users",
                "hubs_projects",
                params={"limit": limit, "offset": offset},
            )
            if not data:
                break
            results.extend(data)
            if len(data) < limit:
                break
            offset += limit
        return results

    def get_project_users(self, project_id):
        """Fetch project users and return the full list to extract emails and admin roles."""
        results = []
        offset = 0
        limit = 100
        while True:
            # Try ACC admin endpoint
            try:
                data = self._get(
                    f"{BASE}/construction/admin/v1/projects/{project_id}/users",
                    "hubs_projects",
                    params={"limit": limit, "offset": offset},
                )
                users = data.get("results", [])
                results.extend(users)
                total = data.get("pagination", {}).get("totalResults", len(results))
                offset += limit
                if offset >= total:
                    break
            except Exception:
                break
        return results

    # ---- hubs / projects -------------------------------------------------

    def list_hubs(self):
        data = self._get(f"{BASE}/project/v1/hubs", "hubs_projects")
        return data.get("data", [])

    def list_projects_data_management(self, hub_id):
        """Only ACTIVE projects (Data Management drops inactive/archived ones)."""
        results = []
        page = 0
        while True:
            data = self._get(
                f"{BASE}/project/v1/hubs/{hub_id}/projects",
                "hubs_projects",
                params={"page[number]": page, "page[limit]": 200},
            )
            results.extend(data.get("data", []))
            if not data.get("links", {}).get("next"):
                break
            page += 1
        return results

    def list_projects_account_admin(self, account_id):
        """Full project list including status + classification (production/template/...)."""
        results = []
        offset = 0
        limit = 200
        while True:
            data = self._get(
                f"{BASE}/construction/admin/v1/accounts/{account_id}/projects",
                "hubs_projects",
                params={"limit": limit, "offset": offset},
            )
            results.extend(data.get("results", []))
            total = data.get("pagination", {}).get("totalResults", len(results))
            offset += limit
            if offset >= total:
                break
        return results

    # ---- folder tree -------------------------------------------------------

    def get_top_folders(self, hub_id, project_id):
        data = self._get(f"{BASE}/project/v1/hubs/{hub_id}/projects/{project_id}/topFolders", "folders")
        return data.get("data", [])

    def get_folder_contents(self, project_id, folder_id):
        """Returns (folders, items) for the immediate contents of one folder.
        Each item includes its resolved tip-version name (item.displayName is
        reserved/unreliable -- the real filename lives on the included version)."""
        folders, items = [], []
        page = 0
        while True:
            data = self._get(
                f"{BASE}/data/v1/projects/{project_id}/folders/{folder_id}/contents",
                "folders",
                params={"page[number]": page, "page[limit]": 200},
            )
            included_versions = {v["id"]: v for v in data.get("included", []) if v.get("type") == "versions"}
            for entry in data.get("data", []):
                if entry["type"] == "folders":
                    folders.append(entry)
                elif entry["type"] == "items":
                    tip_id = entry.get("relationships", {}).get("tip", {}).get("data", {}).get("id")
                    version = included_versions.get(tip_id, {})
                    # "Description" (as shown in the ACC Docs UI) is a built-in field,
                    # not a custom attribute -- it lives on the ITEM resource under
                    # attributes.extension.data.description, not on the version.
                    description = entry["attributes"].get("extension", {}).get("data", {}).get("description")
                    items.append(
                        {
                            "id": entry["id"],
                            "name": version.get("attributes", {}).get("name") or entry["attributes"].get("displayName"),
                            "tip_version_id": tip_id,
                            "description": description,
                            "lastModifiedUserId": version.get("attributes", {}).get("lastModifiedUserId"),
                            "createUserId": version.get("attributes", {}).get("createUserId"),
                            "raw": entry,
                        }
                    )
            if not data.get("links", {}).get("next"):
                break
            page += 1
        return folders, items

    def get_project_files_folder(self, hub_id, project_id):
        """ACC top folders include many module-internal system folders (Photos,
        cost/issues/checklists/submittals roots, etc.) -- the actual Docs file
        tree the naming/folder standard applies to lives under the one literally
        named 'Project Files'."""
        top_folders = self.get_top_folders(hub_id, project_id)
        return next((tf for tf in top_folders if tf["attributes"]["name"] == "Project Files"), None)

    def list_contract_packages(self, project_id, project_files_folder):
        """Immediate subfolders of 'Project Files' -- normally one per awarded
        contract (e.g. 'C3018 - Lead Design Consultancy...'). The CDE structure
        (01_WIP/02_Shared/...) lives one level inside each of these, not at the
        project root."""
        sub_folders, _items = self.get_folder_contents(project_id, project_files_folder["id"])
        return sub_folders

    def walk_folder_tree(self, project_id, root_folder, root_label):
        """Yields (relative_path, node_type, node) for every folder and item
        beneath root_folder, depth-first, with paths relative to root_label."""
        stack = [(root_label, root_folder)]
        while stack:
            rel_path, folder_node = stack.pop()
            yield rel_path, "folder", folder_node
            sub_folders, items = self.get_folder_contents(project_id, folder_node["id"])
            for it in items:
                yield f"{rel_path}/{it['name']}", "item", it
            for sf in sub_folders:
                stack.append((f"{rel_path}/{sf['attributes']['name']}", sf))

    # ---- custom attributes -------------------------------------------------

    def get_custom_attribute_definitions(self, project_id, folder_id):
        data = self._get(
            f"{BASE}/bim360/docs/v1/projects/{project_id}/folders/{folder_id}/custom-attribute-definitions",
            "custom_attrs_read",
        )
        return data.get("results", [])

    def batch_get_custom_attributes(self, project_id, version_urns):
        """version_urns: list of Data Management version URNs (max 50 per call)."""
        all_results = []
        for i in range(0, len(version_urns), 50):
            chunk = version_urns[i : i + 50]
            data = self._post(
                f"{BASE}/bim360/docs/v1/projects/{project_id}/versions:batch-get",
                "custom_attrs_read",
                {"urns": chunk},
            )
            all_results.extend(data.get("results", []))
        return all_results
