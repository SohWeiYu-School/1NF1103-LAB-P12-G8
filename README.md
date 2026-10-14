# Source of Wealth Screening System

A Python CLI tool that helps bank compliance officers screen client Source of Wealth declarations faster and more consistently, using AI-generated career benchmarks to flag red flags without replacing human judgement.

---

## Problem

After the Monetary Authority of Singapore fined nine banks a combined S$27.45 million for failing to identify Source of Wealth red flags, banks over-corrected and account-opening procedures stretched to six weeks or more; this system helps banks screen faster without missing the red flags that triggered those fines.

---

## How It Works

The crime scan, the forecast and the benchmark run as **one assessment** and give **one verdict**
(`PASSED`, `MANUAL_REVIEW`, `RISKY` or `SERIOUS_RISK`) with the reasons behind it. The AI only
gathers evidence; `logic_manager` makes every decision with fixed rules from `config/policy.json`.

All three features test the same set of "possible stories" for the client, the forecast's
**person types**: T1 is the honest story (his wealth came exactly as declared), T2+ are crime stories.

| Feature | The question it answers about each story |
|---|---|
| Crime scan | Is this crime story a known pattern for his industry (FATF / MAS reports), and did its warning signs fire in his declaration? |
| Forecast | Could the money add up under this story, given his pay, his assets and the economy? |
| Benchmark | Is he unusual next to peers in the same economy, and does the honest story explain it? |
| Documents | Do his own documents (bank statements, tax notices, company records, ...) agree with what he declared? |

```
 1     Documents            every supporting document is read: what they SHOW, with the file each fact
                            came from (the documents layer), kept apart from what he DECLARED
 2-5   Crime scan context   research reports -> verified -> sector typologies (each with a crime category)
 6-10  Forecast             declaration (read ONCE, shared with the crime scan) -> economy ->
                            pay + assets -> person types BUILT FROM the typologies -> behaviour + events;
                            pay is then set to what his tax notices / employment letter show
 11    Benchmark            peers in the same economy (the forecast's markets go to the benchmark AI)
 12    Test every story     warning signs per person type; each benchmark failure cross-checked
                            against the forecast ("explained" failures don't count); forecast checks;
                            documents vs declaration (undeclared companies, income, accounts, ...)
 13    ONE verdict          logic_manager.combined_verdict -> logic_manager.verdict_report
```

**Serious Risk needs two features to agree:** either the honest story can't explain his wealth, a
crime story can, AND that story's warning signs fired, his documents support it, or a benchmark
failure the forecast can't explain points the same way; or his documents contradict his declaration
in several ways AND another feature agrees. A PEP, or any part that couldn't run, is at least Manual Review.

The client record stays the standard form (what he declared); the documents fill any field the
declaration leaves empty, and the officer is only asked for what no document contains.

The command line (menu options 1, 2, 4) and the web app (Run Assessment) call the same function,
`main.run_combined_assessment`, and show the same report: the verdict, next step and next review
date; why; where the checks agree; the benchmark, forecast and crime findings; the documents to
request; what came back clean. The full evidence is one step away: the command line asks
"Show the full evidence?", the web page keeps it collapsed.

**The benchmark AI never sees the client's declared figures** (net worth, AUM, asset split): it
gets only the allowlisted career fields plus the economy (market % changes and country risk
listings) from the forecast.

---


## Prerequisites

- Git
- Docker Desktop

---

## Getting Started

### 1. Clone the repo (first time only)

```bash
git clone https://github.com/SohWeiYu-School/1NF1103-LAB-P12-G8.git
cd 1NF1103-LAB-P12-G8
```

### 2. Pull the latest changes (do this every time before you start working)

```bash
git checkout main
git pull
```

### 3. Set up your environment file (first time only)

The `.env` file will be handed to you privately. Place it in the root of the project folder. Do not share or commit it.

### 4. Build and run with Docker

Build the image:
```bash
docker build -t sow-screening .
```

Run the app:
```bash
docker run -it --env-file .env -v "$(pwd)/data:/app/data" sow-screening
```

The `-v` flag mounts your local `data/` folder into the container so saved records and the AI reply cache persist between runs. You only need to rebuild if `requirements.txt` or `Dockerfile` changes.

If you're actively coding and don't want to rebuild Docker every time you change a file, you can run locally instead:
```bash
python main.py
```

---

## Filling in a Client from Documents (web app)

Instead of typing every field, the officer can upload the client's documents and let the AI fill in the form.

```bash
python -m app.web_app        # then open http://127.0.0.1:5000
```

1. Open **+ New client** (or **Edit** on an existing client).
2. In **Fill in from documents**, choose one or more files — CV, payslips, employment letters, tax notices, property deeds, bank or brokerage statements, KYC forms. Accepted: PDF (including scans), Word (.docx), Excel (.xlsx), text/CSV, and images (.png, .jpg).
3. Click **Read documents & fill in**. The AI fills in the **empty** boxes only — anything already typed is never overwritten — and highlights every filled box in yellow.
4. Check each highlighted value, read the **Notes** box (e.g. amounts in other currencies that need converting to SGD, conflicts between documents), then **Save client**.

