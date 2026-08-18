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
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8000
```

Or from `backend/src`:

```bash
cd project/backend/src
uvicorn main:app --reload --port 8000
```

## Tests

```bash
cd project/backend
python tests/test_demo_plan.py
```
