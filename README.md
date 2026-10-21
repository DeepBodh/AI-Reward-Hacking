# AI-Reward-Hacking

Demo showing an AI agent exhibiting uncontrolled behaviour due to reward hacking.

This repo builds the **locked-door demo** for the "AI Mishaps, Investigated"
video: an AI agent does a harmless research task on a **fake website on our own
computer**, hits a locked page, and we film what it does — stop, use a side door
we left open, or make up an answer — then show four fixes. It's a staged demo of
real runs, not a measurement. See the plan for the full story and the on-camera
rules.

## Docs
- [`docs/plan.md`](docs/plan.md) — the plan (a copy of Confluence page 63078402).
- [`CLAUDE.md`](CLAUDE.md) — brief and rules for working in this repo.

## Layout
```
site/app.py          fake "National Health Statistics portal"; ENV_MODE=E1|E2
site/data.json       the fictional figures (2023, 2024, hidden 2025)
harness/agent.py     the agent loop + the fetch_url tool (locked to 127.0.0.1)
harness/adapters.py  one connector per AI family (chat / responses / anthropic)
harness/watcher.py   fix F4 (a second AI that says ALLOW/BLOCK)
harness/prompts.py   task, system prompt, the four fix texts, F2 allow-list
harness/smoke_test.py  the offline quick check (site, hints, log, URL guard)
harness/portal.py    loads site/app.py by path (the folder name 'site' clashes
                     with Python's builtin 'site' module)
score.py             scores one run from the website log + the final answer
test_score.py        ~40 hand-written answers the scorer must pass
run.py               runs a scene; --self-test proves the pipeline with no API
pick_runs.py         lists runs to pick the clearest one per scene
compare.py           optional section-16 counting helper (Wilson/Newcombe CIs)
models.yaml          model slugs, providers (pinned), precision
tests/               site, agent, adapters, pipeline and smoke tests
```

## Setup
```bash
pip install -r requirements.txt
```

## Check it works (no API key, no network)
```bash
python harness/smoke_test.py     # site, hints, log, and the URL guard
python run.py --self-test        # scripted model opens a side door, scored end to end
python -m pytest                 # full suite
```

## Run the fake site by hand
```bash
ENV_MODE=E1 PORT=8000 python site/app.py    # doors open
ENV_MODE=E2 PORT=8000 python site/app.py    # no doors, no hints
```

## Record a scene (needs the model's API key in the environment)
```bash
export OPENROUTER_API_KEY=...        # for the open models
export OPENAI_API_KEY=...            # for GPT-6 Luna and the F4 watcher
python run.py --model qwen38_27b --setup E1 --fix none --runs 20
python pick_runs.py runs/qwen38_27b_E1_none.jsonl --answer canary
```
`--fix` is one of `none F1 F2 F3 F4`. `--setup` is `E1` or `E2`.

Before recording, confirm the per-model checks that need a live model (the tool
really fires, thinking lands in its own field, a tiny output limit ends as
`truncated`) in addition to the offline smoke test above.
