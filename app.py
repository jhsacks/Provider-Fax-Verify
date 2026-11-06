import os, re, json, secrets, uuid, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from drive_store import DriveStore, normalize

st.set_page_config(page_title="Provider Fax Verify", page_icon="📠", layout="centered")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
ROOT = Path(__file__).parent
LEGACY = ROOT / "legacy_providers.csv"
SN = "https://wellstar.service-now.com/esc?id=sc_cat_item&sys_id=ba15cfea839647d4b80fc0d6feaad316&table=sc_cat_item&searchTerm=update%20fax%20number%20provider"
NPI = "https://npiregistry.cms.hhs.gov/search"
API = "https://npiregistry.cms.hhs.gov/api/"


def digits(v):
    return re.sub(r"\D", "", str(v or ""))[-10:]


def fmt(v):
    d = digits(v)
    return f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 else ""


def clean(v):
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stat(v):
    v = clean(v)
    if v in ("epiccorrect", "correct", "green"):
        return "EPIC_CORRECT"
    if v in ("epicincorrect", "incorrect", "missing", "red"):
        return "EPIC_INCORRECT"
    return "VERIFY_NEEDED"


@st.cache_resource
def get_store():
    return DriveStore()


@st.cache_data(ttl=300, show_spinner=False)
def load():
    return get_store().load()


try:
    STORE = get_store()
    DATA = load()
    OK, ERR = True, ""
except Exception as e:
    STORE = None
    DATA = normalize({})
    OK, ERR = False, str(e)

DATA.setdefault("practices", [])
DATA.setdefault("providers", [])
DATA.setdefault("audit", [])
DATA.setdefault("review_queue", [])


def index():
    global PR, PV
    PR = {x.get("id"): x for x in DATA.get("practices", [])}
    PV = {x.get("id"): x for x in DATA.get("providers", [])}


index()


def practice(i):
    return PR.get(i, {})


def save(action, entity=""):
    global DATA
    DATA.setdefault("audit", []).append({"at": now(), "action": action, "entity": entity})
    DATA = STORE.save(DATA)
    load.clear()
    index()


def get_or_create_practice(name, city="", state="", fax="", phone=""):
    name = str(name or "").strip() or "Practice not recorded"
    city = str(city or "").strip()
    found = next(
        (
            x for x in DATA["practices"]
            if clean(x.get("name")) == clean(name)
            and (not city or clean(x.get("city")) == clean(city))
        ),
        None,
    )
    if found:
        found["active"] = True
        return found
    found = {
        "id": str(uuid.uuid4()),
        "name": name,
        "city": city,
        "state": str(state or "").strip(),
        "fax": digits(fax),
        "phone": digits(phone),
        "active": True,
        "created_at": now(),
    }
    DATA["practices"].append(found)
    index()
    return found


def create_provider_profile(values, practice_id):
    profile = {
        "id": str(uuid.uuid4()),
        "provider_name": str(values.get("provider_name", "")).strip(),
        "credentials": str(values.get("credentials", "")).strip(),
        "practice_id": practice_id,
        "npi": digits(values.get("npi")),
        "epic_status": stat(values.get("epic_status")),
        "epic_fax": digits(values.get("epic_fax")),
        "correct_fax": digits(values.get("correct_fax")),
        "notes": str(values.get("notes", "")).strip(),
        "active": True,
        "created_at": now(),
        "removed_at": "",
    }
    DATA["providers"].append(profile)
    index()
    return profile


def add_mem(r):
    name = str(r.get("provider_name", "")).strip()
    if not name:
        return "skipped"
    pr = get_or_create_practice(
        r.get("practice_name"), r.get("city"), r.get("state"),
        r.get("correct_fax"), r.get("phone")
    )
    # Imports update an exact existing provider/practice profile; UI additions always create a new profile.
    existing = next(
        (
            x for x in DATA["providers"]
            if clean(x.get("provider_name")) == clean(name)
            and x.get("practice_id") == pr["id"]
        ),
        None,
    )
    values = dict(r)
    if existing:
        updates = {
            "credentials": str(values.get("credentials", "")).strip(),
            "npi": digits(values.get("npi")),
            "epic_status": stat(values.get("epic_status")),
            "epic_fax": digits(values.get("epic_fax")),
            "correct_fax": digits(values.get("correct_fax")),
            "notes": str(values.get("notes", "")).strip(),
            "active": True,
            "removed_at": "",
        }
        for k, v in updates.items():
            if v not in ("", None):
                existing[k] = v
        index()
        return "updated"
    create_provider_profile(values, pr["id"])
    return "added"


