"""Live ACC Governance Compliance dashboard.

Runs a small local web server. Nothing is ever written to disk -- results
live only in this process's memory (cleared on restart), never in a file or
database. Only accessible from this PC (binds to localhost).

Caching behavior (in-memory only, not "saved data" in the disk sense):
  - "/" itself never crawls anything -- it's just the live (but fast) project
    picker. Nothing is audited until you pick a project or open the dashboard.
  - Selecting a project audits JUST that project, live, and caches it in
    memory so revisiting it is instant until you explicitly refresh it.
  - "/dashboard" audits every active project the FIRST time it's opened (this
    is slow -- see the warning on that page) and caches the results; reopening
    it afterward is instant. "Refresh all" forces a full live re-crawl.
  - Click "Refresh this project" on a project's page to re-crawl live for
    JUST that one project (does not touch the others' cached data).
  - The Shared/BIM check is always live on demand (it's fast enough already
    that caching it wasn't worth the complexity).

Usage:
    python server.py
    -> open http://localhost:5000

Routes:
    /                          fast project picker (no crawling)
    /dashboard                 KPI tiles + per-project chart, all projects (slow the first time)
    /dashboard?refresh=1       same, but forces a full live re-crawl first
    /project/<slug>            one project's full audit (cached after first view)
    /project/<slug>?refresh=1  re-crawls just that project live
    /project/<slug>/shared-bim focused live check of 02_Shared/BIM
"""

import sys
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, abort, request

sys.path.insert(0, os.path.dirname(__file__))
from src.audit import AuditContext, ContextError, timestamp
from src.acc_client import ACCApiError
from src.report import build_index_report, build_project_detail_report, build_shared_bim_report, build_picker, safe_filename, _page

app = Flask(__name__)
_ctx = None

# In-memory only -- never written to disk, gone on restart.
_cache = {
    "summaries": {},    # slug -> summary dict
    "findings": {},     # slug -> findings list
    "audited_at": {},   # slug -> timestamp string
    "last_full_audit_at": None,
    "projects_list": None,
}


def get_context():
    global _ctx
    if _ctx is None:
        _ctx = AuditContext()
    return _ctx


def get_cached_projects(ctx, force_refresh=False):
    if force_refresh or _cache.get("projects_list") is None:
        _cache["projects_list"] = ctx.list_active_projects(name_filter="sky")
    return _cache["projects_list"]


def slug_for(name):
    return safe_filename(name).removesuffix(".html")


def audit_one(ctx, project):
    """Live-audits a single project and updates the cache for its slug."""
    findings, summary = ctx.audit_project(project["name"], project["dm_id"])
    slug = slug_for(project["name"])
    _cache["summaries"][slug] = summary
    _cache["findings"][slug] = findings
    _cache["audited_at"][slug] = timestamp()
    return findings, summary


FULL_AUDIT_WORKERS = 10  # concurrent projects; the ACCClient's rate limiters
                         # still enforce one shared, account-wide pace per
                         # endpoint group, so this just keeps that pace fully
                         # busy instead of idling on network latency between
                         # each project's sequential calls.


def run_full_audit(ctx):
    """Live-audits every active project concurrently and (re)populates the
    whole cache. Each project's own crawl is still sequential internally
    (its folders depend on each other), but auditing several projects at
    once removes the dead time between them."""
    projects = [p for p in get_cached_projects(ctx, force_refresh=True) if p["dm_id"]]
    with ThreadPoolExecutor(max_workers=FULL_AUDIT_WORKERS) as pool:
        futures = {pool.submit(audit_one, ctx, p): p for p in projects}
        for future in as_completed(futures):
            try:
                future.result()
            except ACCApiError:
                continue
    _cache["last_full_audit_at"] = timestamp()


@app.route("/")
def index():
    try:
        ctx = get_context()
    except ContextError as e:
        return f"<pre>Configuration error: {e}</pre>", 500



    force_refresh = bool(request.args.get("refresh"))
    projects = get_cached_projects(ctx, force_refresh=force_refresh)
    alphabetical = sorted(projects, key=lambda p: p["name"])
    picker_items = []
    for p in alphabetical:
        slug = slug_for(p["name"])
        summary = _cache["summaries"].get(slug)
        if summary:
            total = sum(summary["issue_counts"].values())
            status_badge = f'<span class="conf-pill">{total or "OK"} issue(s)</span>'
        else:
            status_badge = '<span class="conf-pill">not yet audited</span>'
        confirm_badge = ' <span class="conf-pill low">needs confirmation</span>' if p["confidence"] == "low_ambiguous_duplicate" else ""
        picker_items.append({
            "url": f"/project/{slug}", "name": p["name"], "code": p["code"],
            "badge_html": status_badge + confirm_badge,
        })

    body = f"""
  <div class="hero">
    <div>
      <h2>{len(projects)} active production projects</h2>
      <p>Data as of {_cache["last_full_audit_at"]} (02_Shared only -- WIP is intentionally skipped).
      Nothing is saved to disk. Jumping to a project below is instant (cached from this crawl);
      <a href="/?refresh=1">refresh all</a> to re-crawl live.</p>
    </div>
    <a class="btn" href="/dashboard">All Projects Dashboard &rarr;</a>
  </div>
  <section>
    <h2>Jump to a project</h2>
    {build_picker(picker_items)}
  </section>"""
    return _page("ACC Projects (live)", ctx.account_name, timestamp(), body)


