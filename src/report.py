"""Builds the interactive HTML compliance report as a lightweight summary/index
page plus one detail page per project (each self-contained, no CDN deps) --
keeps individual files small enough to open/share even at full-account scale.

Palette/status colors follow the project's dataviz skill reference palette.
"""

import json
import re

SEVERITY_ORDER = ["critical", "serious", "warning"]
SEVERITY_LABEL = {"critical": "Critical", "serious": "Serious", "warning": "Warning"}

STATUS_COLORS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

# Reference categorical palette (fixed order -- see dataviz skill), reused here
# purely as a deterministic identity color per project code (avatar chips).
CATEGORICAL_PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


def _avatar_color(key):
    return CATEGORICAL_PALETTE[sum(ord(c) for c in (key or "?")) % len(CATEGORICAL_PALETTE)]

BASE_CSS = f"""
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    --surface-1: #ffffff; --surface-2: #fcfcfb; --page: #f4f4f2; --text-primary: #0b0b0b; --text-secondary: #52514e;
    --muted: #898781; --grid: #e6e5e0; --border: rgba(11,11,11,0.08); --accent: #2a78d6; --accent-dark: #184f95;
    --shadow-sm: 0 1px 2px rgba(20,20,15,0.04), 0 1px 1px rgba(20,20,15,0.03);
    --shadow-md: 0 2px 6px rgba(20,20,15,0.06), 0 8px 24px rgba(20,20,15,0.06);
    font-family: -apple-system, "Segoe UI Variable", "Segoe UI", system-ui, sans-serif;
    background: var(--page); color: var(--text-primary); margin: 0; padding: 0 0 64px;
    -webkit-font-smoothing: antialiased;
  }}
  @media (prefers-color-scheme: dark) {{
    body:not([data-theme="light"]) {{
      --surface-1: #1e1e1d; --surface-2: #1a1a19; --page: #101010; --text-primary: #ffffff; --text-secondary: #c3c2b7;
      --muted: #93918a; --grid: #333230; --border: rgba(255,255,255,0.08); --accent: #3987e5; --accent-dark: #86b6ef;
      --shadow-sm: 0 1px 2px rgba(0,0,0,0.3); --shadow-md: 0 4px 16px rgba(0,0,0,0.4);
    }}
  }}
  [data-theme="dark"] {{
    --surface-1: #1e1e1d; --surface-2: #1a1a19; --page: #101010; --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --muted: #93918a; --grid: #333230; --border: rgba(255,255,255,0.08); --accent: #3987e5; --accent-dark: #86b6ef;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.3); --shadow-md: 0 4px 16px rgba(0,0,0,0.4);
  }}
  header {{
    padding: 28px 32px; background: var(--surface-1); border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 10; backdrop-filter: blur(8px);
  }}
  .header-inner {{ max-width: 1280px; margin: 0 auto; display: flex; align-items: center; gap: 14px; }}
  .brand-mark {{
    width: 36px; height: 36px; border-radius: 10px; flex: none;
    background: linear-gradient(135deg, var(--accent), #4a3aa7);
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-weight: 700; font-size: 13px; letter-spacing: -0.02em; box-shadow: var(--shadow-sm);
  }}
  h1 {{ font-size: 19px; font-weight: 650; margin: 0; letter-spacing: -0.01em; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .subtitle {{ color: var(--text-secondary); font-size: 13px; margin-top: 1px; display: flex; align-items: center; gap: 7px; }}
  .live-dot {{ width: 7px; height: 7px; border-radius: 50%; background: {STATUS_COLORS['good']}; flex: none; box-shadow: 0 0 0 0 rgba(12,163,12,.5); animation: livepulse 2.2s ease-out infinite; }}
  @keyframes livepulse {{
    0%   {{ box-shadow: 0 0 0 0 rgba(12,163,12,.45); }}
    70%  {{ box-shadow: 0 0 0 6px rgba(12,163,12,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(12,163,12,0); }}
  }}
  main {{ padding: 28px 32px 0; max-width: 1280px; margin: 0 auto; }}
  @keyframes riseIn {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  .hero, .kpi-row, section {{ animation: riseIn .35s ease both; }}
  .kpi-row {{ animation-delay: .04s; }}
  section {{ animation-delay: .08s; }}
  .hero {{
    background: linear-gradient(155deg, var(--surface-1), var(--surface-2));
    border: 1px solid var(--border); border-radius: 16px; padding: 22px 24px; margin-bottom: 20px;
    box-shadow: var(--shadow-sm); display: flex; align-items: center; justify-content: space-between; gap: 20px; flex-wrap: wrap;
  }}
  .hero h2 {{ font-size: 16px; font-weight: 650; margin: 0 0 4px; }}
  .hero p {{ margin: 0; color: var(--text-secondary); font-size: 13px; max-width: 60ch; }}
  .kpi-row {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin: 0 0 24px; }}
  @media (max-width: 900px) {{ .kpi-row {{ grid-template-columns: repeat(2, 1fr); }} }}
  .tile {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px;
    box-shadow: var(--shadow-sm); border-top: 3px solid var(--grid); transition: transform .15s ease, box-shadow .15s ease;
  }}
  .tile:hover {{ transform: translateY(-1px); box-shadow: var(--shadow-md); }}
  .tile .value {{ font-size: 30px; font-weight: 700; font-variant-numeric: proportional-nums; letter-spacing: -0.02em; line-height: 1.1; }}
  .tile .label {{ font-size: 12px; color: var(--text-secondary); margin-top: 4px; font-weight: 500; }}
  .tile.critical {{ border-top-color: {STATUS_COLORS['critical']}; }}
  .tile.critical .value {{ color: {STATUS_COLORS['critical']}; }}
  .tile.serious {{ border-top-color: {STATUS_COLORS['serious']}; }}
  .tile.serious .value {{ color: {STATUS_COLORS['serious']}; }}
  .tile.warning {{ border-top-color: {STATUS_COLORS['warning']}; }}
  .tile.warning .value {{ color: #a86a00; }}
  .tile.good {{ border-top-color: {STATUS_COLORS['good']}; }}
  .tile.good .value {{ color: {STATUS_COLORS['good']}; }}
  section {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: 16px;
    padding: 22px 24px; margin-bottom: 20px; box-shadow: var(--shadow-sm);
  }}
  section h2 {{ font-size: 14px; font-weight: 650; margin: 0 0 16px; letter-spacing: -0.005em; }}
  .btn {{
    display: inline-flex; align-items: center; gap: 6px; background: var(--accent); color: #fff !important;
    border: none; border-radius: 9px; padding: 9px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
    text-decoration: none !important; box-shadow: var(--shadow-sm); transition: transform .12s ease, box-shadow .12s ease, opacity .12s ease;
  }}
  .btn:hover {{ transform: translateY(-1px); box-shadow: var(--shadow-md); opacity: .95; }}
  .btn.secondary {{ background: var(--surface-2); color: var(--text-primary) !important; border: 1px solid var(--border); }}
  .btn-row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 18px; }}
  .note {{ color: var(--text-secondary); font-size: 13px; line-height: 1.6; margin: 0 0 18px; }}
  .bar-row {{ display: grid; grid-template-columns: 280px 1fr 44px; align-items: center; gap: 14px; margin-bottom: 10px; font-size: 13px; }}
  .bar-label {{ color: var(--text-secondary); text-overflow: ellipsis; overflow: hidden; white-space: nowrap; }}
  .bar-label a {{ color: var(--text-primary); text-decoration: none; font-weight: 500; }}
  .bar-label a:hover {{ color: var(--accent); }}
  .bar-track {{ display: flex; height: 14px; background: var(--grid); border-radius: 4px; overflow: hidden; }}
  .bar-total {{ text-align: right; color: var(--text-secondary); font-variant-numeric: tabular-nums; font-weight: 600; }}
  .seg {{ height: 100%; }}
  .seg-critical {{ background: {STATUS_COLORS['critical']}; }}
  .seg-serious {{ background: {STATUS_COLORS['serious']}; }}
  .seg-warning {{ background: {STATUS_COLORS['warning']}; }}
  .seg-good {{ background: {STATUS_COLORS['good']}; border-radius: 4px; }}
  .legend {{ display: flex; gap: 18px; font-size: 12px; color: var(--text-secondary); margin-top: 14px; flex-wrap: wrap; padding-top: 14px; border-top: 1px solid var(--grid); }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 9px; height: 9px; border-radius: 2px; display: inline-block; }}
  .filters {{ display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }}
  .filters select, .filters input {{
    background: var(--surface-2); color: var(--text-primary); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 12px; font-size: 13px; font-family: inherit; transition: border-color .12s ease;
  }}
  .filters select:focus, .filters input:focus {{ outline: none; border-color: var(--accent); }}
  .filters input {{ flex: 1; min-width: 200px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--grid); }}
  th {{ color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; cursor: pointer; user-select: none; white-space: nowrap; }}
  th:hover {{ color: var(--text-primary); }}
  tbody tr {{ transition: background .1s ease; }}
  tbody tr:hover {{ background: var(--surface-2); }}
  td.path {{ font-family: ui-monospace, "SF Mono", Consolas, monospace; font-size: 12px; color: var(--text-secondary); }}
  .sev-pill {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 650; }}
  .sev-pill.critical {{ background: rgba(208,59,59,0.15); color: {STATUS_COLORS['critical']}; }}
  .sev-pill.serious {{ background: rgba(236,131,90,0.18); color: #b85a30; }}
  .sev-pill.warning {{ background: rgba(250,178,25,0.20); color: #a86a00; }}
  .conf-pill {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; background: var(--grid); color: var(--text-secondary); }}
  .conf-pill.low {{ background: rgba(250,178,25,0.20); color: #a86a00; }}
  .empty-state {{ text-align: center; color: var(--muted); padding: 40px 20px; }}
  .row-count {{ color: var(--muted); font-size: 12px; margin-bottom: 10px; font-weight: 500; }}

  .picker-search {{
    width: 100%; background: var(--surface-2); color: var(--text-primary); border: 1px solid var(--border);
    border-radius: 10px; padding: 11px 14px 11px 38px; font-size: 14px; font-family: inherit; margin-bottom: 12px;
    background-repeat: no-repeat; background-position: 12px center; background-size: 15px;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2398968f' stroke-width='2.2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cline x1='21' y1='21' x2='16.65' y2='16.65'/%3E%3C/svg%3E");
    transition: border-color .12s ease;
  }}
  .picker-search:focus {{ outline: none; border-color: var(--accent); }}
  .picker-list {{
    max-height: 420px; overflow-y: auto; border: 1px solid var(--grid); border-radius: 12px;
    scrollbar-width: thin; scrollbar-color: var(--grid) transparent;
  }}
  .picker-list::-webkit-scrollbar {{ width: 8px; }}
  .picker-list::-webkit-scrollbar-track {{ background: transparent; }}
  .picker-list::-webkit-scrollbar-thumb {{ background: var(--grid); border-radius: 8px; }}
  .picker-list::-webkit-scrollbar-thumb:hover {{ background: var(--muted); }}
  .picker-row {{
    display: flex; align-items: center; gap: 13px; padding: 11px 16px; cursor: pointer;
    border-bottom: 1px solid var(--grid); transition: background .1s ease;
  }}
  .picker-row:last-child {{ border-bottom: none; }}
  .picker-row:hover {{ background: var(--surface-2); }}
  .picker-avatar {{
    flex: none; width: 30px; height: 30px; border-radius: 9px; display: flex; align-items: center; justify-content: center;
    color: #fff; font-size: 11px; font-weight: 700; letter-spacing: -0.02em;
  }}
  .picker-main {{ flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 8px; }}
  .picker-name {{ font-weight: 550; font-size: 13.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .picker-code {{
    font-size: 10.5px; color: var(--muted); font-family: ui-monospace, monospace; flex: none;
    background: var(--grid); padding: 2px 7px; border-radius: 6px; letter-spacing: .02em;
  }}
  .picker-badge {{ flex: none; }}
  .picker-arrow {{ flex: none; color: var(--muted); opacity: 0; transform: translateX(-4px); transition: all .12s ease; font-size: 15px; }}
  .picker-row:hover .picker-arrow {{ opacity: 1; transform: translateX(0); color: var(--accent); }}
  .picker-empty {{ padding: 24px; text-align: center; color: var(--muted); font-size: 13px; }}
"""


