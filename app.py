"""Learning curve calculator backed by SEC EDGAR Company Facts."""

from __future__ import annotations

import math
import re
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_DATA = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet", "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax",
]
COGS_TAGS = [
    "CostOfRevenue", "CostOfGoodsAndServicesSold",
    "CostOfGoodsAndServicesSoldExcludingDepreciationDepletionAndAmortization",
    "CostOfGoodsSold", "CostOfGoodsSoldExcludingDepreciationDepletionAndAmortization",
]
ODP_TICKETS = {2020: 64.12, 2021: 63.65, 2022: 68.03, 2023: 71.63, 2024: 71.36}

st.set_page_config(page_title="Learning-Curve Calculator", page_icon="📉", layout="wide")
st.title("Learning-Curve Calculator")
st.caption("Estimate a company’s cost experience curve from live SEC filings and a disclosed or estimated volume measure.")


def sec_get(url: str, email: str):
    if not email or "@" not in email:
        raise ValueError("Enter a contact email for the SEC User-Agent before fetching data.")
    response = requests.get(url, headers={"User-Agent": f"LearningCurveCalculator/1.0 {email}", "Accept-Encoding": "gzip, deflate"}, timeout=30)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=3600, show_spinner=False)
def company_tickers(email: str):
    return sec_get(SEC_TICKERS, email)


@st.cache_data(ttl=3600, show_spinner=False)
def company_facts(cik: int, email: str):
    return sec_get(SEC_DATA.format(cik=cik), email)


@st.cache_data(ttl=3600, show_spinner=False)
def company_submissions(cik: int, email: str):
    return sec_get(SEC_SUBMISSIONS.format(cik=cik), email)


def identify_company(ticker: str, email: str):
    rows = company_tickers(email).values()
    for item in rows:
        if item["ticker"].upper() == ticker.upper():
            return int(item["cik_str"]), item["title"]
    # ODP was delisted after the assignment's historical comparison window and may
    # no longer appear in the SEC's current ticker map, though its filings remain public.
    if ticker.upper() == "ODP":
        return 800240, "The ODP Corporation"
    raise ValueError(f"Ticker {ticker.upper()} was not found in SEC’s current ticker list.")


def fact_concepts(facts: dict, tag_list: list[str]):
    concepts = facts.get("facts", {}).get("us-gaap", {})
    found = []
    for tag, concept in concepts.items():
        if tag in tag_list:
            found.append((tag, concept.get("label", tag)))
    # Include likely related standard tags for companies with different taxonomy choices.
    if not found:
        terms = ("revenue", "sales") if tag_list is REVENUE_TAGS else ("cost of revenue", "cost of goods")
        for tag, concept in concepts.items():
            label = concept.get("label", "").lower()
            if any(term in label for term in terms) and tag not in ("RevenueLossFromLitigationSettlement",):
                found.append((tag, concept.get("label", tag)))
    return sorted(set(found), key=lambda pair: (pair[0] not in tag_list, pair[0]))


def filing_lookup(submissions: dict, cik: int):
    recent = submissions.get("filings", {}).get("recent", {})
    mapping = {}
    for i, accession in enumerate(recent.get("accessionNumber", [])):
        mapping[accession] = {
            "form": recent.get("form", [""])[i],
            "filed": recent.get("filingDate", [""])[i],
            "report": recent.get("reportDate", [""])[i],
        }
    for old in submissions.get("filings", {}).get("files", []):
        # Company Facts almost always points to recent filings; older metadata can be loaded on demand.
        if old.get("name"):
            mapping["__older__"] = old["name"]
    return mapping


def duration_rows(facts: dict, tag: str):
    concept = facts.get("facts", {}).get("us-gaap", {}).get(tag, {})
    rows = []
    for unit, items in concept.get("units", {}).items():
        if unit not in ("USD", "USD/shares"):
            continue
        for item in items:
            if "start" not in item or item.get("form") not in ("10-K", "10-Q", "20-F", "40-F"):
                continue
            start, end = pd.Timestamp(item["start"]), pd.Timestamp(item["end"])
            days = (end - start).days + 1
            if 65 <= days <= 115 or 330 <= days <= 400:
                rows.append({"start": start, "end": end, "days": days, "value": float(item["val"]),
                             "form": item.get("form", ""), "filed": item.get("filed", ""),
                             "accn": item.get("accn", "")})
    if not rows:
        return pd.DataFrame(columns=["start", "end", "days", "value", "form", "filed", "accn"])
    return pd.DataFrame(rows)


