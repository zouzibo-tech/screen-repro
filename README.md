# screen-repro v3.6

> 程序主导，AI 辅助，可复现为本。  
> A reproducible, gate-driven full-text screening toolkit for systematic reviews and meta-analyses.

`screen-repro` is a Python-first screening system for systematic reviews and meta-analyses. It uses AI only for bounded PICOS full-text judgment, while Python owns orchestration, data validation, PDF authenticity checks, state management, audit reports, and reproducibility manifests.

The core idea is simple:

```text
Python is the orchestrator. AI is only a callable judgment function.
```

---

## Why this project exists

In AI-assisted evidence synthesis, the most dangerous failure is not a crash. It is a workflow that **reproducibly preserves the wrong evidence**.

`screen-repro` was built after several real screening incidents:

- a bibliographic record was linked to the wrong PDF;
- a PDF path existed, but the file was not the correct full text;
- a final lock pool preserved earlier mistakes;
- AI judgments looked plausible but were not grounded in auditable artifacts;
- progress monitors mixed checkpoint and log files from different runs.

Therefore, `screen-repro` treats reproducibility as more than “same code, same output”. It requires that every downstream judgment can be traced to the correct PDF, extracted text, rules, prompt, model configuration, script version, checkpoint, progress log, and lock-pool state.

---

## Current version

```text
screen-repro v3.6
Release theme: reproducibility audit chain enhancement
Tag: v3.6
```

### v3.6 highlights

- Runtime artifacts are paired by the same `run_id`.
- `checkpoint` and `progress.log` are never silently mixed across different runs.
- Runtime status exposes `artifact_pairing_status`:
  - `PAIRED` — checkpoint and progress log belong to the same run;
  - `UNPAIRED` — usable for debugging, not a complete audit chain;
  - `MISSING` — no complete runtime artifacts found.
- `reproducibility_manifest` records key input fingerprints:
  - `config.json`;
  - project/stage `PICOS_RULES.md`;
  - `FINAL_POOL_LOCK.yaml`;
  - status script;
  - project wrapper;
  - checkpoint;
  - progress log.
- P3 monitor and status scripts are portable:
  - `--project`;
  - `--run-script`;
  - `--stage-dir`;
  - `--checkpoint-dir`.
- Relative paths are resolved against `--project`, not the current shell directory.
- The command-line status summary now directly prints `run_id` and `artifact_pairing_status`.

---

## Design principles

| Principle | Meaning |
|---|---|
| Program first | Python controls workflow, state, validation, reports, and recovery. |
| AI minimized | AI only reads full text and returns structured PICOS judgment. |
| PDF authenticity first | `pdf_path` is not evidence until DOI/title/hash checks pass. |
| SQLite as authority | Database is the source of structured truth; Markdown/CSV are readable exports. |
| Gate before downstream | QC failures stop P4/RoB/meta-analysis. |
| Reproducibility by fingerprint | PDF/text/rules/prompt/model/script/checkpoint/log are all fingerprinted. |
| Human adjudication traceable | Admin overrides require reason, evidence, UID, and timestamp. |
| No silent success | Missing or unpaired artifacts are explicitly marked. |

---

## Architecture

```text
init
  ↓
import RIS
  ↓
gate project/database inputs
  ↓
map PDFs
  ↓
pdf-qc: verify record-PDF authenticity
  ↓
extract text
  ↓
rule prescreen
  ↓
AI PICOS judgment
  ↓
write DB + Markdown records
  ↓
lock-qc: verify final lock pool
  ↓
picos-audit: identify high-risk included records
  ↓
report / export / downstream P4
```

Layer view:

```text
┌─────────────────────────────────────────────────────┐
│ Orchestration Layer                                  │
│ state machine, validation, backups, retries, logs     │
├─────────────────────────────────────────────────────┤
│ Extraction Layer                                     │
│ RIS import, PDF matching, text extraction, caching     │
├─────────────────────────────────────────────────────┤
│ Judgment Layer                                       │
│ bounded AI PICOS judgment with fixed schema            │
└─────────────────────────────────────────────────────┘
```

---

## Repository layout

