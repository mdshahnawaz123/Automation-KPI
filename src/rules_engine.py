"""Matches an ACC project to its ECD naming-standard project code, then validates
folder structure, file naming, and required metadata against that code's rules."""

import json
import os
import re


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# 02_Shared/BIM/<folder> -> required file extension, or None if any extension is fine.
# Per ECD convention: Federated_NWD holds only combined Navisworks federated models
# (.nwd); IFC holds only IFC exchange files; Native Files and Navisworks Coordination
# legitimately hold mixed authoring formats, so no extension is enforced there.
SHARED_BIM_EXPECTED_EXTENSIONS = {
    "Federated_NWD": ".nwd",
    "IFC": ".ifc",
    "Native Files": None,
    "Navisworks Coordination": None,
}


def validate_shared_bim_item(folder_name, filename, description):
    """Checks one file inside 02_Shared/BIM/<folder_name> against the Description
    and file-type rules that apply specifically to that folder."""
    problems = []
    if not description or not str(description).strip():
        problems.append("Missing Description")

    expected_ext = SHARED_BIM_EXPECTED_EXTENSIONS.get(folder_name)
    if expected_ext:
        actual_ext = os.path.splitext(filename)[1].lower()
        if actual_ext != expected_ext:
            problems.append(f"Unexpected file type '{actual_ext or '(none)'}' in {folder_name} (expected {expected_ext})")

    return problems


def extract_leading_code(name):
    """ACC project names are 'CODE - Description' (e.g. '2EA - Expo City Apartments'),
    and contract/package subfolder names are 'CONTRACTNO - Description' (e.g.
    'C3018 - Lead Design Consultancy Service...'). Both share this shape."""
    m = re.match(r"\s*([A-Za-z0-9]{2,6})\s*[-–—]\s*", name or "")
    return m.group(1) if m else None


def match_project_to_code(project_name, config):
    """Primary match: the project's own name prefix IS its code (confirmed against
    the live ACC account -- e.g. '2EA - Expo City Apartments' -> '2EA')."""
    code = extract_leading_code(project_name)
    if code in config["projects"]:
        return code, None
    return None, code


def match_contract_package(folder_name, code, config):
    """Cross-checks a 'Project Files' subfolder (e.g. 'C3018 - Lead Design...')
    against the matched project code's known contract numbers. Returns
    (contract_no, is_recognised)."""
    contract_no = extract_leading_code(folder_name)
    if not contract_no:
        return None, False
    known = config["projects"].get(code, {}).get("contract_numbers", [])
    return contract_no, contract_no in known


def _dropdown_values(attr_def):
    if attr_def.get("kind") != "dropdown":
        return None
    return {v["value"] for v in attr_def["values"]}


def build_naming_regex(code_config, config):
    """Builds a regex that validates the hyphen-delimited filename segments in order,
    using each segment's actual allowed value set (or numeric/length spec for Number)."""
    attrs = code_config["attributes"]
    parts = []
    for seg in config["required_segments"]:
        attr = attrs.get(seg)
        if attr is None:
            parts.append(r"[^-]+")
        elif attr["kind"] == "text":
            length = attr.get("length")
            char_class = r"\d" if (attr.get("char_type") or "").lower() == "numeric" else r"[A-Za-z0-9]"
            parts.append(f"{char_class}{{{length}}}" if length else f"{char_class}+")
        else:
            values = sorted(_dropdown_values(attr), key=len, reverse=True)
            parts.append("(?:" + "|".join(re.escape(v) for v in values) + ")")

    required_pattern = config["delimiter"].join(parts)

    optional_parts = []
    for seg in config["optional_trailing_segments"]:
        attr = attrs.get(seg)
        if attr and attr["kind"] == "dropdown":
            values = sorted(_dropdown_values(attr), key=len, reverse=True)
            optional_parts.append("(?:" + "|".join(re.escape(v) for v in values) + ")")
        else:
            optional_parts.append(r"[^-_]+")

    # each optional trailing segment may or may not be present, but if present must
    # appear in order, each preceded by the delimiter
    trailing = "".join(f"(?:{config['delimiter']}{p})?" for p in optional_parts)
    return re.compile(r"^" + required_pattern + trailing + r"(?:[_.].*)?$")


def validate_filename(filename, code_config, config):
    """Returns list of human-readable problems (empty list = compliant)."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    regex = build_naming_regex(code_config, config)
    if regex.match(stem):
        return []

    segments = stem.split(config["delimiter"])
    attrs = code_config["attributes"]
    problems = []
    ordered_segments = config["required_segments"] + config["optional_trailing_segments"]
    for i, seg_name in enumerate(ordered_segments):
        if i >= len(segments):
            if seg_name in config["required_segments"]:
                problems.append(f"missing '{seg_name}' segment")
            continue
        value = segments[i].split("_")[0].split(".")[0]
        attr = attrs.get(seg_name)
        if attr is None:
            continue
        if attr["kind"] == "text":
            char_ok = value.isdigit() if (attr.get("char_type") or "").lower() == "numeric" else value.isalnum()
            length_ok = (not attr.get("length")) or len(value) == int(attr["length"])
            if not (char_ok and length_ok):
                problems.append(
                    f"segment {i + 1} ('{value}') should be a {attr.get('length')}-character "
                    f"{attr.get('char_type')} value for '{seg_name}'"
                )
        else:
            allowed = _dropdown_values(attr)
            if value not in allowed:
                problems.append(f"segment {i + 1} ('{value}') is not a recognised '{seg_name}' code")
    if not problems:
        problems.append(f"does not match the expected pattern: {config['delimiter'].join(ordered_segments)}")
    return problems


def validate_folder_structure(observed_paths, config, required=None):
    """observed_paths: set of relative folder paths actually seen under a project's
    top folders (e.g. '01_WIP/BIM/IFC'). Returns dict of missing/extra.
    Pass `required` to check against a subset of the template (e.g. shared_only_folder_template)
    instead of the full 01_WIP..06_Transmittals tree."""
    required = set(required if required is not None else config["folder_template"])
    observed = set(observed_paths)
    missing = sorted(required - observed)
    extra = sorted(p for p in observed - required if p.split("/")[0] in {seg.split("/")[0] for seg in required})
    return {"missing": missing, "extra": extra}


def shared_only_folder_template(config):
    """The subset of folder_template that lives under 02_Shared, with that
    prefix stripped -- used when a check is scoped to 02_Shared only (WIP is
    intentionally excluded: it holds draft/incomplete work not yet shared for
    coordination, so naming/metadata/folder-completeness standards apply once
    it lands in Shared, not before)."""
    prefix = "02_Shared/"
    return [p[len(prefix):] for p in config["folder_template"] if p.startswith(prefix)]


def missing_metadata_fields(custom_attributes_present, config):
    """custom_attributes_present: list of {name, value} dicts as returned by
    versions:batch-get (only attributes WITH a value are present in that list --
    absence from this list means missing/blank, confirmed by APS docs)."""
    present_names = {a["name"] for a in custom_attributes_present}
    return [f for f in config["metadata_required_fields"] if f not in present_names]
