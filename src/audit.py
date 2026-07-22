"""Shared live-audit logic used by both main.py (CLI, writes static files) and
server.py (live dashboard, writes nothing -- queries ACC fresh on every request).

Nothing in this module caches ACC data to disk. AuditContext holds only an
authenticated client + the (static, local) naming-rules config; every method
call goes out to ACC live.
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.auth import TokenProvider, AuthError
from src.acc_client import ACCClient, ACCApiError
from src import rules_engine

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "naming_rules.json")


class ContextError(RuntimeError):
    pass


def timestamp():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


class AuditContext:
    """Holds the authenticated ACC client + local rules config. Auth tokens
    refresh themselves on expiry (see TokenProvider); nothing else is cached."""

    def __init__(self):
        load_dotenv()
        client_id = os.environ.get("APS_CLIENT_ID")
        client_secret = os.environ.get("APS_CLIENT_SECRET")
        self.account_id = os.environ.get("ACC_ACCOUNT_ID")
        impersonate_email = os.environ.get("ACC_IMPERSONATE_USER_EMAIL")

        try:
            self.client = ACCClient(TokenProvider(client_id, client_secret))
        except AuthError as e:
            raise ContextError(str(e))

        if not self.account_id:
            raise ContextError("ACC_ACCOUNT_ID is not set in .env. Run `python main.py --list-hubs` first.")

        hubs = self.client.list_hubs()
        hub = next((h for h in hubs if h["id"].removeprefix("b.") == self.account_id), None)
        if not hub:
            available = ", ".join(f"{h['id']} ({h['attributes']['name']})" for h in hubs)
            raise ContextError(f"No hub found matching account id {self.account_id}. Available: {available}")
        self.hub = hub
        self.hub_id = hub["id"]
        self.account_name = hub["attributes"]["name"]

        if impersonate_email:
            self.client._impersonate_user_id = self.client.find_user_id_by_email(self.account_id, impersonate_email)

        self.config = rules_engine.load_config(CONFIG_PATH)

    def list_active_projects(self, name_filter=None):
        """Live call, every time -- current active/production projects, each
        annotated with its matched naming-standard code and Data Management id."""
        admin_projects = self.client.list_projects_account_admin(self.account_id)
        admin_projects = [p for p in admin_projects if p.get("status") == "active" and p.get("classification") == "production"]

        dm_projects = self.client.list_projects_data_management(self.hub_id)
        dm_by_name = {p["attributes"]["name"]: p["id"] for p in dm_projects}

        if name_filter:
            admin_projects = [p for p in admin_projects if name_filter.lower() in p["name"].lower()]

        result = []
        for p in admin_projects:
            code, _ = rules_engine.match_project_to_code(p["name"], self.config)
            result.append({
                "name": p["name"],
                "code": code or "",
                "confidence": self.config["projects"].get(code, {}).get("confidence") if code else None,
                "dm_id": dm_by_name.get(p["name"]),
            })
        return result

    def audit_project(self, project_name, dm_project_id):
        """Runs the live folder/naming/metadata crawl for one project, scoped to
        02_Shared only (01_WIP, 03_Published, 04_Archive, 05_Documents,
        06_Transmittals are intentionally NOT checked -- WIP in particular holds
        draft/incomplete work that isn't expected to meet naming/metadata/folder
        standards until it's shared for coordination; checking it produces noise,
        not signal).

        Real ACC structure (confirmed against the live account): a project's Docs
        tree lives under the top folder literally named 'Project Files'; ITS
        immediate subfolders are one per awarded contract/package (e.g.
        'C3018 - Lead Design Consultancy...'), and 02_Shared lives one level
        inside each of those -- not at the project root.
        """
        client, config, hub_id = self.client, self.config, self.hub_id
        code, _ = rules_engine.match_project_to_code(project_name, config)
        findings = []
        shared_required = rules_engine.shared_only_folder_template(config)

        project_files = client.get_project_files_folder(hub_id, dm_project_id)
        if project_files is None:
            findings = [{
                "project_name": project_name, "project_code": code or "",
                "category": "folder_structure", "severity": "critical",
                "path": "(project root)", "detail": "No 'Project Files' top folder found",
            }]
            return findings, self._summary(project_name, code, findings)

        packages = client.list_contract_packages(dm_project_id, project_files)
        items_by_path = {}

        for package in packages:
            package_name = package["attributes"]["name"]
            contract_no, recognised = rules_engine.match_contract_package(package_name, code, config)
            if not contract_no:
                findings.append({
                    "project_name": project_name, "project_code": code or "",
                    "category": "folder_structure", "severity": "warning",
                    "path": f"Project Files/{package_name}", "detail": "Unexpected folder (not a recognised contract package)",
                })
                continue
            if code and not recognised:
                findings.append({
                    "project_name": project_name, "project_code": code or "",
                    "category": "folder_structure", "severity": "warning",
                    "path": f"Project Files/{package_name}",
                    "detail": f"Contract '{contract_no}' is not in the known contract list for project code '{code}'",
                })

            sub_folders, _items = client.get_folder_contents(dm_project_id, package["id"])
            shared_folder = next((f for f in sub_folders if f["attributes"]["name"] == "02_Shared"), None)
            if not shared_folder:
                findings.append({
                    "project_name": project_name, "project_code": code or "",
                    "category": "folder_structure", "severity": "critical",
                    "path": f"Project Files/{package_name}/02_Shared", "detail": "Required folder is missing",
                })
                continue

            observed_folders = set()
            for rel_path, node_type, node in client.walk_folder_tree(dm_project_id, shared_folder, ""):
                rel_path = rel_path.lstrip("/")
                if node_type == "folder":
                    if rel_path:
                        observed_folders.add(rel_path)
                else:
                    folder_path = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
                    items_by_path.setdefault(f"Project Files/{package_name}/02_Shared/{folder_path}", []).append(node)

                    if code:
                        problems = rules_engine.validate_filename(node["name"], config["projects"][code], config)
                        for p in problems:
                            findings.append({
                                "project_name": project_name, "project_code": code,
                                "category": "naming", "severity": "serious",
                                "path": f"Project Files/{package_name}/02_Shared/{rel_path}", "detail": p,
                            })

            folder_result = rules_engine.validate_folder_structure(observed_folders, config, required=shared_required)
            for missing in folder_result["missing"]:
                findings.append({
                    "project_name": project_name, "project_code": code or "",
                    "category": "folder_structure", "severity": "critical",
                    "path": f"Project Files/{package_name}/02_Shared/{missing}", "detail": "Required folder is missing",
                })

        for folder_path, items in items_by_path.items():
            urns = [it["tip_version_id"] for it in items if it.get("tip_version_id")]
            if not urns:
                continue
            try:
                values = client.batch_get_custom_attributes(dm_project_id, urns)
            except ACCApiError:
                continue
            by_urn = {v["urn"]: v for v in values}
            for it in items:
                v = by_urn.get(it.get("tip_version_id"))
                present = v.get("customAttributes", []) if v else []
                missing_fields = rules_engine.missing_metadata_fields(present, config)
                for field in missing_fields:
                    findings.append({
                        "project_name": project_name, "project_code": code or "",
                        "category": "metadata", "severity": "warning",
                        "path": f"{folder_path}/{it['name']}", "detail": f"Missing required metadata: '{field}'",
                    })

        return findings, self._summary(project_name, code, findings)

    def _summary(self, project_name, code, findings):
        issue_counts = {"critical": 0, "serious": 0, "warning": 0}
        for f in findings:
            issue_counts[f["severity"]] += 1
        return {
            "project_name": project_name,
            "project_code": code or "",
            "match_confidence": self.config["projects"].get(code, {}).get("confidence") if code else None,
            "issue_counts": issue_counts,
        }

    def audit_shared_bim(self, project_name, dm_project_id):
        """Focused live check of 02_Shared/BIM/{Federated_NWD, IFC, Native Files,
        Navisworks Coordination} only -- not the full CDE tree. For each file:
        Name/Description/Version/Size/Last Updated, flagging a missing Description
        everywhere, and the wrong file extension in Federated_NWD/IFC specifically
        (Native Files and Navisworks Coordination are intentionally not extension-checked).
        Returns a flat list of row dicts, one per file found.
        """
        client = self.client
        rows = []

        project_files = client.get_project_files_folder(self.hub_id, dm_project_id)
        if project_files is None:
            return rows
        packages = client.list_contract_packages(dm_project_id, project_files)

        for package in packages:
            package_name = package["attributes"]["name"]
            sub_folders, _items = client.get_folder_contents(dm_project_id, package["id"])
            shared = next((f for f in sub_folders if f["attributes"]["name"] == "02_Shared"), None)
            if not shared:
                continue
            shared_sub, _ = client.get_folder_contents(dm_project_id, shared["id"])
            bim = next((f for f in shared_sub if f["attributes"]["name"] == "BIM"), None)
            if not bim:
                continue
            bim_sub, _ = client.get_folder_contents(dm_project_id, bim["id"])

            for target_folder in rules_engine.SHARED_BIM_EXPECTED_EXTENSIONS:
                folder_node = next((f for f in bim_sub if f["attributes"]["name"] == target_folder), None)
                if not folder_node:
                    continue
                _sub, items = client.get_folder_contents(dm_project_id, folder_node["id"])
                urns = [it["tip_version_id"] for it in items if it.get("tip_version_id")]
                versions_by_urn = {}
                if urns:
                    try:
                        for v in client.batch_get_custom_attributes(dm_project_id, urns):
                            versions_by_urn[v["urn"]] = v
                    except ACCApiError:
                        pass

                for it in items:
                    version = versions_by_urn.get(it.get("tip_version_id"), {})
                    problems = rules_engine.validate_shared_bim_item(target_folder, it["name"], it.get("description"))
                    rows.append({
                        "package": package_name,
                        "folder": target_folder,
                        "name": it["name"],
                        "description": it.get("description") or "",
                        "version": version.get("revisionNumber"),
                        "size": version.get("storageSize"),
                        "last_updated": version.get("lastModifiedTime"),
                        "flags": problems,
                        "lastModifiedUserId": it.get("lastModifiedUserId"),
                    })

        return rows
    def send_notification_emails(self, project_name, dm_project_id, rows):
        from src.email_sender import create_draft_email
        
        project_users = self.client.get_project_users(dm_project_id)
        
        # Build lookups
        user_email_by_id = {}
        admin_emails = []
        for u in project_users:
            uid = u.get("autodeskId")
            email = u.get("email")
            if uid and email:
                user_email_by_id[uid] = email
            if u.get("accessLevels", {}).get("projectAdmin") and email:
                if "it.support" not in email.lower():
                    admin_emails.append(email)
                
        # Group problematic files by user
        issues_by_user = {}
        for row in rows:
            if not row.get("flags"):
                continue
            uid = row.get("lastModifiedUserId")
            if not uid:
                continue
            if uid not in issues_by_user:
                issues_by_user[uid] = []
            issues_by_user[uid].append(row)
            
        admin_cc = ";".join(admin_emails)
        success_count = 0
        errors = []
        emailed_users = []
        
        for uid, flagged_rows in issues_by_user.items():
            to_email = user_email_by_id.get(uid)
            if not to_email:
                continue
                
            subject = f"Action Required: ACC Naming & Metadata Issues for {project_name}"
            
            body = f"<p>Hello,</p><p>Please correct the following issues in the <b>{project_name}</b> project (02_Shared folder):</p>"
            body += "<ul>"
            for row in flagged_rows:
                flags_text = ", ".join(row["flags"])
                body += f"<li><b>{row['name']}</b> (in {row['folder']}): <span style='color:red;'>{flags_text}</span></li>"
            body += "</ul><p>Thank you.</p>"
            
            ok, err = create_draft_email(to_email, admin_cc, subject, body)
            if ok:
                success_count += 1
                emailed_users.append(to_email)
            else:
                errors.append(err)
                
        return success_count, emailed_users, errors