@app.route("/dashboard")
def dashboard():
    try:
        ctx = get_context()
    except ContextError as e:
        return f"<pre>Configuration error: {e}</pre>", 500

    if _cache["last_full_audit_at"] is None or request.args.get("refresh"):
        run_full_audit(ctx)

    summaries = list(_cache["summaries"].values())

    def link_fn(s):
        return f"/project/{slug_for(s['project_name'])}"

    header = (
        '<div class="btn-row">'
        '<a class="btn secondary" href="/">&larr; Project picker</a>'
        '<a class="btn secondary" href="/dashboard?refresh=1">&#8635; Refresh all (slow)</a>'
        '</div>'
        '<p class="note">'
        f'Data as of {_cache["last_full_audit_at"]} &mdash; from the first full crawl this session; '
        'reopening this page does not re-crawl everything again. Nothing here is saved to disk.'
        '</p>'
    )
    html = build_index_report(
        summaries, timestamp(), account_name=ctx.account_name,
        project_link_fn=link_fn, extra_header_html=header,
    )
    return html


@app.route("/project/<slug>")
def project_detail(slug):
    try:
        ctx = get_context()
    except ContextError as e:
        return f"<pre>Configuration error: {e}</pre>", 500

    projects = get_cached_projects(ctx)
    match = next((p for p in projects if slug_for(p["name"]) == slug), None)
    if not match:
        abort(404, f"No active project matches '{slug}' (it may have been renamed/archived since you loaded the list)")
    if not match["dm_id"]:
        return f"<pre>{match['name']}: not found via Data Management API</pre>", 502

    force_refresh = bool(request.args.get("refresh"))
    if force_refresh or slug not in _cache["summaries"]:
        try:
            findings, summary = audit_one(ctx, match)
        except ACCApiError as e:
            return f"<pre>ACC API error auditing {match['name']}:\n{e}</pre>", 502
    else:
        findings, summary = _cache["findings"][slug], _cache["summaries"][slug]

    html = build_project_detail_report(
        summary, findings, timestamp(), account_name=ctx.account_name,
        back_link="/", shared_bim_link=f"/project/{slug}/shared-bim",
        refresh_link=f"/project/{slug}?refresh=1", data_as_of=_cache["audited_at"].get(slug),
    )
    return html


@app.route("/project/<slug>/shared-bim")
def project_shared_bim(slug):
    try:
        ctx = get_context()
    except ContextError as e:
        return f"<pre>Configuration error: {e}</pre>", 500

    projects = get_cached_projects(ctx)
    match = next((p for p in projects if slug_for(p["name"]) == slug), None)
    if not match:
        abort(404, f"No active project matches '{slug}'")
    if not match["dm_id"]:
        return f"<pre>{match['name']}: not found via Data Management API</pre>", 502

    try:
        rows = ctx.audit_shared_bim(match["name"], match["dm_id"])
    except ACCApiError as e:
        return f"<pre>ACC API error checking {match['name']}:\n{e}</pre>", 502

    return build_shared_bim_report(
        match["name"], rows, timestamp(), account_name=ctx.account_name,
        back_link="/", full_audit_link=f"/project/{slug}",
        notify_link=f"/project/{slug}/shared-bim/notify"
    )


@app.route("/project/<slug>/shared-bim/notify", methods=["POST"])
def notify_shared_bim(slug):
    try:
        ctx = get_context()
    except ContextError as e:
        return f"Configuration error: {e}", 500

    projects = get_cached_projects(ctx)
    match = next((p for p in projects if slug_for(p["name"]) == slug), None)
    if not match:
        return f"No active project matches '{slug}'", 404
        
    try:
        rows = ctx.audit_shared_bim(match["name"], match["dm_id"])
        success_count, emailed_users, errors = ctx.send_notification_emails(match["name"], match["dm_id"], rows)
    except Exception as e:
        return f"Error triggering emails: {e}", 500
        
    msg = f"Opened {success_count} email draft(s)."
    if emailed_users:
        msg += f" Addressed to: {', '.join(emailed_users)}"
    if errors:
        msg += f" Errors: {errors}"
    return msg, 200


if __name__ == "__main__":
    print("Starting live ACC dashboard at http://localhost:8080  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=8080, debug=False)
