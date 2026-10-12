import os
import re
import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from drive_store import DriveStore, normalize

st.set_page_config(page_title="Provider Fax Verify", page_icon="📠", layout="centered")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
ROOT = Path(__file__).resolve().parent
LEGACY = ROOT / "legacy_providers.csv"


def digits(value):
    return re.sub(r"\D", "", str(value or ""))[-10:]


def fmt(value):
    number = digits(value)
    return f"{number[:3]}-{number[3:6]}-{number[6:]}" if len(number) == 10 else ""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def canonical_status(value):
    value = clean(value)
    if value in ("epiccorrect", "correct", "green"):
        return "EPIC_CORRECT"
    if value in ("epicincorrect", "incorrect", "missing", "red"):
        return "EPIC_INCORRECT"
    return "VERIFY_NEEDED"


@st.cache_resource
def get_store():
    return DriveStore()


@st.cache_data(ttl=300, show_spinner=False)
def load_drive_data():
    return get_store().load()


try:
    STORE = get_store()
    DATA = load_drive_data()
    STORAGE_OK = True
    STORAGE_ERROR = ""
except Exception as exc:
    STORE = None
    DATA = normalize({})
    STORAGE_OK = False
    STORAGE_ERROR = str(exc)


def persist(action="", entity=""):
    global DATA
    DATA.setdefault("audit", []).append({"at": now(), "action": action, "entity": entity})
    DATA = STORE.save(DATA) if STORE else DATA
    load_drive_data.clear()


# Build fast in-memory indexes once per rerun. No network call is involved.
PRACTICE_BY_ID = {item.get("id"): item for item in DATA.get("practices", [])}
PROVIDER_BY_ID = {item.get("id"): item for item in DATA.get("providers", [])}


def practice(practice_id):
    return PRACTICE_BY_ID.get(practice_id, {})


def provider_key(name, practice_name):
    return clean(name) + "|" + clean(practice_name)


def rebuild_indexes():
    global PRACTICE_BY_ID, PROVIDER_BY_ID
    PRACTICE_BY_ID = {item.get("id"): item for item in DATA.get("practices", [])}
    PROVIDER_BY_ID = {item.get("id"): item for item in DATA.get("providers", [])}


def add_provider_to_memory(row):
    name = str(row.get("provider_name", "")).strip()
    practice_name = str(row.get("practice_name", "")).strip() or "Practice not recorded"
    city = str(row.get("city", "")).strip()
    if not name:
        return "skipped"

    current_practice = next(
        (
            item
            for item in DATA["practices"]
            if clean(item.get("name")) == clean(practice_name)
            and (not city or clean(item.get("city")) == clean(city))
        ),
        None,
    )

    if not current_practice:
        current_practice = {
            "id": str(uuid.uuid4()),
            "name": practice_name,
            "city": city,
            "state": str(row.get("state", "")).strip(),
            "fax": digits(row.get("correct_fax")),
            "phone": digits(row.get("phone")),
            "active": True,
        }
        DATA["practices"].append(current_practice)

    rebuild_indexes()
    existing = next(
        (
            item
            for item in DATA["providers"]
            if provider_key(item.get("provider_name"), practice(item.get("practice_id")).get("name"))
            == provider_key(name, practice_name)
        ),
        None,
    )

    payload = {
        "provider_name": name,
        "credentials": str(row.get("credentials", "")).strip(),
        "practice_id": current_practice["id"],
        "npi": digits(row.get("npi")),
        "epic_status": canonical_status(row.get("epic_status")),
        "epic_fax": digits(row.get("epic_fax")),
        "correct_fax": digits(row.get("correct_fax")),
        "source_url": str(row.get("source_url", "")).strip(),
        "notes": str(row.get("notes", "")).strip(),
        "active": True,
    }

    if existing:
        for key, value in payload.items():
            if value not in ("", None):
                existing[key] = value
        rebuild_indexes()
        return "updated"

    payload["id"] = str(uuid.uuid4())
    DATA["providers"].append(payload)
    rebuild_indexes()
    return "added"


def import_dataframe(frame, action):
    counts = {"added": 0, "updated": 0, "skipped": 0}
    for _, row in frame.fillna("").iterrows():
        counts[add_provider_to_memory(row.to_dict())] += 1
    persist(action, json.dumps(counts))
    return counts