```text
screen-repro/
├── SKILL.md
├── README.md
├── scripts/
│   ├── screen.py                         # compatibility wrapper to scripts_v3.0/screen.py
│   ├── p3_lock_qc.py                     # compatibility wrapper
│   └── p3_picos_lock_audit.py            # compatibility entry
├── scripts_v3.0/
│   ├── screen.py                         # maintained screening entry
│   ├── picos_judge.py                    # AI judgment backend and prompt
│   ├── record_writer.py                  # deterministic DB/MD writer and hard gates
│   ├── human_review.py                   # human review utilities
│   ├── p3_lock_qc.py                     # PDF/lock-pool gate
│   ├── p3_picos_lock_audit.py            # high-risk PICOS lock audit
│   ├── p3_runtime_status.py              # reproducible runtime status snapshot
│   ├── p3_monitor_server.py              # local monitor + supervisor
│   └── p3_monitor.html                   # read-only monitor UI
└── templates/
```

Project-local outputs usually live under:

```text
{project}/03_Screening/
├── screening.db
├── pdfs/
├── txt/
├── screening_records/
├── FINAL_POOL_LOCK.yaml
└── qc/
    ├── runtime/
    ├── lock_qc/
    ├── picos_audit/
    └── full_reaudit/
```

---

## Quick start

### 1. Clone or install the skill

```bash
git clone https://github.com/zouzibo-tech/screen-repro.git
```

In WorkBuddy, the skill is usually installed at:

```text
~/.workbuddy/skills/screen-repro/
```

The maintained scripts are under:

```text
~/.workbuddy/skills/screen-repro/scripts_v3.0/
```

### 2. Prepare a project

A typical project should contain:

```text
03_Screening/
├── pdfs/                 # full-text PDFs
├── PICOS_RULES.md         # project-specific PICOS rules
└── screening.db           # imported bibliographic records
```

### 3. Run screening workflow

```bash
python screen.py workflow --ris records.ris
```

Or run step by step:

```bash
# Import bibliographic records
python screen.py import --ris records.ris

# Map PDFs, but do not trust the mapping yet
python screen.py pdf map

# Gate 1: verify PDF mapping / lock-pool prerequisites
python scripts/p3_lock_qc.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml

# Run full-text screening
python screen.py run

# Gate 2: verify final lock pool before downstream P4/RoB/meta-analysis
python scripts/p3_lock_qc.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml

# Gate 3: focused PICOS high-risk audit for included records
python scripts/p3_picos_lock_audit.py --project . --lock 03_Screening/FINAL_POOL_LOCK.yaml
```

If `p3_lock_qc.py` returns non-zero, stop. Fix the FAIL items before entering downstream extraction, RoB, or meta-analysis.

---

## PDF authenticity gate

`screen-repro` does not trust `pdf_path` by itself.

A PDF mapping must be programmatically checked before it can support full-text screening or enter the final lock pool.

The gate checks include:

- YAML parseability;
- illegal control characters;
- record count consistency;
- PDF file existence;
- actual PDF sha256 versus lock-pool sha256;
- duplicated sha256 across different DOI/title records;
- DOI presence in first pages;
- title-token overlap in first pages;
- admin-included/admin-excluded consistency;
- excluded records leaking into P4 TSV or active RoB folders.

FAIL means stop. WARN means review and document.

---

## PICOS lock high-risk audit

Even after a lock pool passes file-level checks, it is still not guaranteed that all PICOS judgments are academically correct.

Run:

```bash
python scripts/p3_picos_lock_audit.py \
  --project . \
  --lock 03_Screening/FINAL_POOL_LOCK.yaml
```

The audit reads only the lock pool, extracted txt files, and human-readable screening records. It does not change decisions.

It flags risks such as:

- VR-internal comparator mistaken as non-VR control;
- experience-level or validation studies misread as controlled trials;
- no-control or single-group designs;
- self-report/knowledge outcomes replacing objective procedural skill outcomes;
- immediate-only post-test mistaken as retention/transfer;
- evidence quotes not found in the current extracted text.

HIGH findings require admin review before the lock pool is treated as final.

---

## Runtime monitor

P3 long-running tasks should write machine-readable heartbeat and event files:

```text
03_Screening/qc/runtime/p3_runtime_status.json
03_Screening/qc/runtime/p3_runtime_events.jsonl
```

Start the monitor:

```bash
python 03_Screening/p3_monitor_server.py
```

Or run the portable implementation directly:

```bash
python ~/.workbuddy/skills/screen-repro/scripts_v3.0/p3_monitor_server.py --project .
```

Useful options:

```bash
python ~/.workbuddy/skills/screen-repro/scripts_v3.0/p3_monitor_server.py \
  --project . \
  --run-script 03_Screening/qc/full_reaudit/run_dual_model_disputed_review_resume.py \
  --stage-dir 03_Screening/qc/full_reaudit/dual_model_disputed_review \
  --checkpoint-dir 03_Screening/qc/full_reaudit/dual_model_disputed_review/checkpoints
```

