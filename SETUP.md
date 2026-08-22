# Getting This Running

Drop the `backend/` folder into your `9gear_Pulse` repo (or merge it with
your existing `backend/` if you already made one).

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Fill in .env with a REAL Postgres connection you can safely test against
# — ideally a scratch DB, not production, and a read-only user if possible.
# Add your Anthropic API key from https://console.anthropic.com
```

**Step 1 — confirm schema introspection works on its own:**

```bash
python introspect.py
```

You should see a JSON dump of your tables, columns, row counts, and a few
sample rows. This is the exact payload that will get handed to the AI —
worth eyeballing it once to make sure nothing more sensitive than you
intend is in there (drop `sample_rows` to `0` in the call if you'd rather
send shape only, no content).

**Step 2 — generate a pipeline from a plain-English goal:**

```bash
python generate_pipeline.py
```

It'll re-run introspection, ask you for a goal in plain English, call
Claude with the schema summary + your goal, and write the generated script
to `generated_pipeline.py`.

**What to evaluate once this works:** don't judge it on whether it runs —
judge it on whether the generated code is *correct* for 5–10 different
goals against your real schema shapes. That accuracy number is what
Phase 2 (the Docker sandbox + self-healing loop) gets built on top of.

**Step 3 — start the actual API server for the Next.js frontend:**

Running `python main.py` or `python app.py` on their own will NOT start a
web server — neither file calls `uvicorn.run()` directly (only the
Dockerfile did, via its CMD). From the `backend/` directory, with your
venv active:

```bash
uvicorn app:app --reload --port 8000
```

Run this from the same directory as your `.env` and `9gear_pulse.db` —
the SQLite path in `.env` is relative (`sqlite:///./9gear_pulse.db`), so
starting uvicorn from anywhere else will silently create/read a *different*,
empty database file instead of the one `init_db.py` seeded.

Leave this running in its own terminal, then start the frontend
(`npm run dev` in `frontend/`) in another. `app.py` is the only file that
should ever be served — `main.py` is just the pipeline orchestrator +
CLI, imported by `app.py`, not a second server.