def import_df(df, label):
    counts = {"added": 0, "updated": 0, "skipped": 0}
    for _, row in df.fillna("").iterrows():
        counts[add_mem(row.to_dict())] += 1
    save(label, json.dumps(counts))
    return counts


def search(q):
    needle = clean(q)
    if len(needle) < 2:
        return []
    out = []
    for p in DATA.get("providers", []):
        if not p.get("active", True):
            continue
        pr = practice(p.get("practice_id"))
        name, pname = p.get("provider_name", ""), pr.get("name", "")
        hay = clean(" ".join([name, pname, p.get("npi", ""), p.get("correct_fax", "")]))
        if needle in hay:
            status = stat(p.get("epic_status"))
            icon = {
                "EPIC_CORRECT": "✅",
                "EPIC_INCORRECT": "🔴",
                "VERIFY_NEEDED": "🟡",
            }.get(status, "⚪")
            label = f"{icon} {name}"
            if status == "EPIC_INCORRECT":
                fax = fmt(p.get("correct_fax"))
                if fax:
                    label += f" | USE {fax}"
            if pname != "Practice not recorded":
                label += f" — {pname}"
            out.append((0 if clean(name).startswith(needle) else 1, label, "p", p["id"]))
    for pr in DATA.get("practices", []):
        if pr.get("active", True) and pr.get("name") != "Practice not recorded" and needle in clean(pr.get("name")):
            correct, incorrect, verify_needed = practice_summary(pr["id"])
            label = f"Practice: {pr.get('name')} | ✅ {correct}  🔴 {incorrect}  🟡 {verify_needed}"
            out.append((0 if clean(pr.get("name")).startswith(needle) else 1, label, "r", pr["id"]))
    return sorted(out, key=lambda x: (x[0], clean(x[1])))[:12]


def ticket_label(v):
    return {
        "NOT_SUBMITTED": "Not submitted", "SUBMITTED": "Submitted with ticket",
        "IN_PROGRESS": "In progress", "AWAITING_EPIC_UPDATE": "Awaiting Epic update",
        "CORRECTED": "Epic corrected", "CLOSED": "Closed",
    }.get(v or "NOT_SUBMITTED", "Not submitted")


def practice_summary(practice_id):
    counts = {"EPIC_CORRECT": 0, "EPIC_INCORRECT": 0, "VERIFY_NEEDED": 0}
    for p in DATA.get("providers", []):
        if p.get("active", True) and p.get("practice_id") == practice_id:
            counts[stat(p.get("epic_status"))] += 1
    return counts["EPIC_CORRECT"], counts["EPIC_INCORRECT"], counts["VERIFY_NEEDED"]


def outstanding(p):
    return p.get("active", True) and stat(p.get("epic_status")) == "EPIC_INCORRECT" and p.get("correction_status", "NOT_SUBMITTED") not in ("CORRECTED", "CLOSED")


def split_name(n):
    base = re.sub(r",?\s*(MD|DO|NP|APRN|PA-C|PA|FNP-C|FNP|CPNP|DNP|FAAP)\b.*$", "", str(n), flags=re.I).strip()
    parts = base.split()
    return (parts[0], parts[-1]) if len(parts) > 1 else (base, "")


def verify(p):
    first, last = split_name(p.get("provider_name"))
    pr = practice(p.get("practice_id"))
    params = {"version": "2.1", "limit": "10", "enumeration_type": "NPI-1"}
    if p.get("npi"):
        params["number"] = p["npi"]
    else:
        params.update({"first_name": first, "last_name": last})
        if pr.get("state"):
            params["state"] = pr["state"]
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "ProviderFaxVerify/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = json.loads(response.read().decode())
    faxes = sorted({digits(a.get("fax_number")) for r in raw.get("results", []) for a in (r.get("addresses", []) or []) if digits(a.get("fax_number"))})
    stored = digits(p.get("correct_fax"))
    result = "FAX_MATCH" if stored and stored in faxes else ("REVIEW_NEEDED" if faxes else "NO_FAX_FOUND")
    p.update({"last_verified": now(), "verification_result": result, "registry_faxes": faxes, "verification_source": "NPPES API 2.1"})
    if result == "REVIEW_NEEDED":
        for fax in faxes:
            if not any(x.get("provider_id") == p["id"] and x.get("found_fax") == fax and x.get("status", "pending") == "pending" for x in DATA["review_queue"]):
                DATA["review_queue"].append({"id": str(uuid.uuid4()), "provider_id": p["id"], "current_fax": stored, "found_fax": fax, "status": "pending", "checked_at": now(), "source": "NPPES API 2.1"})
    save("npi_verification", p["id"])
    return result


