"""ECD ACC Governance Compliance Auditor -- static report CLI.

Crawls active projects in your ACC account and writes a static HTML snapshot
to output/. For a live dashboard that re-queries ACC on every page load
instead of writing files, use `python server.py` instead.

Usage:
    python main.py                 run the full audit, write a static report
    python main.py --list-hubs     print available hub/account IDs and exit
    python main.py --project "2EA" only audit projects whose name contains this
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from src.audit import AuditContext, ContextError, timestamp
from src.acc_client import ACCApiError
from src.report import build_index_report, build_project_detail_report, safe_filename

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-hubs", action="store_true")
    parser.add_argument("--project", help="only audit projects whose name contains this substring")
    args = parser.parse_args()

    if args.list_hubs:
        # --list-hubs works even without ACC_ACCOUNT_ID set yet, so build just
        # enough of the context by hand rather than going through AuditContext.
        from src.auth import TokenProvider, AuthError
        from src.acc_client import ACCClient
        from dotenv import load_dotenv
        load_dotenv()
        try:
            client = ACCClient(TokenProvider(os.environ.get("APS_CLIENT_ID"), os.environ.get("APS_CLIENT_SECRET")))
        except AuthError as e:
            print(f"Auth error: {e}")
            sys.exit(1)
        for h in client.list_hubs():
            print(f"hub_id={h['id']}  name={h['attributes']['name']}  account_id={h['id'].removeprefix('b.')}")
        return

    try:
        ctx = AuditContext()
    except ContextError as e:
        print(f"Error: {e}")
        sys.exit(1)

    print("Fetching current project list from ACC (live)...")
    projects = ctx.list_active_projects(name_filter=args.project)
    print(f"{len(projects)} active production project(s) to audit.\n")

    run_dir = os.path.join(OUTPUT_DIR, f"report_{datetime.now().strftime('%Y%m%d_%H%M')}")
    projects_dir = os.path.join(run_dir, "projects")
    os.makedirs(projects_dir, exist_ok=True)
    gen_at = timestamp()

    all_summaries = []
    for p in projects:
        if not p["dm_id"]:
            print(f"  ! {p['name']}: not found via Data Management API (skipping folder/file crawl)")
            continue
        print(f"  Auditing {p['name']} ...")
        try:
            findings, summary = ctx.audit_project(p["name"], p["dm_id"])
        except ACCApiError as e:
            print(f"    API error: {e}")
            continue
        except Exception as e:
            print(f"    Unexpected error auditing {p['name']}: {e}")
            continue
        all_summaries.append(summary)
        counts = summary["issue_counts"]
        print(f"    critical={counts['critical']} serious={counts['serious']} warning={counts['warning']} "
              f"(matched code: {summary['project_code'] or 'NONE'})")

        detail_html = build_project_detail_report(summary, findings, gen_at, account_name=ctx.account_name)
        with open(os.path.join(projects_dir, safe_filename(p["name"])), "w", encoding="utf-8") as f:
            f.write(detail_html)

    index_html = build_index_report(all_summaries, gen_at, account_name=ctx.account_name)
    index_path = os.path.join(run_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_html)
    print(f"\nReport written to {index_path}")
    print(f"({len(all_summaries)} per-project detail files in {projects_dir})")


if __name__ == "__main__":
    main()
