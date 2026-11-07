# omamobile-hermes

First-login bootstrap for the Hermes agent on the spacewar phone (P4
onboarding, `HERMES-ONBOARDING.md` §1).

## What this package ships

Everything below is installed into the root image at `/usr/share/omamobile/hermes/`:

| File | Purpose |
| --- | --- |
| `bootstrap.sh` | Idempotent one-shot: installs the Hermes agent (`install.sh`, git+uv fallback) into the user's home, then `hermes update`. Retryable at every login until the network is up. |
| `hermes-bootstrap.service` / `hermes-bootstrap.timer` | User units: the retry timer (60 s) that keeps the bootstrap running until the agent is installed. |
| `hermes.service` | `hermes serve` systemd user unit template. |
| `LICENSE`, `version` | Licensing + version. |

## Behaviour

* The **agent** (the `~/.local/bin/hermes` binary) is **not** in the image:
  it is installed once, by `bootstrap.sh`, from
  `https://hermes-agent.nousresearch.com/install.sh` (git+uv fallback on
  ARM64). The root image can therefore be built offline.
* The bootstrap is idempotent: a second run exits immediately after the
  first successful install/update.
* `hermes-bootstrap.timer` runs the bootstrap every 60 s until the agent
  answers `hermes --version`; the timer is enabled at the first login
  (`omamobile-setup-user`, `omamobile-hermes` step). A firstboot that never
  logged in runs the bootstrap once directly.
* The `hermes config set model.default glm-5.3`, `model.provider zai`,
  `model.key_env GLM_API_KEY` and `hermes doctor --fix` steps (API-only,
  no phone gateway) run only after the agent is installed.

## Onboarding wrapper

`tools/onboard-hermes.sh` is the human-run wrapper (skip if the agent is
already installed): update, configure, copy the serve unit, print the
`loginctl enable-linger` instruction.