The uploaded files are kept as the client's supporting documents when you save.

The command line does the same through menu option **2. New Client from Documents** (point it at a folder of documents).

How it works — the web app and the command line share every step, so the same documents always give the same client:

1. `data_manager.read_document_for_extraction` reads each file's text (or keeps images and scanned PDFs as they are).
2. `ai_manager.extract_client_details` sends them to the AI with `app/prompts/document_extract.txt` and checks the reply against `app/schemas/document_extract.schema.json`.
3. `logic_manager.record_from_documents` turns the reply into a client record (the web app then shows it in the form, and `web_app.fill_blanks` only fills empty boxes).
4. `logic_manager.check_document_record` lists things worth a second look, shown with the AI's notes.

The form records what the client **declares** (from the KYC / declaration form). Anything the other documents show that the client did not declare — an offshore company, unexplained payments, a purchase funded differently — is not added to the form; it appears in the **Notes** as "Not declared:", "Conflict:" or "Check:".

Every AI call (document reading, research, typologies, forecast, benchmark) uses the ONE model set
as `OPENAI_MODEL` in `.env` (see `.env.example`). `FORECAST_WEB_SEARCH=on` lets the forecast check
market figures online. If the AI can't describe one asset sensibly even after the system repairs its
answer, that asset is held at its purchase price and the report says so; the rest still runs.

`tests/documents/marcus_lim/` holds a specimen set of documents, and `tests/test_document_parity.py` checks that both routes save the same client from them.

> **Privacy note:** this sends the full document contents — including declared figures — to the AI provider. This is separate from the benchmark call, whose allowlist (see `CLAUDE.md`) is unchanged. Only use synthetic documents.

---

## Running Tests

```bash
make test
```

---

## Project Structure

```
1NF1103-LAB-P12-G8/
├── app/
│   ├── prompts/          # AI prompt template text files
│   ├── schemas/          # JSON Schema files for validating AI responses
│   ├── __init__.py       # Makes app a Python package (do not delete)
│   ├── ai_manager.py     # AI calls and response caching
│   ├── data_manager.py   # Saves and loads JSON files
│   ├── io_manager.py     # All print() and input() calls live here
│   ├── logic_manager.py  # Scoring and decision logic
│   ├── utilities.py      # Shared config, cache helpers and AI client
│   └── web_app.py        # Flask web interface
├── config/
│   └── policy.json       # Risk thresholds and rules
├── data/
│   └── sample/           # Synthetic demo cases for testing
├── logs/
│   └── app.log           # All application logs (warnings, errors, AI call info)
├── tests/                # All test files
├── .dockerignore
├── .env.example          # Template showing what variables are needed
├── .gitignore
├── CLAUDE.md             # Project coding conventions
├── Dockerfile            # Instructions for building the Docker image
├── main.py               # Entry point
└── requirements.txt      # Python dependencies with pinned versions
```

## Logging

Every warning, error and AI call is written to `logs/app.log`. Nothing is printed to the console or terminal by the application itself — all diagnostic output goes to the log file for clean, consistent history. Check `logs/app.log` first whenever something behaves unexpectedly.

---

## Git Workflow (Step by Step)

This is the workflow every teammate should follow. **Never commit directly to `main`.**

### Step 1 — Pull the latest main before starting

```bash
git checkout main
git pull
```

### Step 2 — Create your own branch

Name it after what you're working on:

```bash
git checkout -b feature/your-feature-name
```

Examples:
- `feature/io-menu`
- `feature/ai-benchmark-call`
- `feature/logic-scoring`
- `fix/data-save-bug`

### Step 3 — Make your changes and commit

```bash
git add .
git commit -m "feat: describe what you did"
```

Keep commits small and descriptive. Commit often.

### Step 4 — Push your branch to GitHub

```bash
git push -u origin feature/your-feature-name
```

### Step 5 — Open a Pull Request on GitHub

1. Go to the repo on GitHub
2. You'll see a banner saying your branch was recently pushed — click **"Compare & pull request"**
3. Write a short description of what you changedu 
4. Assign a teammate to review it
5. Click **"Create pull request"**

### Step 6 — Get it reviewed and merged

- A teammate reviews your code and approves it
- Once approved, click **"Merge pull request"** (use **"Create a merge commit"** — not squash)
- After merging, click **"Delete branch"** on GitHub to keep things tidy

### Step 7 — Pull main again before your next task

```bash
git checkout main
git pull
```

Then repeat from Step 2 for your next task.

---

## Data and Privacy

All data used in this project is **entirely synthetic**. No real client information is used anywhere in the codebase or sample data. The system is a learning prototype for a university course.