def edit_profile(p):
    pr = practice(p.get("practice_id"))
    with st.expander("Edit provider profile"):
        with st.form("edit_" + p["id"]):
            c1, c2 = st.columns(2)
            name = c1.text_input("Provider name*", p.get("provider_name", ""))
            credentials = c2.text_input("Credentials", p.get("credentials", ""))
            npi = st.text_input("NPI", p.get("npi", ""))
            status_opts = ["EPIC_CORRECT", "EPIC_INCORRECT", "VERIFY_NEEDED"]
            current = stat(p.get("epic_status"))
            epic_status = st.radio("Epic fax status", status_opts, index=status_opts.index(current), horizontal=True)
            c3, c4 = st.columns(2)
            epic_fax = c3.text_input("Epic fax", fmt(p.get("epic_fax")))
            correct_fax = c4.text_input("Correct fax", fmt(p.get("correct_fax")))
            notes = st.text_area("Notes", p.get("notes", ""))
            if st.form_submit_button("Save provider changes", use_container_width=True, disabled=not OK):
                if not name.strip():
                    st.error("Provider name is required.")
                elif epic_status == "EPIC_INCORRECT" and len(digits(correct_fax)) != 10:
                    st.error("A 10-digit correct fax is required.")
                else:
                    p.update({"provider_name": name.strip(), "credentials": credentials.strip(), "npi": digits(npi), "epic_status": epic_status, "epic_fax": digits(epic_fax), "correct_fax": digits(correct_fax), "notes": notes.strip(), "updated_at": now()})
                    save("provider_edited", p["id"])
                    st.session_state.notice = "Provider profile updated."
                    st.rerun()
        st.caption(f"Current practice: {pr.get('name', 'Practice not recorded')}")
        if st.button("Remove provider from this practice", key="remove_" + p["id"], use_container_width=True):
            st.session_state.confirm_remove = p["id"]
        if st.session_state.get("confirm_remove") == p["id"]:
            st.warning(f"Remove {p.get('provider_name')} from {pr.get('name', 'this practice')}? The profile will be retained as inactive history.")
            a, b = st.columns(2)
            if a.button("Confirm removal", key="confirm_" + p["id"], type="primary", use_container_width=True):
                p.update({"active": False, "removed_at": now()})
                save("provider_removed_from_practice", p["id"])
                st.session_state.kind = ""
                st.session_state.pick = ""
                st.session_state.confirm_remove = ""
                st.session_state.notice = "Provider removed from the practice. Historical data was retained."
                st.rerun()
            if b.button("Cancel", key="cancel_" + p["id"], use_container_width=True):
                st.session_state.confirm_remove = ""
                st.rerun()


def card(p, allow_edit=True):
    pr = practice(p.get("practice_id"))
    status = stat(p.get("epic_status"))
    name = p.get("provider_name", "") + (f", {p.get('credentials')}" if p.get("credentials") else "")
    pname = "" if pr.get("name") == "Practice not recorded" else pr.get("name", "")
    if status == "EPIC_CORRECT":
        st.markdown(f"<div class='card green'><div class='status'>✅ FAX IN EPIC CORRECT</div><h2>{name}</h2><div>{pname}</div><h3>Route normally through Epic.</h3></div>", unsafe_allow_html=True)
    elif status == "EPIC_INCORRECT":
        fax = fmt(p.get("correct_fax"))
        st.markdown(f"<div class='card red'><div class='status'>🔴 FAX IN EPIC INCORRECT</div><h2>{name}</h2><div>{pname}</div><b>USE THIS FAX</b><div class='fax'>{fax or 'FAX NEEDED'}</div></div>", unsafe_allow_html=True)
        if fax:
            st.code(fax, language=None)
        st.write(f"**Correction status:** {ticket_label(p.get('correction_status'))}")
        with st.expander("Update correction ticket"):
            with st.form("ticket_" + p["id"]):
                opts = ["NOT_SUBMITTED", "SUBMITTED", "IN_PROGRESS", "AWAITING_EPIC_UPDATE", "CORRECTED", "CLOSED"]
                current = p.get("correction_status", "NOT_SUBMITTED")
                cs = st.selectbox("Status", opts, index=opts.index(current) if current in opts else 0)
                tn = st.text_input("ServiceNow ticket number", p.get("ticket_number", ""))
                td = st.text_input("Submitted date", p.get("ticket_submitted_date", ""), placeholder="YYYY-MM-DD")
                note = st.text_input("Status update", p.get("ticket_note", ""))
                if st.form_submit_button("Save ticket status", use_container_width=True):
                    p.update({"correction_status": cs, "ticket_number": tn.strip(), "ticket_submitted_date": td.strip(), "ticket_note": note.strip(), "ticket_updated_at": now()})
                    save("ticket_updated", p["id"])
                    st.session_state.notice = "Ticket status saved."
                    st.rerun()
    else:
        st.markdown(f"<div class='card yellow'><div class='status'>🟡 EPIC FAX NEEDS VERIFICATION</div><h2>{name}</h2><div>{pname}</div></div>", unsafe_allow_html=True)
    if allow_edit:
        edit_profile(p)