def _severity_counts(findings):
    counts = {s: 0 for s in SEVERITY_ORDER}
    for f in findings:
        counts[f["severity"]] += 1
    return counts


def safe_filename(name):
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    return f"{slug[:80]}.html"


def _findings_table_section(findings, title="All findings", show_project_col=False):
    project_col = '<th data-key="project_name">Project</th>' if show_project_col else ""
    findings_json = json.dumps(findings, ensure_ascii=False)
    return f"""
  <section>
    <h2>{title}</h2>
    <div class="filters">
      {'<select id="f-project"><option value="">All projects</option></select>' if show_project_col else ''}
      <select id="f-category">
        <option value="">All issue types</option>
        <option value="folder_structure">Folder structure</option>
        <option value="naming">File naming</option>
        <option value="metadata">Metadata</option>
      </select>
      <select id="f-severity">
        <option value="">All severities</option>
        <option value="critical">Critical</option>
        <option value="serious">Serious</option>
        <option value="warning">Warning</option>
      </select>
      <input id="f-search" type="text" placeholder="Search path or detail...">
    </div>
    <div class="row-count" id="row-count"></div>
    <table id="findings-table">
      <thead>
        <tr>
          {project_col}
          <th data-key="category">Type</th>
          <th data-key="severity">Severity</th>
          <th data-key="path">Path / File</th>
          <th data-key="detail">Detail</th>
        </tr>
      </thead>
      <tbody id="findings-body"></tbody>
    </table>
    <div class="empty-state" id="empty-state" style="display:none">No findings match these filters.</div>
  </section>
  <script>
  const FINDINGS = {findings_json};
  const SHOW_PROJECT_COL = {str(show_project_col).lower()};
  const CATEGORY_LABEL = {{folder_structure: "Folder structure", naming: "File naming", metadata: "Metadata"}};
  let sortKey = "severity", sortDir = 1;
  const severityRank = {{critical: 0, serious: 1, warning: 2}};

  function populateProjectFilter() {{
    if (!SHOW_PROJECT_COL) return;
    const sel = document.getElementById("f-project");
    const names = [...new Set(FINDINGS.map(f => f.project_name))].sort();
    for (const n of names) {{
      const opt = document.createElement("option");
      opt.value = n; opt.textContent = n;
      sel.appendChild(opt);
    }}
  }}

  function currentFilters() {{
    return {{
      project: SHOW_PROJECT_COL ? document.getElementById("f-project").value : "",
      category: document.getElementById("f-category").value,
      severity: document.getElementById("f-severity").value,
      search: document.getElementById("f-search").value.trim().toLowerCase(),
    }};
  }}

  function render() {{
    const {{project, category, severity, search}} = currentFilters();
    let rows = FINDINGS.filter(f =>
      (!project || f.project_name === project) &&
      (!category || f.category === category) &&
      (!severity || f.severity === severity) &&
      (!search || f.path.toLowerCase().includes(search) || f.detail.toLowerCase().includes(search))
    );
    rows.sort((a, b) => {{
      let av = sortKey === "severity" ? severityRank[a.severity] : a[sortKey];
      let bv = sortKey === "severity" ? severityRank[b.severity] : b[sortKey];
      if (av < bv) return -1 * sortDir;
      if (av > bv) return 1 * sortDir;
      return 0;
    }});
    const tbody = document.getElementById("findings-body");
    tbody.innerHTML = "";
    const frag = document.createDocumentFragment();
    for (const f of rows) {{
      const tr = document.createElement("tr");
      tr.innerHTML = `
        ${{SHOW_PROJECT_COL ? `<td>${{f.project_name}}</td>` : ""}}
        <td>${{CATEGORY_LABEL[f.category] || f.category}}</td>
        <td><span class="sev-pill ${{f.severity}}">${{f.severity}}</span></td>
        <td class="path">${{f.path}}</td>
        <td>${{f.detail}}</td>`;
      frag.appendChild(tr);
    }}
    tbody.appendChild(frag);
    document.getElementById("row-count").textContent = `${{rows.length}} of ${{FINDINGS.length}} findings`;
    document.getElementById("empty-state").style.display = rows.length ? "none" : "block";
  }}

  document.querySelectorAll("#findings-table th").forEach(th => {{
    th.addEventListener("click", () => {{
      const key = th.dataset.key;
      if (sortKey === key) {{ sortDir *= -1; }} else {{ sortKey = key; sortDir = 1; }}
      render();
    }});
  }});
  ["f-project", "f-category", "f-severity"].forEach(id => {{
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", render);
  }});
  document.getElementById("f-search").addEventListener("input", render);
  populateProjectFilter();
  render();
  </script>"""


