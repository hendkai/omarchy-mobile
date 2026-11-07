#!/bin/bash
# SPDX-License-Identifier: MIT
# omamobile-hermes — idempotent first-login bootstrap for the Hermes agent.
#
# Runs as the user (omamobile-setup-user), typically at the first login after
# the phone boots. The hermes-bootstrap.timer retries it every 60 s until the
# network is up, so a first login before Wi-Fi retrieves the agent later.
#
#   * Primary path:  curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
#     (the installer sets up uv + venv + launcher in the user's home).
#   * Fallback:      git clone + uv sync of the agent (ARM64 fallback).
#   * Once installed: "hermes update --no-backup --yes" — the first time.
#
# Idempotent: a second run exits immediately after the first success.
set -euo pipefail

home=${HOME:-$HOME}
bin_dir=${HERMES_BIN_DIR:-$HOME/.local/bin}
hermes=$bin_dir/hermes
marker=$HOME/.local/state/omamobile/hermes-installed
mkdir -p "$(dirname "$marker")"

# 0. Already installed and answering? Reconcile (update) and stop.
if command -v hermes >/dev/null 2>&1 && "$hermes" --version >/dev/null 2>&1; then
  "$hermes" update --no-backup --yes >/dev/null 2>&1 || true
  exit 0
fi

# 1. Network up? The installer asks the network for its channel. A plain
#    outbound-HTTPS probe doubles as the ping gate for the whole bootstrap.
if ! curl -fsS --max-time 15 -o /dev/null https://hermes-agent.nousresearch.com/install.sh 2>/dev/null; then
  echo "omamobile-hermes: no network yet; the retry timer will run us again" >&2
  exit 1
fi

# 2. Primary: the official installer.
if command -v curl >/dev/null 2>&1; then
  curl -fsSL --max-time 300 https://hermes-agent.nousresearch.com/install.sh | HOME="$HOME" bash
  curl_rc=${PIPESTATUS[0]:-0}
else
  curl_rc=127
fi

# 3. Fallback (ARM64): git clone + uv sync.
if [[ $curl_rc -ne 0 || ! -x $hermes ]]; then
  if command -v git >/dev/null 2>&1 && command -v uv >/dev/null 2>&1; then
    rm -rf "$HOME/.hermes/hermes-agent"
    if git clone --depth 1 https://github.com/NousResearch/hermes-agent "$HOME/.hermes/hermes-agent" 2>/dev/null; then
      if ( cd "$HOME/.hermes/hermes-agent" && uv sync ) 2>/dev/null; then
        for v in "$HOME/.hermes/hermes-agent/.venv/bin/hermes" "$HOME/.hermes/hermes-agent/bin/hermes"; do
          if [[ -x $v ]]; then ln -sf "$v" "$hermes"; break; fi
        done
      fi
    fi
  fi
fi

# 4. Verify; if it still isn't there, the timer keeps retrying.
if [[ -x $hermes ]] && "$hermes" --version >/dev/null 2>&1; then
  "$hermes" update --no-backup --yes >/dev/null 2>&1 || true
  touch "$marker"
  exit 0
fi

echo "omamobile-hermes: the agent could not be installed (installer rc=$curl_rc)" >&2
exit 1