def normalize_periods(revenue: pd.DataFrame, costs: pd.DataFrame, frequency: str):
    # Choose the latest filed observation for a given fiscal duration. This avoids duplicates
    # caused by amendments and comparative figures repeated in later filings.
    def clean(df):
        df = df.sort_values(["filed", "end"], ascending=[False, False]).drop_duplicates(["start", "end"], keep="first")
        df = df.copy()
        df["key"] = df["start"].dt.strftime("%Y-%m-%d") + "/" + df["end"].dt.strftime("%Y-%m-%d")
        return df
    rev, cgs = clean(revenue), clean(costs)
    joined = rev.merge(cgs, on="key", suffixes=("_rev", "_cogs"))
    if joined.empty:
        return pd.DataFrame()
    joined["start"] = joined["start_rev"]
    joined["end"] = joined["end_rev"]
    joined["revenue"] = joined["value_rev"]
    joined["cogs"] = joined["value_cogs"]
    joined["form"] = joined["form_rev"]
    joined["filed"] = joined["filed_rev"]
    joined["accn"] = joined["accn_rev"]
    if frequency == "Quarterly":
        joined = joined[(joined["days_rev"] <= 115) & (joined["form_rev"] == "10-Q")]
    else:
        joined = joined[(joined["days_rev"] >= 330) & joined["form_rev"].isin(["10-K", "20-F", "40-F"])]
    joined = joined.sort_values("end").drop_duplicates("end", keep="last")
    joined["period"] = joined["end"].dt.strftime("%Y-%m-%d")
    joined["source_form"] = joined["form"]
    joined["filing_date"] = joined["filed"]
    joined["accession"] = joined["accn"]
    joined["cost_source_form"] = joined["form_cogs"]
    joined["cost_filing_date"] = joined["filed_cogs"]
    joined["cost_accession"] = joined["accn_cogs"]
    return joined[["period", "start", "end", "revenue", "cogs", "source_form", "filing_date", "accession", "cost_source_form", "cost_filing_date", "cost_accession"]].reset_index(drop=True)


def fit_curve(data: pd.DataFrame):
    x = np.log(data["cumulative_units"].to_numpy(dtype=float))
    y = np.log(data["unit_cost"].to_numpy(dtype=float))
    slope, intercept = np.polyfit(x, y, 1)
    predicted = intercept + slope * x
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")
    learning_rate = 1 - 2 ** slope
    return slope, intercept, r2, learning_rate


with st.sidebar:
    st.header("Company and filings")
    ticker = st.text_input("Public company ticker", value="ODP", max_chars=12).strip().upper()
    email = st.text_input("Contact email for SEC requests", help="SEC asks automated clients to identify themselves with a User-Agent that includes contact information.")
    frequency = st.radio("Filing frequency", ["Quarterly", "Annual"], horizontal=True, index=0)
    st.caption("Quarterly mode uses standalone 10-Q periods (Q1–Q3); annual mode uses 10-K periods. EDGAR Company Facts does not always provide a standalone Q4 value.")
    fetch = st.button("Fetch latest SEC data", type="primary", use_container_width=True)

if "company_payload" not in st.session_state:
    st.session_state.company_payload = None
if fetch:
    try:
        cik, company_name = identify_company(ticker, email)
        facts = company_facts(cik, email)
        submissions = company_submissions(cik, email)
        st.session_state.company_payload = {"ticker": ticker, "cik": cik, "name": company_name, "facts": facts, "submissions": submissions}
        st.success(f"Loaded live SEC company facts for {company_name} (CIK {cik:010d}).")
    except Exception as exc:
        st.error(f"SEC lookup failed: {exc}")

payload = st.session_state.company_payload
if not payload:
    st.info("Enter your contact email in the sidebar, then fetch a ticker. The default ODP sample will load from SEC when requested.")
    st.markdown("**How this works:** SEC Company Facts supplies reported revenue and cost facts. Since filings often omit physical unit counts, the calculator asks for an average selling price and labels the resulting volume as an estimate.")
    st.stop()
if payload["ticker"] != ticker:
    st.info("Ticker changed. Click **Fetch latest SEC data** to load the selected company.")
    st.stop()

facts, cik, company_name = payload["facts"], payload["cik"], payload["name"]
st.subheader(f"{company_name} ({payload['ticker']})")
rev_options = fact_concepts(facts, REVENUE_TAGS)
cogs_options = fact_concepts(facts, COGS_TAGS)
if not rev_options or not cogs_options:
    st.error("Could not find compatible standard XBRL revenue and cost facts for this company. Try another ticker or use the CSV workflow in the static app.")
    st.stop()