def add_provider_form(form_key, preset_practice=None):
    label = preset_practice.get("name") if preset_practice else ""
    with st.form(form_key, clear_on_submit=True):
        c1, c2 = st.columns(2)
        provider_name = c1.text_input("Provider name*")
        credentials = c2.text_input("Credentials")
        practice_name = st.text_input("Practice name*", value=label, disabled=bool(preset_practice))
        npi = st.text_input("NPI")
        epic_status = st.radio("Epic fax status", ["EPIC_CORRECT", "EPIC_INCORRECT", "VERIFY_NEEDED"], horizontal=True)
        c3, c4 = st.columns(2)
        epic_fax = c3.text_input("Epic fax")
        correct_fax = c4.text_input("Correct fax")
        city = st.text_input("City", value=(preset_practice or {}).get("city", ""))
        state = st.text_input("State", value=(preset_practice or {}).get("state", "GA") or "GA")
        notes = st.text_area("Notes")
        if st.form_submit_button("Create provider profile", use_container_width=True, disabled=not OK):
            final_practice_name = (preset_practice or {}).get("name", practice_name)
            if not provider_name.strip() or not final_practice_name.strip():
                st.error("Provider and practice are required.")
            elif epic_status == "EPIC_INCORRECT" and len(digits(correct_fax)) != 10:
                st.error("A 10-digit correct fax is required.")
            else:
                pr = preset_practice or get_or_create_practice(final_practice_name, city, state, correct_fax, "")
                p = create_provider_profile(locals(), pr["id"])
                save("provider_profile_created", p["id"])
                st.session_state.notice = f"New provider profile created for {pr.get('name')}."
                st.rerun()


st.markdown("""<style>.block-container{max-width:860px;padding-top:2rem}.card{border:1px solid #ddd;border-radius:18px;padding:25px;margin:14px 0;background:white}.green{border-left:14px solid #20894e}.red{border-left:14px solid #c93545}.yellow{border-left:14px solid #d99b00}.status{font-size:25px;font-weight:900}.fax{font-size:44px;font-weight:900}.stButton button{min-height:44px;font-weight:750;text-align:left}</style>""", unsafe_allow_html=True)

st.title("📠 Provider Fax Verify")
st.caption("☁️ Google Drive saving is on" if OK else "")
if not OK:
    st.error("Saving is not connected. " + ERR)

for key in ("kind", "pick", "confirm_remove"):
    st.session_state.setdefault(key, "")

q = st.text_input("Search provider or practice", key="q", placeholder="Start typing a provider or practice...")
if q:
    matches = search(q)
    if not matches:
        st.warning("No matching provider or practice found.")
    for i, (_, label, kind, pid) in enumerate(matches):
        if st.button(label, key=f"m{i}{pid}", use_container_width=True):
            st.session_state.kind, st.session_state.pick = kind, pid
            st.rerun()

if st.session_state.kind == "p" and st.session_state.pick in PV:
    card(PV[st.session_state.pick])