def _page(title, account_name, generated_at, body_html):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header>
  <div class="header-inner">
    <div class="brand-mark">ACC</div>
    <div>
      <h1>{title}</h1>
      <div class="subtitle"><span class="live-dot"></span>{account_name + ' &middot; ' if account_name else ''}Generated {generated_at}</div>
    </div>
  </div>
</header>
<main>
{body_html}
</main>
</body>
</html>"""


def build_picker(items, empty_message="No projects match your filter."):
    """A styled, searchable list of {url, name, code, badge_html} -- the
    modern replacement for a plain <select>, shared by the fast project
    picker and the dashboard's "jump to a project" section."""
    def _initials(it):
        code = it["code"] or it["name"]
        letters = "".join(ch for ch in code if ch.isalnum())[:2].upper()
        return letters or "?"

    rows_html = "".join(
        f'<div class="picker-row" data-search="{(it["name"] + " " + it["code"]).lower()}" '
        f'onclick="window.location.href=this.dataset.href" data-href="{it["url"]}">'
        f'<div class="picker-avatar" style="background:{_avatar_color(it["code"] or it["name"])}">{_initials(it)}</div>'
        f'<div class="picker-main"><span class="picker-name">{it["name"]}</span>'
        f'<span class="picker-code">{it["code"] or "unmatched"}</span></div>'
        f'<div class="picker-badge">{it.get("badge_html", "")}</div>'
        f'<span class="picker-arrow">&rarr;</span>'
        f'</div>'
        for it in items
    )
    return f"""
    <input class="picker-search" id="picker-search" type="text" placeholder="Search by project name or code...">
    <div class="picker-list" id="picker-list">{rows_html}</div>
    <div class="picker-empty" id="picker-empty" style="display:none">{empty_message}</div>
    <script>
      document.getElementById("picker-search").addEventListener("input", (e) => {{
        const q = e.target.value.trim().toLowerCase();
        let shown = 0;
        document.querySelectorAll("#picker-list .picker-row").forEach(row => {{
          const match = !q || row.dataset.search.includes(q);
          row.style.display = match ? "" : "none";
          if (match) shown++;
        }});
        document.getElementById("picker-empty").style.display = shown ? "none" : "block";
      }});
    </script>"""