col1, col2 = st.columns(2)
rev_tag = col1.selectbox("Revenue XBRL concept", rev_options, format_func=lambda x: f"{x[1]} · {x[0]}", index=0)[0]
cogs_tag = col2.selectbox("Cost of revenue / COGS concept", cogs_options, format_func=lambda x: f"{x[1]} · {x[0]}", index=0)[0]
raw = normalize_periods(duration_rows(facts, rev_tag), duration_rows(facts, cogs_tag), frequency)
if raw.empty:
    st.error("No matching periods found for the selected concepts. Try another XBRL concept or switch annual/quarterly frequency.")
    st.stop()

raw = raw.tail(40 if frequency == "Quarterly" else 12).copy()
raw["revenue_m"] = raw["revenue"] / 1_000_000
raw["cogs_m"] = raw["cogs"] / 1_000_000
raw["average_price"] = np.nan
if payload["ticker"] == "ODP":
    for idx, row in raw.iterrows():
        year = int(row["end"].year)
        if year in ODP_TICKETS:
            raw.loc[idx, "average_price"] = ODP_TICKETS[year]

st.markdown("### Volume measure")
volume_mode = st.radio("How will volume be measured?", ["Revenue ÷ average price proxy", "Enter direct units sold"], horizontal=True)
if volume_mode == "Revenue ÷ average price proxy":
    st.caption("Enter average selling price per unit/order for each period. ODP sample years are prefilled from the proxy series; other tickers need a sourced price series.")
    st.caption("ODP price source: Advan SpendView consumer card transactions (2020–H1 2021); 2022–2024 extended using the BLS producer-price index for NAICS 453210. This is an estimated consumer ticket, not ODP’s disclosed average selling price.")
    raw["average_price"] = raw["average_price"].fillna(0.0)
    edited = st.data_editor(raw[["period", "revenue_m", "cogs_m", "average_price", "source_form", "filing_date", "accession", "cost_source_form", "cost_filing_date", "cost_accession"]],
                            hide_index=True, use_container_width=True,
                            column_config={"period": st.column_config.TextColumn("Fiscal period end", disabled=True),
                                           "revenue_m": st.column_config.NumberColumn("Revenue ($m)", format="%.2f", disabled=True),
                                           "cogs_m": st.column_config.NumberColumn("COGS / cost ($m)", format="%.2f", disabled=True),
                                           "average_price": st.column_config.NumberColumn("Average price ($/unit)", min_value=0.0, format="%.2f"),
                                           "source_form": st.column_config.TextColumn("Filing", disabled=True),
                                           "filing_date": st.column_config.TextColumn("Filed", disabled=True),
                                           "accession": st.column_config.TextColumn("Revenue accession", disabled=True),
                                           "cost_source_form": st.column_config.TextColumn("Cost filing", disabled=True),
                                           "cost_filing_date": st.column_config.TextColumn("Cost filed", disabled=True),
                                           "cost_accession": st.column_config.TextColumn("Cost accession", disabled=True)})
    raw["average_price"] = edited["average_price"].astype(float).to_numpy()
    raw["estimated_units"] = raw["revenue"] / raw["average_price"].replace(0, np.nan)
    units_label = "estimated order-equivalents"
    proxy_note = "Volume is estimated as revenue divided by the user-entered average price. The ODP defaults are a consumer-ticket proxy and do not represent company-reported order counts."
else:
    edited = st.data_editor(raw[["period", "revenue_m", "cogs_m", "source_form", "filing_date", "accession", "cost_source_form", "cost_filing_date", "cost_accession"]].assign(direct_units=0.0),
                            hide_index=True, use_container_width=True,
                            column_config={"revenue_m": st.column_config.NumberColumn("Revenue ($m)", disabled=True), "cogs_m": st.column_config.NumberColumn("COGS / cost ($m)", disabled=True), "direct_units": st.column_config.NumberColumn("Units sold", min_value=0.0)})
    raw["estimated_units"] = edited["direct_units"].astype(float).to_numpy()
    units_label = "reported units"
    proxy_note = "Units were entered directly by the analyst; verify that the unit definition and period match company revenue and cost reporting."

price_source = st.text_input("Average-price or direct-unit source / method", value=("Advan SpendView (2020–H1 2021) and BLS NAICS 453210 price index (2022–2024); annual proxy mapped to fiscal periods" if payload["ticker"] == "ODP" and volume_mode.startswith("Revenue") else "Enter source name, report, page/table, and estimation method"))
window_start = st.date_input("Cumulative production window starts", value=raw["end"].min().date())
raw = raw[raw["end"].dt.date >= window_start].copy()
raw = raw.dropna(subset=["estimated_units"])
raw = raw[(raw["estimated_units"] > 0) & (raw["cogs"] > 0)].sort_values("end")
if len(raw) < 3:
    st.warning("At least three aligned, positive periods are required to fit a curve. Fill in volume data or expand the window.")
    st.stop()
