#!/usr/bin/env bash
# Publishes this folder to a NEW empty GitHub repository.
# Usage:  ./scripts/publish_github.sh https://github.com/<user>/<repo>.git
# Create the empty repository on github.com first (no README/.gitignore/license there).
# You will be asked for your GitHub login; use a Personal Access Token as the password (not your account password).
set -euo pipefail
cd "$(dirname "$0")/.."
REMOTE="${1:?usage: $0 https://github.com/<user>/<repo>.git}"
python3 scripts/check_repo.py
[ -d .git ] || git init -b main
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-$(git config user.name || echo developer)}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-$(git config user.email || echo developer@example.com)}" \
    commit -m "Initial commit: AI Sign Translator (stages 1-7)" || echo "nothing new to commit"
git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"
git push -u origin main
echo "Published. Open the repository on GitHub, tab Actions, to see the automatic test run."
