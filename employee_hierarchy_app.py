"""
Employee Designation & Reporting Hierarchy Tool
------------------------------------------------
Run with:
    streamlit run employee_hierarchy_app.py

Expects a CSV/Excel file with (at least) these columns:
    Name, Email, Role, Reporting Manager, Employee ID

What it does:
1. Lets you upload the raw export.
2. Filters rows to Role in [Bda, Am, Bdm, Avp, Senior bdm, Vp] AND
   Employee ID containing "GKL" or "GKH".
3. For every surviving row, walks UP the reporting chain (using the
   FULL uploaded file, so no manager is lost to the filter) and fills
   in 5 new columns: AM, BDM, AVP, Senior BDM, VP — the name of the
   manager holding that title above this employee. Blank if none
   found (e.g. "No AM", top of chain, or manager not in the file).
4. Lets you search by Name / Email / Employee ID. If the search
   matches more than one row (duplicate IDs/names in the export),
   each match is shown in its own block separated by st.divider().
5. Lets you download the fully processed CSV.
"""

import re
import io
import pandas as pd
import streamlit as st

# ----------------------------- Config ------------------------------

REQUIRED_COLUMNS = ["Name", "Email", "Role", "Reporting Manager", "Employee ID"]

TARGET_ROLES = ["Bda", "Am", "Bdm", "Avp", "Senior bdm", "Vp"]

# Role value (as it appears in the "Role" column) -> output column name
ROLE_TO_COL = {
    "Am": "AM",
    "Bdm": "BDM",
    "Avp": "AVP",
    "Senior bdm": "Senior BDM",
    "Vp": "VP",
}
CHAIN_COLUMNS = list(ROLE_TO_COL.values())  # ["AM", "BDM", "AVP", "Senior BDM", "VP"]

MAX_HOPS = 15  # safety cap against circular reporting chains

# --------------------------- Core helpers ---------------------------


def _norm(value):
    """Collapse whitespace / strip. Returns None for NaN/empty."""
    if pd.isna(value):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text if text else None


def _norm_key(value):
    n = _norm(value)
    return n.upper() if n else None


def _is_placeholder(name):
    """Values like 'No AM', 'No Sr BDM', 'No VP', 'No AVP (Edtech)' terminate
    the chain — they are not real people."""
    if name is None:
        return True
    return bool(re.match(r"^no\s", name, re.IGNORECASE))


def build_name_lookup(df: pd.DataFrame):
    """normalized-name -> (role, reporting_manager) using the FIRST
    occurrence of each name. Also returns the set of names that appear
    more than once (ambiguous — chain may follow the wrong person)."""
    lookup = {}
    duplicate_names = set()
    for _, row in df.iterrows():
        key = _norm_key(row.get("Name"))
        if key is None:
            continue
        if key in lookup:
            duplicate_names.add(key)
        else:
            lookup[key] = (_norm(row.get("Role")), _norm(row.get("Reporting Manager")))
    return lookup, duplicate_names


def trace_chain(start_manager_name, name_lookup):
    """Walk up the reporting chain starting at `start_manager_name`,
    filling AM/BDM/AVP/Senior BDM/VP as those roles are encountered."""
    result = {col: "" for col in CHAIN_COLUMNS}
    current = _norm(start_manager_name)
    visited = set()
    hops = 0

    while current and not _is_placeholder(current) and hops < MAX_HOPS:
        key = current.upper()
        if key in visited:
            break  # circular reference guard
        visited.add(key)

        entry = name_lookup.get(key)
        if entry is None:
            break  # manager not found in the file — stop here

        role, next_manager = entry
        col = ROLE_TO_COL.get(role)
        if col and not result[col]:
            result[col] = current

        current = next_manager
        hops += 1

    return result


