# Installing gexwheel

## Option A - installer script (any Linux, recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/nsb1014/gexwheel/main/install.sh | bash
```

or from a checkout: `./install.sh`. It prompts for your Discord webhook
(hidden) and optional PRAW credentials (hidden), writes
`~/gexwheel-data/config.yaml` (chmod 600), creates a virtualenv under
`~/.local/share/gexwheel/app`, and enables the two systemd **user** timers:

| Timer | Schedule | Job |
|-------|----------|-----|
| `gexwheel-mentions.timer` | daily 07:00 ET | Reddit mention scan |
| `gexwheel-morning.timer` | Mon-Fri 07:15 ET | GEX screen + Discord alerts |

Useful afterwards:

```bash
systemctl --user list-timers 'gexwheel-*'        # next fire times
systemctl --user start gexwheel-mentions.service # one manual run
journalctl --user -u gexwheel-morning.service -e # logs
```

Re-running the installer pulls the latest code and reinstalls dependencies but
never overwrites an existing `config.yaml`. If no systemd user session exists
(e.g. some containers/WSL setups), the installer skips timer setup - schedule
`python -m gexwheel mentions` / `morning` yourself (cron works; set
`PYTHONPATH=<install>/src` and `GEXWHEEL_CONFIG=~/gexwheel-data/config.yaml`).

## Option B - containers (podman quadlets + user timers)

For image-based hosts (e.g. Bazzite/Silverblue):

```bash
# 1. data dir + config
mkdir -p ~/gexwheel-data
cp config/config.example.yaml ~/gexwheel-data/config.yaml
$EDITOR ~/gexwheel-data/config.yaml   # set discord webhook_url; db_path stays /data/gexwheel.db

# 2. build image
podman build -t localhost/gexwheel:latest -f deploy/Containerfile .

# 3. install units
mkdir -p ~/.config/containers/systemd ~/.config/systemd/user
cp deploy/*.container ~/.config/containers/systemd/
cp deploy/*.timer     ~/.config/systemd/user/
systemctl --user daemon-reload

# 4. sanity checks
podman run --rm -v ~/gexwheel-data:/data:Z localhost/gexwheel:latest test-discord
systemctl --user start gexwheel-mentions.service     # one manual run
journalctl --user -u gexwheel-mentions.service -e

# 5. enable timers (+ survive logout)
systemctl --user enable --now gexwheel-mentions.timer gexwheel-morning.timer
loginctl enable-linger $USER
```

Rebuild after code changes: rerun step 2 (timers pick up the new image on next fire).