def _default_project_link(project_summary):
    return f"projects/{safe_filename(project_summary['project_name'])}"


def build_index_report(projects_summary, generated_at, account_name="", project_link_fn=None, extra_header_html=""):
    """Lightweight dashboard: KPI tiles + per-project stacked bar (links to each
    project's detail page) -- no per-finding detail, stays small at any scale.

    project_link_fn(project_summary) -> url. Defaults to the static-file layout
    (projects/<name>.html); pass a different one to link into live routes instead."""
    link_fn = project_link_fn or _default_project_link
    total_projects = len(projects_summary)
    fully_compliant = sum(1 for p in projects_summary if sum(p["issue_counts"].values()) == 0)
    totals = {s: sum(p["issue_counts"][s] for p in projects_summary) for s in SEVERITY_ORDER}

    ranked = sorted(projects_summary, key=lambda p: sum(p["issue_counts"].values()), reverse=True)
    max_issues = max((sum(p["issue_counts"].values()) for p in ranked), default=0) or 1

    alphabetical = sorted(projects_summary, key=lambda p: p["project_name"])
    picker_items = [
        {
            "url": link_fn(p),
            "name": p["project_name"],
            "code": p["project_code"],
            "badge_html": (
                f'<span class="conf-pill low">needs confirmation</span>'
                if p.get("match_confidence") == "low_ambiguous_duplicate"
                else f'<span class="conf-pill">{sum(p["issue_counts"].values()) or "OK"} issue(s)</span>'
            ),
        }
        for p in alphabetical
    ]
    jump_section = f"""
  <section>
    <h2>Jump to a project</h2>
    {build_picker(picker_items)}
  </section>"""

    bars_html = ""
    for p in ranked:
        total = sum(p["issue_counts"].values())
        label = f"{p['project_name']} ({p['project_code'] or 'unmatched'})"
        conf = p.get("match_confidence")
        needs_confirmation = conf == "low_ambiguous_duplicate"
        conf_badge = ' <span class="conf-pill low">needs confirmation</span>' if needs_confirmation else ""
        segs = ""
        for sev in SEVERITY_ORDER:
            count = p["issue_counts"][sev]
            if count == 0:
                continue
            segs += f'<div class="seg seg-{sev}" style="width:{(count / max_issues) * 100:.2f}%" title="{SEVERITY_LABEL[sev]}: {count}"></div>'
        if total == 0:
            segs = '<div class="seg seg-good" style="width:3%"></div>'
        search_key = f"{p['project_name']} {p['project_code']}".lower().replace('"', "")
        status = "flagged" if total else "compliant"
        bars_html += f"""
        <div class="bar-row" data-search="{search_key}" data-status="{status}" data-confirm="{"1" if needs_confirmation else "0"}">
          <div class="bar-label"><a href="{link_fn(p)}">{label}</a>{conf_badge}</div>
          <div class="bar-track">{segs}</div>
          <div class="bar-total">{total if total else 'OK'}</div>
        </div>"""

    body = f"""
  {extra_header_html}
  <div class="kpi-row">
    <div class="tile"><div class="value">{total_projects}</div><div class="label">Projects Audited</div></div>
    <div class="tile good"><div class="value">{fully_compliant}</div><div class="label">Fully Compliant</div></div>
    <div class="tile critical"><div class="value">{totals['critical']}</div><div class="label">Critical (missing folders)</div></div>
    <div class="tile serious"><div class="value">{totals['serious']}</div><div class="label">Serious (naming errors)</div></div>
    <div class="tile warning"><div class="value">{totals['warning']}</div><div class="label">Warning (missing metadata)</div></div>
  </div>
  {jump_section}
  <section>
    <h2>Issues by project -- click a project to see full detail</h2>
    <div class="filters">
      <input id="proj-search" type="text" placeholder="Search project name or code...">
      <select id="proj-status">
        <option value="">All projects</option>
        <option value="flagged">Flagged only</option>
        <option value="compliant">Fully compliant only</option>
      </select>
      <select id="proj-confirm">
        <option value="">Any naming-standard confidence</option>
        <option value="1">Needs confirmation only</option>
      </select>
    </div>
    <div class="row-count" id="proj-row-count"></div>
    <div id="bars-container">{bars_html}</div>
    <div class="empty-state" id="proj-empty-state" style="display:none">No projects match these filters.</div>
    <div class="legend">
      <span><span class="swatch" style="background:{STATUS_COLORS['critical']}"></span>Critical -- missing required folder</span>
      <span><span class="swatch" style="background:{STATUS_COLORS['serious']}"></span>Serious -- file naming violation</span>
      <span><span class="swatch" style="background:{STATUS_COLORS['warning']}"></span>Warning -- missing required metadata</span>
      <span><span class="conf-pill low">needs confirmation</span> naming standard file for this code was ambiguous -- see console output</span>
    </div>
  </section>
  <script>
    function applyProjectFilters() {{
      const q = document.getElementById("proj-search").value.trim().toLowerCase();
      const status = document.getElementById("proj-status").value;
      const confirm = document.getElementById("proj-confirm").value;
      let shown = 0;
      const rows = document.querySelectorAll("#bars-container .bar-row");
      rows.forEach(row => {{
        const okSearch = !q || row.dataset.search.includes(q);
        const okStatus = !status || row.dataset.status === status;
        const okConfirm = !confirm || row.dataset.confirm === confirm;
        const show = okSearch && okStatus && okConfirm;
        row.style.display = show ? "" : "none";
        if (show) shown++;
      }});
      document.getElementById("proj-row-count").textContent = `${{shown}} of ${{rows.length}} projects`;
      document.getElementById("proj-empty-state").style.display = shown ? "none" : "block";
    }}
    document.getElementById("proj-search").addEventListener("input", applyProjectFilters);
    document.getElementById("proj-status").addEventListener("change", applyProjectFilters);
    document.getElementById("proj-confirm").addEventListener("change", applyProjectFilters);
    applyProjectFilters();
  </script>"""
    return _page("ACC Governance Compliance Report", account_name, generated_at, body)