elif st.session_state.kind == "r" and st.session_state.pick in PR:
    selected_practice = PR[st.session_state.pick]
    st.subheader(selected_practice.get("name", "Practice"))
    active_profiles = sorted([x for x in DATA["providers"] if x.get("practice_id") == selected_practice["id"] and x.get("active", True)], key=lambda x: clean(x.get("provider_name")))
    if not active_profiles:
        st.info("No active providers are currently assigned to this practice.")
    for p in active_profiles:
        card(p)
    with st.expander("➕ Add another provider to this practice", expanded=not active_profiles):
        st.caption("This creates a separate provider profile assigned to this practice.")
        add_provider_form("add_to_practice_" + selected_practice["id"], selected_practice)

if q or st.session_state.pick:
    if st.button("Clear search", use_container_width=True):
        st.session_state.q = ""
        st.session_state.kind = ""
        st.session_state.pick = ""
        st.rerun()

if "notice" in st.session_state:
    st.success(st.session_state.pop("notice"))

with st.expander("Add a provider not on the list"):
    a, b = st.columns(2)
    a.link_button("Submit Epic fax update", SN, use_container_width=True)
    b.link_button("Search NPI Registry", NPI, use_container_width=True)
    add_provider_form("add_general")

with st.sidebar:
    st.header("Admin")
    pw = st.text_input("Admin password", type="password")
    if ADMIN_PASSWORD and secrets.compare_digest(pw, ADMIN_PASSWORD):
        st.success("Admin unlocked")
        outs = sorted([p for p in DATA["providers"] if outstanding(p)], key=lambda p: clean(p.get("provider_name")))
        st.subheader(f"Outstanding Epic correction tickets ({len(outs)})")
        if not outs:
            st.caption("No outstanding Epic correction tickets.")
        for p in outs:
            pr = practice(p.get("practice_id"))
            st.markdown(f"**{p.get('provider_name')}**" + (f" — {pr.get('name')}" if pr.get("name") != "Practice not recorded" else ""))
            st.write(f"{ticket_label(p.get('correction_status'))} | Ticket: {p.get('ticket_number') or 'Not entered'} | Fax: {fmt(p.get('correct_fax')) or 'Not entered'}")
        if LEGACY.exists() and st.button("Load built-in provider directory", use_container_width=True):
            counts = import_df(pd.read_csv(LEGACY, dtype=str), "legacy_import")
            st.success(str(counts))
            st.rerun()
        st.subheader("NPI verification")
        batch = st.number_input("Providers to check", 1, 50, 10)
        if st.button("Auto-check next providers", use_container_width=True):
            items = sorted([p for p in DATA["providers"] if p.get("active", True)], key=lambda x: x.get("last_verified", ""))[:int(batch)]
            for p in items:
                try:
                    verify(p)
                except Exception:
                    pass
            st.rerun()
        pending = [x for x in DATA.get("review_queue", []) if x.get("status", "pending") == "pending" and PV.get(x.get("provider_id"), {}).get("active", True)]
        st.subheader(f"Verification review ({len(pending)})")
        for item in pending[:50]:
            p = PV.get(item.get("provider_id"), {})
            st.write(f"**{p.get('provider_name', 'Provider')}** Stored: {fmt(item.get('current_fax')) or 'none'} | Registry: {fmt(item.get('found_fax'))}")
            a, b = st.columns(2)
            if a.button("Accept registry fax", key="a" + item["id"]):
                p["correct_fax"] = digits(item["found_fax"])
                item["status"] = "accepted"
                save("fax_accepted", item["id"])
                st.rerun()
            if b.button("Keep stored fax", key="r" + item["id"]):
                item["status"] = "rejected"
                save("fax_rejected", item["id"])
                st.rerun()
        rows = []
        for p in DATA["providers"]:
            pr = practice(p.get("practice_id"))
            rows.append({
                "provider_name": p.get("provider_name"), "practice_name": pr.get("name"),
                "profile_active": p.get("active", True), "removed_at": p.get("removed_at", ""),
                "npi": p.get("npi", ""), "epic_status": stat(p.get("epic_status")),
                "correct_fax": fmt(p.get("correct_fax")), "correction_status": p.get("correction_status", "NOT_SUBMITTED"),
                "ticket_number": p.get("ticket_number", ""), "ticket_submitted_date": p.get("ticket_submitted_date", ""),
                "ticket_note": p.get("ticket_note", ""),
            })
        export = pd.DataFrame(rows)
        st.download_button("Download directory and tickets", export.to_csv(index=False).encode(), "provider_fax_directory.csv", "text/csv", use_container_width=True)
    elif pw:
        st.error("Incorrect password")