def process_file(raw_df: pd.DataFrame):
    """Full pipeline: validate -> build lookup on FULL data -> filter
    -> trace chain for each filtered row. Returns (processed_df, stats)."""
    df = raw_df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            "The uploaded file is missing required column(s): " + ", ".join(missing)
        )

    # Build the manager lookup on the FULL, unfiltered file so a manager
    # who doesn't personally satisfy the role/ID filter is still found.
    name_lookup, duplicate_names = build_name_lookup(df)

    role_norm = df["Role"].apply(_norm)
    mask_role = role_norm.isin(TARGET_ROLES)
    mask_id = df["Employee ID"].fillna("").str.upper().str.contains("GKL|GKH", regex=True)

    filtered = df[mask_role & mask_id].copy()

    chains = filtered["Reporting Manager"].apply(lambda x: trace_chain(x, name_lookup))
    chain_df = pd.DataFrame(list(chains), index=filtered.index)

    processed = pd.concat([filtered, chain_df], axis=1).reset_index(drop=True)

    stats = {
        "total_rows": len(df),
        "filtered_rows": len(processed),
        "duplicate_manager_names": len(duplicate_names),
    }
    return processed, stats


@st.cache_data(show_spinner=False)
def read_uploaded_file(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    buffer = io.BytesIO(file_bytes)
    if file_name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(buffer, dtype=str)
    return pd.read_csv(buffer, dtype=str)


@st.cache_data(show_spinner=False)
def run_pipeline(file_bytes: bytes, file_name: str):
    raw_df = read_uploaded_file(file_bytes, file_name)
    return process_file(raw_df)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


# ------------------------------ UI -----------------------------------

st.set_page_config(page_title="Employee Designation & Hierarchy Tool", layout="wide")
st.title("Employee Designation & Reporting Hierarchy Tool")

st.write(
    "Upload the raw export. The app filters to **Bda / Am / Bdm / Avp / Senior bdm / Vp** "
    "roles with an Employee ID containing **GKL** or **GKH**, and adds AM / BDM / AVP / "
    "Senior BDM / VP columns by walking up each employee's reporting chain."
)

uploaded_file = st.file_uploader("Upload CSV or Excel file", type=["csv", "xlsx", "xls"])

if uploaded_file is None:
    st.info("Upload a file to get started.")
    st.stop()

file_bytes = uploaded_file.getvalue()

try:
    processed_df, stats = run_pipeline(file_bytes, uploaded_file.name)
except ValueError as exc:
    st.error(str(exc))
    st.stop()
except Exception as exc:  # pragma: no cover
    st.error(f"Could not process the file: {exc}")
    st.stop()

# ------------------------- Summary + preview --------------------------

col1, col2, col3 = st.columns(3)
col1.metric("Rows in original file", stats["total_rows"])
col2.metric("Rows after filter", stats["filtered_rows"])
col3.metric("Duplicate manager names in file", stats["duplicate_manager_names"])

if stats["duplicate_manager_names"] > 0:
    st.caption(
        "⚠️ Some names appear more than once in the uploaded file. "
        "The chain lookup used the first occurrence of each name, so a "
        "few AM/BDM/AVP/Senior BDM/VP values could point to the wrong "
        "person of the same name — worth a spot check."
    )

st.subheader("Processed data preview")


# ------------------------------ Search ---------------------------------

st.subheader("Search an employee")
search_term = st.text_input("Search by Name, Email, or Employee ID")

if search_term:
    term = search_term.strip().lower()
    search_cols = ["Name", "Email", "Employee ID"]
    mask = pd.Series(False, index=processed_df.index)
    for c in search_cols:
        mask = mask | processed_df[c].fillna("").str.lower().str.contains(term, regex=False)

    matches = processed_df[mask]

    if matches.empty:
        st.warning("No matching employee found.")
    else:
        st.write(f"Found **{len(matches)}** matching record(s).")
        for i, (_, row) in enumerate(matches.iterrows()):
            st.markdown(f"**{row['Name']}**  ·  {row['Email']}  ·  `{row['Employee ID']}`  ·  Role: {st.info(row['Role'].upper())}")

            manager_row = pd.DataFrame(
                [{
                    "Reporting Manager": row["Reporting Manager"] if _norm(row["Reporting Manager"]) else "—",
                    **{col: (row[col] if row[col] else "—") for col in CHAIN_COLUMNS},
                }]
            )
            st.dataframe(manager_row, use_container_width=True, hide_index=True)

            if i < len(matches) - 1:
                st.divider()

st.divider()

#st.dataframe(processed_df, width=True, height=350)

#st.divider()
# ------------------------------ Download --------------------------------

st.subheader("Download")
st.download_button(
    label="Download processed CSV",
    data=to_csv_bytes(processed_df),
    file_name="processed_employees.csv",
    mime="text/csv",
)
