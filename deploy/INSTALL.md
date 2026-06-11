# Deploy on Bazzite (podman quadlets + user timers)

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
