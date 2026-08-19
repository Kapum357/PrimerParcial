# Backend — Emergency Control

Python API that exposes `POST /api/solve`.

The API uses uniform-cost search (UCS) to produce a minimum-cost plan. The
search uses a canonical state, battery dominance, and restricted `DROP`
successors so the real scenario remains solvable without changing its rules.
Do not «fix» `scenario.json` (capacity, battery, rooms) to make UCS finish:
formulate `Applicable` instead. See `project/design.md`.

## Run

```bash
cd project/backend
pip install -r requirements.txt
py -m uvicorn src.main:app --reload --port 8000
```

## Tests

```bash
cd project/backend
py -m pytest tests/main_test.py
```