raw["cumulative_units"] = raw["estimated_units"].cumsum()
raw["unit_cost"] = raw["cogs"] / raw["estimated_units"]
slope, intercept, r2, learning_rate = fit_curve(raw)

kpi1, kpi2, kpi3, kpi4 = st.columns(4)
kpi1.metric("Learning rate per doubling", f"{learning_rate*100:.1f}%", help="Calculated as 1 − 2^b. A positive value means cost declines as cumulative volume doubles.")
kpi2.metric("Coefficient b", f"{slope:.3f}")
kpi3.metric("R²", f"{r2:.3f}")
kpi4.metric("Observations", str(len(raw)))
if r2 >= 0.8:
    fit_read = "R² is above the assignment’s approximate 0.8 reference, consistent with a visible curve in this window."
elif r2 < 0.5:
    fit_read = "R² is below 0.5; the honest conclusion is that no experience curve is visible in this window."
else:
    fit_read = "R² is moderate and below the approximate 0.8 reference; treat the estimated curve as suggestive at most."
direction = "cost falls" if slope < 0 else "cost rises" if slope > 0 else "cost is flat"
strategy = (f"The fitted b is {slope:.3f}, which implies cost per {units_label[:-1]} changes by {learning_rate*100:.1f}% for each doubling of cumulative volume; {direction} as cumulative volume grows. "
            f"{fit_read} This is an association across the selected reporting window, not proof that production learning caused the cost movement or that a scale advantage is durable.")

st.markdown("### Experience curve")
fig = go.Figure()
fig.add_trace(go.Scatter(x=raw["cumulative_units"], y=raw["unit_cost"], mode="markers+text", text=raw["period"], textposition="top center", name="Observed periods", marker=dict(size=10, color="#b65b25"), hovertemplate="%{text}<br>Cumulative volume: %{x:,.3g}<br>Unit cost: $%{y:,.3g}<extra></extra>"))
xfit = np.geomspace(raw["cumulative_units"].min(), raw["cumulative_units"].max(), 100)
yfit = np.exp(intercept) * xfit ** slope
fig.add_trace(go.Scatter(x=xfit, y=yfit, mode="lines", name=f"Fit: cost = {math.exp(intercept):.4g} × N^{slope:.3f}", line=dict(color="#176b4a", width=3)))
fig.update_xaxes(type="log", title=f"Cumulative {units_label} (window starts {window_start})", showgrid=True)
fig.update_yaxes(type="log", title=f"Unit cost ($ per {units_label[:-1]})", showgrid=True)
fig.update_layout(height=520, template="plotly_white", legend=dict(orientation="h", y=1.08), margin=dict(l=20, r=20, t=45, b=20))
st.plotly_chart(fig, use_container_width=True)
st.markdown(f"**Fitted equation:** unit cost = {math.exp(intercept):.6g} × cumulative volume^{slope:.4f} · **R² = {r2:.4f}**")
st.markdown("### Interpretation")
st.write(strategy)
st.markdown("### Method and limitations")
st.write(f"{proxy_note} Cumulative volume begins at **{window_start}**, the start of the selected data window, rather than lifetime production. {price_source}")
st.write("Reported cost may include product mix, freight, occupancy, inflation, acquisitions, and accounting changes. SEC XBRL concepts can differ across issuers; confirm the selected concepts against the filing statements before relying on the result.")

st.markdown("### SEC data sources")
st.write(f"Revenue concept: **{rev_tag}** ({dict(rev_options).get(rev_tag, rev_tag)}). Cost concept: **{cogs_tag}** ({dict(cogs_options).get(cogs_tag, cogs_tag)}). Source: SEC EDGAR Company Facts for CIK {cik:010d}.")
seen_filings = set()
for _, row in raw.iterrows():
    for prefix, label in (("", "Revenue"), ("cost_", "Cost")):
        accession = str(row["accession" if not prefix else "cost_accession"])
        if accession and accession != "nan" and accession not in seen_filings:
            seen_filings.add(accession)
            filing_form = row["source_form" if not prefix else "cost_source_form"]
            filed = row["filing_date" if not prefix else "cost_filing_date"]
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"
            st.markdown(f"- {label}: {filing_form} filed {filed} (period ending {row['period']}), accession `{accession}` · [SEC filing index]({url})")

download = raw[["period", "revenue_m", "cogs_m", "average_price", "estimated_units", "cumulative_units", "unit_cost", "source_form", "filing_date", "accession", "cost_source_form", "cost_filing_date", "cost_accession"]].copy()
st.download_button("Download output data (CSV)", download.to_csv(index=False).encode(), file_name=f"{payload['ticker'].lower()}-experience-curve.csv", mime="text/csv")
st.caption("To export a PDF for the assignment, use your browser’s Print → Save as PDF. Include this live output and the SEC source links.")