The monitor is read-only with respect to screening data. It must not directly modify:

- `screening.db`;
- lock YAML;
- PDFs;
- extracted txt;
- screening records.

Resume is controlled through the server-side supervisor and lock files.

---

## Reproducibility manifest

Every runtime status snapshot writes a `reproducibility_manifest`.

Example fields:

```json
{
  "schema_version": "p3_runtime_status.v1",
  "run_id": "p3_dual_model_disputed_review_resume_20260706_211449",
  "artifact_pairing_status": "PAIRED",
  "python_executable": ".../python.exe",
  "python_version": "3.13.x",
  "platform": "Windows-10...",
  "script": {"path": "...", "exists": true, "sha256": "..."},
  "project_wrapper": {"path": "03_Screening/p3_runtime_status.py", "exists": true, "sha256": "..."},
  "checkpoint": {"path": "...checkpoint.json", "exists": true, "sha256": "..."},
  "progress_log": {"path": "...progress.log", "exists": true, "sha256": "..."},
  "project_config": {"path": "config.json", "exists": false},
  "picos_rules_screening": {"path": "03_Screening/PICOS_RULES.md", "exists": true, "sha256": "..."},
  "final_pool_lock": {"path": "03_Screening/FINAL_POOL_LOCK.yaml", "exists": true, "sha256": "..."}
}
```

Interpretation:

- `PAIRED`: checkpoint and progress log belong to the same run.
- `UNPAIRED`: status is useful for debugging but not a complete audit chain.
- `MISSING`: required runtime artifacts are missing.

Do not explain result differences before comparing manifests.

---

## Configuration

Example `config.json`:

```json
{
  "llm_backend": "openai",
  "active_profile": "primary_gpt55",
  "openai": {
    "api_key": "sk-...",
    "model": "gpt-5.5",
    "base_url": "https://api.example.com/v1",
    "rpm": 100,
    "tpm": 10000000
  },
  "review_profiles": {
    "primary_gpt55": {
      "llm_backend": "openai",
      "api_key": "sk-...",
      "model": "gpt-5.5",
      "base_url": "https://api.example.com/v1",
      "role": "main_judge"
    },
    "review_claude_opus_4_8": {
      "llm_backend": "openai",
      "api_key": "sk-...",
      "model": "claude-opus-4-8",
      "base_url": "https://api.example.com/v1",
      "role": "reviewer_or_adjudicator"
    }
  },
  "rate_limit": {
    "safety_margin": 0.8
  },
  "picos_rules_path": "PICOS_RULES.md"
}
```

Notes:

- OpenAI-compatible endpoints should normally end with `/v1`.
- API keys may exist in local config files, but logs and reports must never expose them.
- AI output is validated and re-derived before writing final decisions.

---

## Exclusion-code hardening

Recent versions include hard gates for common false inclusions:

| Code | Meaning | Typical trigger |
|---|---|---|
| E5 | No independent control | single-group, baseline vs post-test, pre-post only |
| E7 | Review/theoretical paper | systematic review, meta-analysis, narrative review |
| E10 | VR-internal comparison | both groups use VR/simulator, differ only by feedback, haptics, 2D/3D, protocol, or expertise |

The AI may propose a judgment, but `record_writer.py` re-derives final decision logic and can downgrade unsafe output to `MAYBE`.

---

## Version history

| Version | Date | Theme |
|---|---:|---|
| v3.6 | 2026-07-08 | Reproducibility audit chain: run-id pairing, manifest input hashes, portable monitor/status scripts |
| v3.5 | 2026-07-06 | PDF authenticity gate and final lock-pool QC |
| v3.4 | 2026-06-30 | VR-internal comparator and knowledge-retention false-inclusion hardening |
| v3.3 and earlier | archived | Earlier AI-assisted screening workflow |

---

## Safety rule for downstream work

Do not enter P4 extraction, RoB assessment, or meta-analysis until:

1. PDF mapping QC is PASS;
2. final lock-pool QC is PASS;
3. high-risk PICOS audit findings are reviewed or documented;
4. runtime manifest is complete enough to explain how the current state was produced.

If any of these fail, the project may still be reproducible, but it may be reproducibly wrong.

---

## License

Research workflow tool. Use with human academic oversight. Final inclusion/exclusion decisions remain the responsibility of the review team.