def build_project_detail_report(project_summary, findings, generated_at, account_name="", back_link="../index.html",
                                 shared_bim_link=None, refresh_link=None, data_as_of=None):
    counts = project_summary["issue_counts"]
    as_of = f"Data as of {data_as_of}" if data_as_of else ""
    body = f"""
  <div class="btn-row">
    <a class="btn secondary" href="{back_link}">&larr; All projects</a>
    {f'<a class="btn secondary" href="{shared_bim_link}">Shared/BIM check</a>' if shared_bim_link else ''}
    {f'<a class="btn secondary" href="{refresh_link}">&#8635; Refresh this project</a>' if refresh_link else ''}
    {f'<span class="note" style="margin:0">{as_of}</span>' if as_of else ''}
  </div>
  <div class="kpi-row">
    <div class="tile critical"><div class="value">{counts['critical']}</div><div class="label">Critical</div></div>
    <div class="tile serious"><div class="value">{counts['serious']}</div><div class="label">Serious</div></div>
    <div class="tile warning"><div class="value">{counts['warning']}</div><div class="label">Warning</div></div>
    <div class="tile"><div class="value">{project_summary['project_code'] or '&mdash;'}</div><div class="label">Matched Code</div></div>
  </div>
  {_findings_table_section(findings, title="All findings", show_project_col=False)}"""
    return _page(project_summary["project_name"], account_name, generated_at, body)


