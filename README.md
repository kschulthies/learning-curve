# Learning-Curve Calculator

The repository includes two versions of the calculator:

- `app.py` is the Python/Streamlit app. It looks up a public ticker and pulls live revenue and cost facts from SEC EDGAR Company Facts. This is the recommended version for the assignment.
- `index.html` is the no-backend GitHub Pages version. It supports manual entry and CSV import.

## Publish the live Python app

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, choose **Create app**, connect the public repository, select `app.py` as the main file, then deploy.
3. Share the resulting public `.streamlit.app` URL. Test it in a private window before submitting.

Streamlit runs the Python backend, so the app can send SEC’s requested identifying User-Agent. The app asks for a contact email and sends it with SEC API requests. It does not ask for an SEC API key.

## Use the calculator

Enter a ticker and contact email, choose quarterly or annual frequency, and click **Fetch latest SEC data**. The app identifies available revenue and cost XBRL concepts; check that the chosen cost concept matches the company’s reported COGS or cost-of-revenue line. Quarterly mode uses standalone 10-Q periods (usually Q1–Q3); annual mode uses 10-K periods. It displays filing dates, accession numbers, and SEC filing-index links.

For volume, either enter direct unit counts or use revenue divided by an average selling price. ODP prices are prefilled from the assignment’s proxy series. For other tickers, enter a sourced price series. The app calculates cumulative volume from the chosen data-window start, unit cost, the power-curve coefficient `b`, learning rate `1 − 2^b`, and R². The chart uses logarithmic axes for both variables. Download the rows as CSV or use browser **Print → Save as PDF** to capture the output.

## Method and limitations

SEC Company Facts provides standardized XBRL facts; it does not reliably provide physical units sold or average selling price. Revenue divided by average price is therefore an estimate, not company-reported volume. The ODP series uses Advan SpendView consumer card transaction basket averages for 2020 and H1 2021, then extends the proxy using the BLS producer-price index for NAICS 453210 for 2022–2024. That consumer-ticket proxy does not match ODP’s consolidated B2B, distribution, and services revenue. It should be labeled as estimated order-equivalents.

Cumulative volume starts at the selected analysis window, not lifetime production. Cost per unit can also move because of product mix, freight, occupancy, inflation, acquisitions, or accounting changes. The regression is descriptive and cannot establish that learning caused a cost change. Report R² and treat weak fits as no visible experience curve in the window.

The HTML version can be published with GitHub Pages by selecting the repository’s `main` branch and root folder in **Settings → Pages**. It does not fetch EDGAR live; use the Streamlit version for ticker-based live filing retrieval.
