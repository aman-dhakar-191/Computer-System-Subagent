# Computer System Subagent — Deploy Runbook

How to change the FastAPI subagent (`agent.py`), ship it to the VPS, and restart the service.

**Known facts (as of last deploy):**

- **GitHub repo:** `https://github.com/aman-dhakar-191/Computer-System-Subagent.git`
- **VPS project path:** `/root/computer-subagent/`
- **Main file:** `/root/computer-subagent/agent.py`
- **Service manager:** `systemd` → unit name `computer-subagent.service` (uvicorn on port `8765`)
- **Runs on:** the **host** (NOT inside the `coder-sandbox` container). The n8n agent cannot restart it — this is a manual SSH step.

---

## The loop, in one line

Edit in VSCode (Windows) → push to GitHub → SSH to VPS → `git pull` → `systemctl restart`.

---

## Step 1 — Edit locally (Windows / VSCode)

If not already cloned:

```bash
git clone https://github.com/aman-dhakar-191/Computer-System-Subagent.git
cd Computer-System-Subagent
```

If already cloned, pull first so you start from the live version:

```bash
git pull
```

Make your changes to `agent.py` (e.g. the `blocked` list, handlers, etc.), then:

```bash
git add agent.py
git commit -m "Describe the change"
git push
```

---

## Step 2 — Deploy on the VPS (SSH to the host)

One command does pull + restart + status check:

```bash
cd /root/computer-subagent && git pull && systemctl restart computer-subagent.service && systemctl status computer-subagent.service --no-pager | head -5
```

Confirm the output shows `Active: active (running)` with a fresh `Main PID`.

---

## Verify the service is up

```bash
# port is listening
ss -ltnp | grep 8765

# service answers (lists available actions)
curl -s http://127.0.0.1:8765/actions
```

If `/actions` returns a JSON list, the new code is live.

---

## If you ever lose the paths (rediscover from scratch)

**Find the project file** (excludes the sandbox work dir):

```bash
grep -rl "Computer System Subagent" /root /home /opt /srv 2>/dev/null | grep -v /home/coder/work
```

**Confirm the dir + git remote + branch:**

```bash
cd /root/computer-subagent && ls -la && git remote -v && git branch --show-current && git status --short
```

**Find how the service runs / its name** (only one block will show output):

```bash
echo "--- systemd ---"; systemctl list-units --type=service --all 2>/dev/null | grep -iE "subagent|fastapi|uvicorn|8765"
echo "--- pm2 ---";     pm2 list 2>/dev/null
echo "--- port 8765 ---"; ss -ltnp 2>/dev/null | grep 8765
echo "--- docker ---";  docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -i 8765
```

---

## Troubleshooting

**`git pull` blocked by local changes on the VPS.**
Untracked noise (`__pycache__/`, `subagent.log`) is fine and won't block a pull. If a *tracked* file was hand-edited on the VPS and conflicts:

```bash
cd /root/computer-subagent
git stash            # set aside local edits
git pull
git stash drop       # discard them (VPS should mirror GitHub, not diverge)
```

Rule of thumb: **never hand-edit `agent.py` on the VPS.** Edit in VSCode, push, pull. The VPS is a mirror of GitHub.

**Service fails to start after a pull** (bad Python, syntax error):

```bash
# see the actual error
journalctl -u computer-subagent.service -n 40 --no-pager

# roll back to the previous commit and restart
cd /root/computer-subagent
git log --oneline -5              # find the last good commit hash
git reset --hard <good_hash>
systemctl restart computer-subagent.service
```

Then fix forward in VSCode and push again.

**Quiet the untracked-file noise** (optional, one-time, in VSCode):
add to `.gitignore`, commit, push:

```
__pycache__/
*.log
venv/
```

---

## Reference: the command blocklist

In `agent.py`, `handle_run_command` enforces a host-side blocklist before dispatching into the sandbox:

```python
blocked = ["format", "shutdown", "mkfs", ":(){:|:&}", "dd if="]
```

- These stay blocked because they can harm the **host**, even though commands run inside the sandbox.
- `rm -rf` was intentionally removed (the sandbox is disposable). After that change, the only guard on destructive commands is the Superviser's typed-YES prompt — a prompt rule, not a hard code gate.
- To re-block something, add the substring back to this list, push, and redeploy (Steps 1–2). Matching is a lowercase substring check on the command.
