# Golden Case Test Harness

Golden tests verify that `logic_manager.assess_case` produces the expected
outcome for a complete, known set of inputs — without any network calls.

---

## File Format

Each golden case is a JSON file in this directory named `case_*.json`.
The file must contain:

```json
{
  "description": "Human-readable description of what this case tests",
  "case_input": { ... },
  "benchmark": { ... },
  "declaration": { ... },
  "policy": { ... },
  "expected_outcome": "PASSED"
}
```

| Field | Type | Description |
|---|---|---|
| `description` | string | What scenario this case exercises |
| `case_input` | object | Must conform to `case_input.schema.json` |
| `benchmark` | object or null | Must conform to `benchmark.schema.json`, or null to test AI failure |
| `declaration` | object or null | Must conform to `declaration.schema.json`, or null to test AI failure |
| `policy` | object | Policy config (may differ from `config/policy.json` to test edge cases) |
| `expected_outcome` | string | One of `PASSED`, `MANUAL_REVIEW`, `RISKY`, `SERIOUS_RISK` |

---

## Adding a New Golden Case

1. Create a new file: `tests/golden/case_<descriptive_name>.json`
2. Fill in all fields above
3. Run `pytest tests/golden/` to verify it passes
4. Commit the file on a feature branch

---

## Running Golden Tests

```bash
pytest tests/golden/
```

Or as part of the full suite:

```bash
pytest
```

---

## Design Notes

- Golden tests run entirely offline: no API key required, no network calls
- Each case exercises `logic_manager.assess_case` directly
- The benchmark and declaration values are supplied as pre-computed fixtures,
  exactly as if they had been loaded from `data/fixtures/ai_responses/`
- To test AI failure behaviour, set `"benchmark": null` or `"declaration": null`
- Add one golden case per significant logic scenario as the team implements
  the real assessment functions
