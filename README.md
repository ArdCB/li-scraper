# LinkedIn HTML → Excel (PyScript edition)

Convert a saved LinkedIn activity page (posts or comments) to an Excel
spreadsheet entirely **in the browser**.

## How it works
* **PyScript + Pyodide** run Python (pandas, BeautifulSoup, openpyxl) client‑side.
* Your file never leaves the browser.
* No servers, no API keys, no cost.

## Run locally
```bash
# clone and open index.html in any modern browser
python -m http.server 8000   # optional; nice for testing
