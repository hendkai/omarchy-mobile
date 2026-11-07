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
* The bootstrap is idempotent. Each run: install (if missing), `hermes
  update`, the API-only config (`model.default glm-5.3`, `model.provider zai`,
  `model.key_env GLM_API_KEY`, `hermes doctor --fix`), then `hermes.service`
  (`hermes serve`, user unit) started. Then it disables its own retry timer.
* `hermes-bootstrap.timer` runs it every 60 s until it succeeds. Enabled at
  the first login by `omamobile-setup-user`, which also tries linger.
* Only spacewar gets the package: `ROOTFS_PACKAGES` in
  `device/spacewar/device.conf` (not the all-device list).

## Human steps (not automated)

* `/etc/sudoers.d/hermes-phone`: `<user> ALL=(ALL) NOPASSWD: ALL` (visudo, 0440).
* `.env` with the LLM keys, scp from the Mac, 0600; never in the image or chat.
* `sudo loginctl enable-linger <user>` if the login step could not set it.

`tools/onboard-hermes.sh` only reports state and prints these steps.
