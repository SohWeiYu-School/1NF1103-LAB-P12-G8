# AI cross-check: is what the AI said true?

One folder per sample case. Each file holds REAL answers collected by the team.
`tests/test_ai_crosscheck.py` compares them with the AI's real replies, read straight
from `data/raw/<client_ref>/` (where the system saves them).

| File | Fill it from | The AI must |
|---|---|---|
| `market_history.json` | Official statistics (SingStat, URA, World Bank, S&P...) - link in `source` | be within `allowed_difference` for at least `pass_mark` of the rows |
| `pay_ranges.json` | Published salary data (MOM, salary guides) - link in `source` | put typical pay (base + bonus + equity) inside the range |
| `declaration.json` | Reading `sow_declaration` in the case file - words copied into `from_text` | find every source listed (`source_type`, and `amount` if you give it) |
| `review.json` | Reading the officer notes - words copied into `from_text` | read every payment listed (`signal_code`, plus `amount`, `times_received`, `currency` if you give them) |

The tests keep these files legitimate:
- a figure without an `http(s)://` source link fails;
- a `from_text` that isn't word for word in the client's text fails;
- empty rows are ignored, so you can fill the files in gradually.

Forecasts (2027-2030) can't be cross-checked: no true answer exists until those years happen.

Run: `python -m pytest -q tests/test_ai_crosscheck.py -rs`