def _format_size(n):
    if n is None:
        return ""
    n = float(n)
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def build_shared_bim_report(project_name, rows, generated_at, account_name="", back_link=".", full_audit_link=None, notify_link=None):
    flagged = sum(1 for r in rows if r["flags"])
    table_rows = ""
    for r in rows:
        flag_html = "".join(f'<span class="sev-pill warning">{f}</span> ' for f in r["flags"]) or '<span class="conf-pill">OK</span>'
        table_rows += f"""
        <tr data-folder="{r['folder']}" data-flagged="{"1" if r['flags'] else "0"}">
          <td>{r['package']}</td>
          <td>{r['folder']}</td>
          <td>{r['name']}</td>
          <td>{r['description'] or '<span style="color:#a86a00">(missing)</span>'}</td>
          <td>{r['version'] if r['version'] is not None else ''}</td>
          <td>{_format_size(r['size'])}</td>
          <td>{(r['last_updated'] or '')[:10]}</td>
          <td>{flag_html}</td>
        </tr>"""

    full_audit_html = f'<a class="btn secondary" href="{full_audit_link}">Run full audit (slow)</a>' if full_audit_link else ""
    notify_html = f'<button class="btn" id="notify-btn" onclick="sendNotifications(\'{notify_link}\')" style="background-color:var(--accent);color:white;border:none;">Send Notification Emails</button>' if notify_link else ""
    body = f"""
  <div class="btn-row">
    <a class="btn secondary" href="{back_link}">&larr; Project list</a>
    {full_audit_html}
    {notify_html}
    <span id="notify-status" style="margin-left:12px;font-size:13px;font-weight:500;color:var(--text-secondary);"></span>
  </div>
  <div class="kpi-row">
    <div class="tile"><div class="value">{len(rows)}</div><div class="label">Files Checked</div></div>
    <div class="tile warning"><div class="value">{flagged}</div><div class="label">Flagged</div></div>
    <div class="tile good"><div class="value">{len(rows) - flagged}</div><div class="label">OK</div></div>
  </div>
  <section>
    <h2>02_Shared/BIM -- Federated_NWD, IFC, Native Files, Navisworks Coordination</h2>
    <div class="filters">
      <select id="f-folder">
        <option value="">All folders</option>
        <option value="Federated_NWD">Federated_NWD</option>
        <option value="IFC">IFC</option>
        <option value="Native Files">Native Files</option>
        <option value="Navisworks Coordination">Navisworks Coordination</option>
      </select>
      <select id="f-flagged">
        <option value="">All files</option>
        <option value="1">Flagged only</option>
        <option value="0">OK only</option>
      </select>
    </div>
    <table id="bim-table">
      <thead>
        <tr><th>Package</th><th>Folder</th><th>Name</th><th>Description</th><th>Version</th><th>Size</th><th>Last Updated</th><th>Flags</th></tr>
      </thead>
      <tbody id="bim-body">{table_rows}</tbody>
    </table>
  </section>
  <script>
    function applyFilters() {{
      const folder = document.getElementById("f-folder").value;
      const flagged = document.getElementById("f-flagged").value;
      document.querySelectorAll("#bim-body tr").forEach(tr => {{
        const okFolder = !folder || tr.dataset.folder === folder;
        const okFlagged = !flagged || tr.dataset.flagged === flagged;
        tr.style.display = (okFolder && okFlagged) ? "" : "none";
      }});
    }}
    
    function sendNotifications(url) {{
      if (!url) return;
      const btn = document.getElementById("notify-btn");
      const status = document.getElementById("notify-status");
      btn.disabled = true;
      btn.textContent = "Drafting emails...";
      btn.style.opacity = "0.7";
      status.textContent = "";
      
      fetch(url, {{ method: "POST" }})
        .then(res => res.text())
        .then(text => {{
           status.textContent = text;
           btn.textContent = "Send Notification Emails";
           btn.disabled = false;
           btn.style.opacity = "1";
        }})
        .catch(err => {{
           status.textContent = "Error triggering emails.";
           btn.textContent = "Send Notification Emails";
           btn.disabled = false;
           btn.style.opacity = "1";
        }});
    }}
    document.getElementById("f-folder").addEventListener("change", applyFilters);
    document.getElementById("f-flagged").addEventListener("change", applyFilters);
  </script>"""
    return _page(f"{project_name} -- Shared/BIM check", account_name, generated_at, body)
