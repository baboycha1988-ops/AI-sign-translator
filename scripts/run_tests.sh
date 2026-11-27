#!/usr/bin/env bash
# Runs every test suite that can run in the current environment and prints REAL results.
# Exit code is non-zero if any executed suite fails. Skipped tests are reported as skipped, not as passed.
set -u
cd "$(dirname "$0")/.."
fail=0
run() { echo; echo "=== $1 ==="; shift; ( "$@" ) || fail=1; }

run "repository check"        python3 scripts/check_repo.py
run "schema structure check"  python3 scripts/check_schema.py db/migrations/0001_initial.sql
run "API tests"               bash -c 'cd apps/api && python3 -m unittest discover -s tests -v 2>&1 | tail -n 25; exit ${PIPESTATUS[0]}'
run "ML tests"                bash -c 'cd packages/ml && python3 -m unittest discover -s tests 2>&1 | tail -n 15; exit ${PIPESTATUS[0]}'

echo
if [ "$fail" -eq 0 ]; then echo "ALL EXECUTED SUITES PASSED (see 'skipped' counts above; DB tests are separate: scripts/db_verify.sh)";
else echo "SOME SUITES FAILED"; fi
exit $fail