def search_results(query, limit=12):
    needle = clean(query)
    if len(needle) < 2:
        return []

    matches = []
    for provider in DATA.get("providers", []):
        if not provider.get("active", True):
            continue
        provider_practice = practice(provider.get("practice_id"))
        provider_name = provider.get("provider_name", "")
        practice_name = provider_practice.get("name", "")
        searchable = clean(" ".join([provider_name, practice_name, provider.get("npi", ""), provider.get("correct_fax", "")]))
        if needle in searchable:
            starts = clean(provider_name).startswith(needle)
            label = f"{provider_name}"
            if practice_name and practice_name != "Practice not recorded":
                label += f" — {practice_name}"
            matches.append((0 if starts else 1, label, "provider", provider.get("id")))

    for provider_practice in DATA.get("practices", []):
        practice_name = provider_practice.get("name", "")
        if (
            provider_practice.get("active", True)
            and practice_name != "Practice not recorded"
            and needle in clean(practice_name)
        ):
            starts = clean(practice_name).startswith(needle)
            matches.append((0 if starts else 1, f"Practice: {practice_name}", "practice", provider_practice.get("id")))

    matches.sort(key=lambda item: (item[0], clean(item[1])))
    return matches[:limit]


def provider_card(provider):
    provider_practice = practice(provider.get("practice_id"))
    status = canonical_status(provider.get("epic_status"))
    name = provider.get("provider_name", "")
    credentials = provider.get("credentials", "")
    practice_name = provider_practice.get("name", "")
    display_name = name + (f", {credentials}" if credentials else "")
    display_practice = practice_name if practice_name != "Practice not recorded" else ""

    if status == "EPIC_CORRECT":
        st.markdown(
            f"""
            <div class='card green'>
                <div class='status'>✅ FAX IN EPIC CORRECT</div>
                <h2>{display_name}</h2>
                <div class='muted'>{display_practice}</div>
                <div class='route'>Route normally through Epic.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif status == "EPIC_INCORRECT":
        fax = fmt(provider.get("correct_fax"))
        st.markdown(
            f"""
            <div class='card red'>
                <div class='status'>🔴 FAX IN EPIC INCORRECT</div>
                <h2>{display_name}</h2>
                <div class='muted'>{display_practice}</div>
                <div class='use'>USE THIS FAX</div>
                <div class='fax'>{fax or 'FAX NEEDED'}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if fax:
            st.code(fax, language=None)
    else:
        st.markdown(
            f"""
            <div class='card yellow'>
                <div class='status'>🟡 EPIC FAX NEEDS VERIFICATION</div>
                <h2>{display_name}</h2>
                <div class='muted'>{display_practice}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


st.markdown(
    """
    <style>
    .block-container{max-width:860px;padding-top:2rem}
    .card{border:1px solid #dce2e8;border-radius:18px;padding:25px;margin:14px 0;background:#fff;box-shadow:0 3px 14px #0000000d}
    .red{border-left:14px solid #c93545}
    .green{border-left:14px solid #20894e}
    .yellow{border-left:14px solid #d99b00}
    .fax{font-size:44px;font-weight:900;letter-spacing:1px;margin:5px 0}
    .status{font-size:25px;font-weight:900}
    .route{font-size:20px;font-weight:700;margin-top:14px}
    .use{font-size:18px;font-weight:850;margin-top:14px}
    .muted{color:#66727f}
    .stButton button{min-height:44px;font-weight:750;text-align:left}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📠 Provider Fax Verify")
if STORAGE_OK:
    st.caption("☁️ Google Drive saving is on")
else:
    st.error("Saving is not connected. " + STORAGE_ERROR)

if "selected_kind" not in st.session_state:
    st.session_state.selected_kind = ""
if "selected_id" not in st.session_state:
    st.session_state.selected_id = ""

query = st.text_input(
    "Search provider or practice",
    key="provider_search",
    placeholder="Start typing a provider or practice...",
)

if query:
    matches = search_results(query)
    if not matches:
        st.warning("No matching provider or practice found.")
    else:
        for index, (_, label, kind, item_id) in enumerate(matches):
            if st.button(label, key=f"match_{kind}_{item_id}_{index}", use_container_width=True):
                st.session_state.selected_kind = kind
                st.session_state.selected_id = item_id
                st.rerun()

selected_kind = st.session_state.selected_kind
selected_id = st.session_state.selected_id

if selected_kind == "provider" and selected_id in PROVIDER_BY_ID:
    provider_card(PROVIDER_BY_ID[selected_id])
elif selected_kind == "practice" and selected_id in PRACTICE_BY_ID:
    selected_practice = PRACTICE_BY_ID[selected_id]
    st.subheader(selected_practice.get("name", ""))
    members = [
        item
        for item in DATA.get("providers", [])
        if item.get("practice_id") == selected_id and item.get("active", True)
    ]
    for provider in sorted(members, key=lambda item: clean(item.get("provider_name"))):
        provider_card(provider)

if query or selected_id:
    if st.button("Clear search", use_container_width=True):
        st.session_state.provider_search = ""
        st.session_state.selected_kind = ""
        st.session_state.selected_id = ""
        st.rerun()

if "add_notice" in st.session_state:
    st.success(st.session_state.pop("add_notice"))

with st.expander("Add a provider not on the list"):
    if not STORAGE_OK:
        st.warning("Connect Google Drive before adding permanent records.")
    with st.form("add", clear_on_submit=True):
        column_a, column_b = st.columns(2)
        provider_name = column_a.text_input("Provider name*")
        credentials = column_b.text_input("Credentials")
        practice_name = st.text_input("Practice name*")
        epic_status = st.radio(
            "Epic fax status",
            ["EPIC_CORRECT", "EPIC_INCORRECT", "VERIFY_NEEDED"],
            horizontal=True,
        )
        correct_fax = st.text_input("Correct fax (required if Epic is incorrect)")
        column_c, column_d = st.columns(2)
        city = column_c.text_input("City")
        state = column_d.text_input("State", value="GA")
        submitted = st.form_submit_button(
            "Add provider", use_container_width=True, disabled=not STORAGE_OK
        )
        if submitted:
            if not provider_name or not practice_name:
                st.error("Provider and practice are required.")
            elif epic_status == "EPIC_INCORRECT" and len(digits(correct_fax)) != 10:
                st.error("A 10-digit correct fax is required when Epic is incorrect.")
            else:
                add_provider_to_memory(
                    {
                        "provider_name": provider_name,
                        "credentials": credentials,
                        "practice_name": practice_name,
                        "epic_status": epic_status,
                        "correct_fax": correct_fax,
                        "city": city,
                        "state": state,
                    }
                )
                persist("provider_added", provider_name)
                st.session_state.add_notice = "Provider saved to Google Drive. The form was cleared."
                st.rerun()

with st.sidebar:
    st.header("Admin")
    password = st.text_input("Admin password", type="password")
    if ADMIN_PASSWORD and secrets.compare_digest(password, ADMIN_PASSWORD):
        st.success("Admin unlocked")

        if LEGACY.exists() and st.button(
            "Load built-in provider directory", use_container_width=True, disabled=not STORAGE_OK
        ):
            counts = import_dataframe(
                pd.read_csv(LEGACY, dtype=str), "legacy_directory_import"
            )
            st.success(
                f"Added {counts['added']}, updated {counts['updated']}, skipped {counts['skipped']}"
            )
            st.rerun()

        uploaded = st.file_uploader("Import CSV or XLSX", type=["csv", "xlsx"])
        if uploaded:
            if uploaded.name.lower().endswith(".csv"):
                frame = pd.read_csv(uploaded, dtype=str).fillna("")
            else:
                frame = pd.read_excel(uploaded, dtype=str).fillna("")
            st.dataframe(frame.head(8), use_container_width=True)
            if st.button("Import uploaded file", use_container_width=True, disabled=not STORAGE_OK):
                required = {"provider_name", "practice_name", "epic_status"}
                if not required.issubset(frame.columns):
                    st.error("Missing: " + ", ".join(sorted(required - set(frame.columns))))
                else:
                    counts = import_dataframe(frame, "uploaded_directory_import")
                    st.success(
                        f"Added {counts['added']}, updated {counts['updated']}, skipped {counts['skipped']}"
                    )
                    st.rerun()

        rows = []
        for provider in DATA.get("providers", []):
            provider_practice = practice(provider.get("practice_id"))
            rows.append(
                {
                    "provider_name": provider.get("provider_name"),
                    "credentials": provider.get("credentials"),
                    "practice_name": provider_practice.get("name"),
                    "city": provider_practice.get("city"),
                    "state": provider_practice.get("state"),
                    "npi": provider.get("npi"),
                    "epic_status": canonical_status(provider.get("epic_status")),
                    "correct_fax": fmt(provider.get("correct_fax")),
                    "epic_fax": fmt(provider.get("epic_fax")),
                    "phone": fmt(provider_practice.get("phone")),
                    "source_url": provider.get("source_url", ""),
                    "notes": provider.get("notes", ""),
                }
            )
        export = pd.DataFrame(rows)
        st.metric("Providers", len(export))
        st.download_button(
            "Download directory",
            export.to_csv(index=False).encode(),
            "provider_fax_directory.csv",
            "text/csv",
            use_container_width=True,
        )
        st.download_button(
            "Download JSON backup",
            json.dumps(DATA, indent=2).encode(),
            "provider_fax_data.json",
            "application/json",
            use_container_width=True,
        )
    elif password:
        st.error("Incorrect password")
