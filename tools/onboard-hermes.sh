#!/bin/bash
# SPDX-License-Identifier: MIT
# P4 onboarding: idempotent "Hermes is OK" on this phone.
#
# Skip when the agent is already installed (the update is the job). The
# remaining steps (the API-only model config, the serve unit, linger) run
# from this script or, at the first login, from omamobile-setup-user.
#
# Human-run:
#   * sudoers rule  /etc/sudoers.d/hermes-phone (visudo -f, 0440; HERMES-ONBOARDING.md §2)
#   * .env          per-LLM keys via scp from the Mac (HERMES-ONBOARDING.md §4),
#                   e.g. scp <user>@spacewar:~/GLM_API_KEY ... (or the Mac's .env
#                   dropped into the user's home and sourced by ~/.profile)
#   * loginctl enable-linger  (once, after the first login)
set -euo pipefail

log() { printf '%-12s %s\n' "$1" "$2"; }

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required but not installed"; }

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# --- already installed? The job is only to update + (re)configure. ---
if command -v hermes >/dev/null 2>&1 && hermes --version >/dev/null 2>&1; then
  log "hermes" "already installed ($(hermes --version)); updating and configuring"
  hermes update --no-backup --yes
  hermes config set model.default glm-5.3
  hermes config set model.provider zai
  hermes config set model.key_env GLM_API_KEY
  hermes doctor --fix
  log "hermes" "configured (API-only: model=glm-5.3 provider=zai key_env=GLM_API_KEY)"
  echo
  echo "Next steps (see skill spacewar-phone-ops):"
  echo "  1. sudoers : /etc/sudoers.d/hermes-phone (<user> ALL=(ALL) NOPASSWD: ALL)"
  echo "  2. secrets : .env with the LLM keys, 0600, sourced by ~/.profile"
  echo "  3. loginctl enable-linger  (once, after the first login)"
  echo "  4. hermes serve unit: ~/.config/systemd/user/hermes.service (enabled by omamobile-setup-user)"
  exit 0
fi

log "hermes" "not installed yet"
echo "The omamobile-hermes package installs and updates the agent at the first"
echo "login (hermes-bootstrap.timer retries until the network is up). No action"
echo "needed now; the agent cannot be used offline."
exit 0
