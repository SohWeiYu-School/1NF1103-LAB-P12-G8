# CLAUDE.md — AI Coding Assistant Conventions

This file tells AI coding assistants how to behave in this repo.
Follow these rules to keep the codebase consistent and assessable.

---

## 1. No `class` Keyword (Graded)

**The `class` keyword must not appear anywhere in any `.py` file.**

Use plain `dict` instead of dataclasses, TypedDict, Pydantic models, or Enums.
Use built-in exceptions instead of custom exception classes.
Use plain pytest functions instead of `unittest.TestCase`.

---

## 2. Print/Input Rule (Graded)

**All `print()` and `input()` calls must live in `app/io_manager.py` only.**

Other modules must return values — never print directly.
`main.py` passes return values to `io_manager` functions for display.

---

## 3. Managers Never Import Each Other

The four managers (`io_manager`, `ai_manager`, `logic_manager`, `data_manager`)
must never import from each other. All coordination happens in `main.py`.

```python
# main.py — correct
from app import io_manager, ai_manager, logic_manager, data_manager

# ai_manager.py — WRONG, do not do this
from app import data_manager
```

---

## 4. `logic_manager` is Pure

Every function in `app/logic_manager.py` must have no side effects:
no file access, no network calls, no printing.
Same inputs always produce the same output.

---

## 5. AI Allowlist

The benchmark AI call must never include the client's declared figures.
Only these fields may be sent:

```python
BENCHMARK_ALLOWED_FIELDS = ["occupation", "industry", "age", "career_start_year", "country"]
```

Do not add to this list without team agreement.

---

## 6. Tests

Use pytest plain functions only — no `unittest.TestCase`.

```python
def test_something():
    assert result == expected
```

---

## 7. Key Files

| Path | Purpose |
|---|---|
| `main.py` | Entry point — imports and calls all four managers |
| `app/io_manager.py` | All print/input |
| `app/ai_manager.py` | AI calls, caching, AI boundary enforcement |
| `app/logic_manager.py` | Pure scoring and decision functions |
| `app/data_manager.py` | JSON file persistence |
| `config/policy.json` | Risk thresholds — edit here, not in code |
| `tests/` | All test files |
