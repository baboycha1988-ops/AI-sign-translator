# Database (stage 3)

- `migrations/0001_initial.sql` — full schema: 17 tables required by the spec plus
  module 20 (sign proposals, consents, signers, dataset versions, jobs, model signs).
- `tests/constraints.sql` — checks (about 14 expected-error cases plus positive assertions) that the database itself rejects invalid data
  (state machine, consent rules, frozen dataset versions, one current model, append-only audit log).
- `scripts/check_schema.py` — static structural check, works without PostgreSQL.
- `scripts/db_verify.sh` — fresh PostgreSQL 16 in Docker + migration + tests.

## Status
Static check: PASSED (17/17 spec tables, 63 foreign keys resolved).
Execution on real PostgreSQL: NOT YET DONE (no PostgreSQL in the authoring environment).
Run `./scripts/db_verify.sh` and send me the output before stage 4 relies on this schema.

## Key design rules enforced in SQL
1. A model can be `current` only if it passed the gate, has an artifact, and its sign language
   has `evidence_status = 'validated'` (no Tajik claims without a validated dataset).
2. A sample needs a live, matching consent; revoking consent soft-deletes samples and queues file deletion.
3. Only QC-passed, verified samples can enter a dataset version; frozen versions are immutable.
4. `v_split_signer_leakage` must be empty before training: one signer never spans train/val/test.
5. `audit_logs` is append-only. No permission grants access to private user videos (none are stored).
