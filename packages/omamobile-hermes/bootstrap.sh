#!/bin/bash
# SPDX-License-Identifier: MIT
# omamobile-hermes: idempotent bootstrap for the Hermes agent on the phone
# (HERMES-ONBOARDING.md §1-§5). Runs as the user, from the retry timer
# (hermes-bootstrap.timer, every 60 s) and once from omamobile-setup-user:
#
#   install  the official installer; if that fails, git + uv (ARM64 fallback)
#   update   hermes update, every run
#   config   API-only: model glm-5.3 on zai, key from GLM_API_KEY; no gateway
#   serve    hermes.service (hermes serve, a user unit); then the timer stops
#
# Exit 0 once the agent works and is configured, 1 otherwise (the timer
# runs it again). Nothing here reads or writes a secret: the LLM keys come
# from the user's .env (scp'd from the Mac, runbook step); only names are set.
set -uo pipefail

hermes=$HOME/.local/bin/hermes
share=/usr/share/omamobile/hermes
state=$HOME/.local/state/omamobile
log() { echo "omamobile-hermes: $*"; }
agent_ok() { [[ -x $hermes ]] && "$hermes" --version >/dev/null 2>&1; }

if ! agent_ok; then
  # 1. No point starting the installer offline; the timer comes back.
  if ! curl -fsS --max-time 15 -o /dev/null https://hermes-agent.nousresearch.com/install.sh; then
    log "no network yet; the retry timer runs this again"
    exit 1
  fi

  # 2. The official installer (uv, venv and launcher in the user's home).
  if curl -fsSL --max-time 900 https://hermes-agent.nousresearch.com/install.sh | bash; then
    log "installer finished"
  else
    log "installer failed; trying git + uv"
  fi

  # 3. ARM64 fallback: a git checkout with a uv-made venv, launcher linked.
  if ! agent_ok && command -v git >/dev/null && command -v uv >/dev/null; then
    src=$HOME/.hermes/hermes-agent
    if [[ -d $src/.git ]] || git clone --depth 1 https://github.com/NousResearch/hermes-agent "$src"; then
      if (cd "$src" && uv sync) && [[ -x $src/.venv/bin/hermes ]]; then
        mkdir -p "$HOME/.local/bin"
        ln -sfn "$src/.venv/bin/hermes" "$hermes"
      fi
    fi
  fi

  if ! agent_ok; then
    log "the agent is not installed yet; the retry timer runs this again"
    exit 1
  fi
fi

# 4. Update, then the API-only configuration (idempotent sets).
"$hermes" update --no-backup --yes || log "hermes update failed; keeping the installed version"
"$hermes" config set model.default glm-5.3 &&
  "$hermes" config set model.provider zai &&
  "$hermes" config set model.key_env GLM_API_KEY || { log "config failed"; exit 1; }
"$hermes" doctor --fix || log "hermes doctor --fix reported problems (run hermes doctor)"

# 5. hermes serve as a user unit; linger (set up at login) keeps it up.
mkdir -p "$HOME/.config/systemd/user"
install -Dm644 "$share/hermes.service" "$HOME/.config/systemd/user/hermes.service"
systemctl --user daemon-reload
systemctl --user enable --now hermes.service || { log "could not start hermes.service"; exit 1; }

# 6. Done: the retry timer has nothing left to do.
systemctl --user disable --now hermes-bootstrap.timer || log "could not disable the retry timer"
mkdir -p "$state" && date -Is > "$state/hermes-installed"
log "hermes installed, configured and serving"
exit 0
