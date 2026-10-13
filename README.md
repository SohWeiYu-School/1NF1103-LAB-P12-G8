# Source of Wealth Screening System

A Python CLI tool that helps bank compliance officers screen client Source of Wealth declarations faster and more consistently, using AI-generated career benchmarks to flag red flags without replacing human judgement.

---

## Problem

After the Monetary Authority of Singapore fined nine banks a combined S$27.45 million for failing to identify Source of Wealth red flags, banks over-corrected and account-opening procedures stretched to six weeks or more; this system helps banks screen faster without missing the red flags that triggered those fines.

---

## How It Works

```
User → io_manager → ai_manager → logic_manager → data_manager
```

The system makes **two AI calls per case**, then applies **rule-based scoring** — the AI never decides the outcome:

| Call | Input | Output |
|---|---|---|
| Call 1 — Benchmark | Career fields only (occupation, industry, age, career start year, country) | Expected wealth curve, income range, asset mix, sector typologies |
| Call 2 — Declaration reading | Free-text Source of Wealth declaration | Structured wealth-source rows |

`logic_manager` then compares the client's declared figures against the benchmark and applies policy thresholds to determine an outcome (`PASSED`, `MANUAL_REVIEW`, `RISKY`, or `SERIOUS_RISK`).

**The AI never sees declared net worth, AUM, asset composition, or PEP status.** This boundary is enforced in code.

---


## Prerequisites

- Git
- Docker Desktop

---

## Getting Started

### 1. Clone the repo (first time only)

```bash
git clone https://github.com/SohWeiYu-School/1NF1103-LAB-P12-Team-8.git
cd 1NF1103-LAB-P12-Team-8
```

### 2. Pull the latest changes (do this every time before you start working)

```bash
git checkout main
git pull
```

### 3. Set up your environment file (first time only)

The `.env` file will be handed to you privately. Place it in the root of the project folder. Do not share or commit it.

### 4. Build and run with Docker

Build the image once:
```bash
make build
```

Run the app:
```bash
make run
```

> You only need to rebuild if `requirements.txt` or `Dockerfile` changes. Otherwise just `make run` every time.

If you're actively coding and don't want to rebuild Docker every time you change a file, you can run locally instead:
```bash
make dev
```
Use `make run` (Docker) to do a final check before pushing your code.

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

Set `OPENAI_EXTRACT_MODEL` in `.env` to use a stronger model just for reading documents (it defaults to `OPENAI_MODEL`).

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
1NF1103-LAB-P12-Team-8/
├── app/
│   ├── prompts/          # AI prompt template text files
│   ├── schemas/          # JSON Schema files for validating AI responses
│   ├── __init__.py       # Makes app a Python package (do not delete)
│   ├── ai_manager.py     # AI calls and response caching
│   ├── data_manager.py   # Saves and loads JSON files
│   ├── io_manager.py     # All print() and input() calls live here
│   └── logic_manager.py  # Scoring and decision logic
├── config/
│   └── policy.json       # Risk thresholds and rules
├── data/
│   └── sample/           # Synthetic demo cases for testing
├── tests/                # All test files
├── .dockerignore
├── .env.example          # Template showing what variables are needed
├── .gitignore
├── CLAUDE.md             # Project coding conventions
├── Dockerfile            # Instructions for building the Docker image
├── main.py               # Entry point
├── Makefile              # Shortcuts: make run, make build, make dev, make test
└── requirements.txt      # Python dependencies with pinned versions
```

---

## Team and Ownership

| Member | Owns |
|---|---|
| TBD | `ai_manager` — benchmark call |
| TBD | `ai_manager` — declaration reading call |
| TBD | `logic_manager` — affordability and evidence checks |
| TBD | `logic_manager` — scoring and outcomes |
| TBD | `data_manager` and `io_manager` — persistence and CLI |
| TBD | DevOps — Docker, CI, tests |

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
3. Write a short description of what you changed
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