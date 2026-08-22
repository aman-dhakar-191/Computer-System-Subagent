"""
Computer / System Subagent
Receives structured tasks from n8n, executes them, returns SUCCESS/FAILED/CONFIRMATION_REQUIRED.
Command execution and file operations run inside the Docker sandbox 'coder-sandbox',
confined to the coder user's work directory. System-info reads (psutil) stay on the host.
"""

import os
import subprocess
import platform
import psutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any
import uvicorn

app = FastAPI(title="Computer System Subagent")

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class Task(BaseModel):
    action: str
    params: dict[str, Any] = {}

class Result(BaseModel):
    status: str
    operation: str
    result: Any = None
    reason: str = None
    risk: str = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ok(operation: str, result: Any) -> dict:
    return {"status": "SUCCESS", "operation": operation, "result": result}

def fail(operation: str, reason: str) -> dict:
    return {"status": "FAILED", "operation": operation, "reason": reason}

def confirm(operation: str, risk: str) -> dict:
    return {"status": "CONFIRMATION_REQUIRED", "operation": operation, "risk": risk}

import re

# Match lines where a secret keyword appears as an assignment/label, not mid-word.
# e.g. "password=abc", "TOKEN: xyz", "secret_key = abc" → redacted
# but "passed", "apikey.js", "Authorization: Bearer ..." → not redacted
_REDACT_RE = re.compile(
    r'(?<!\w)(password|token|secret|api_key|auth_key|credential|private_key)(?!\w)\s*[:=]',
    re.IGNORECASE,
)

def redact(text: str) -> str:
    """Scrub likely secrets from command output."""
    return "\n".join(
        "[REDACTED]" if _REDACT_RE.search(line) else line
        for line in text.splitlines()
    )

# ---------------------------------------------------------------------------
# Sandbox execution
# ---------------------------------------------------------------------------

SANDBOX = "coder-sandbox"
WORKDIR = "/home/coder/work"

def _safe_path(path: str) -> str | None:
    """Resolve a user path under WORKDIR. Reject escapes. Returns container path or None."""
    if not path:
        return None
    p = path.strip().lstrip("/")
    if ".." in p.split("/"):
        return None
    full = os.path.normpath(os.path.join(WORKDIR, p))
    if not (full == WORKDIR or full.startswith(WORKDIR + "/")):
        return None
    return full

def sandbox_exec(command: str, timeout: int = 30, stdin_data: str = None):
    """Run a shell command inside the sandbox container as coder, in WORKDIR."""
    docker_cmd = [
        "docker", "exec", "-i",
        "-u", "coder",
        "-w", WORKDIR,
        SANDBOX,
        "bash", "-lc", command,
    ]
    return subprocess.run(
        docker_cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

# ---------------------------------------------------------------------------
# Handlers  (system info = host via psutil; execution/files = sandbox)
# ---------------------------------------------------------------------------

def handle_get_cpu(params):
    return ok("get_cpu", {
        "percent": psutil.cpu_percent(interval=1),
        "count_logical": psutil.cpu_count(),
        "count_physical": psutil.cpu_count(logical=False),
        "freq_mhz": psutil.cpu_freq().current if psutil.cpu_freq() else None,
    })

def handle_get_ram(params):
    vm = psutil.virtual_memory()
    return ok("get_ram", {
        "total_gb": round(vm.total / 1e9, 2),
        "available_gb": round(vm.available / 1e9, 2),
        "used_gb": round(vm.used / 1e9, 2),
        "percent": vm.percent,
    })

def handle_get_disk(params):
    path = params.get("path", "/")
    try:
        usage = psutil.disk_usage(path)
        return ok("get_disk", {
            "path": path,
            "total_gb": round(usage.total / 1e9, 2),
            "used_gb": round(usage.used / 1e9, 2),
            "free_gb": round(usage.free / 1e9, 2),
            "percent": usage.percent,
        })
    except Exception as e:
        return fail("get_disk", str(e))

def handle_get_system_info(params):
    uname = platform.uname()
    return ok("get_system_info", {
        "os": uname.system,
        "node": uname.node,
        "release": uname.release,
        "version": uname.version,
        "machine": uname.machine,
        "processor": uname.processor,
        "python": platform.python_version(),
    })

def handle_list_processes(params):
    name_filter = params.get("name_filter", "").lower()
    procs = []
    for p in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            if name_filter and name_filter not in info["name"].lower():
                continue
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return ok("list_processes", procs)

def handle_kill_process(params):
    pid = params.get("pid")
    name = params.get("name")
    if not pid and not name:
        return fail("kill_process", "Provide pid or name")
    if not params.get("confirmed"):
        target = f"PID {pid}" if pid else f"name={name}"
        return confirm("kill_process", f"This will terminate process {target}. Resend with confirmed=true.")
    try:
        if pid:
            psutil.Process(int(pid)).terminate()
            return ok("kill_process", f"Terminated PID {pid}")
        else:
            killed = []
            for p in psutil.process_iter(["pid", "name"]):
                if p.info["name"].lower() == name.lower():
                    p.terminate()
                    killed.append(p.info["pid"])
            return ok("kill_process", f"Terminated PIDs: {killed}")
    except Exception as e:
        return fail("kill_process", str(e))

def handle_run_command(params):
    command = params.get("command", "").strip()
    if not command:
        return fail("run_command", "No command provided")
    # Blocklist (checked on host, before dispatch into the sandbox)
    blocked = ["format", "shutdown", "mkfs", ":(){:|:&}", "dd if="]
    for b in blocked:
        if b in command.lower():
            return fail("run_command", f"Blocked: '{b}' is not permitted")
    # Destructive ops require confirmation
    destructive = ["rmdir", "remove-item", "del ", "rd /"]
    if any(d in command.lower() for d in destructive) and not params.get("confirmed"):
        return confirm("run_command", f"Destructive command: '{command}'. Resend with confirmed=true.")
    try:
        result = sandbox_exec(command, timeout=params.get("timeout", 30))
        return ok("run_command", {
            "stdout": redact(result.stdout.strip()),
            "stderr": redact(result.stderr.strip()),
            "returncode": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return fail("run_command", "Command timed out")
    except Exception as e:
        return fail("run_command", str(e))

def handle_open_app(params):
    # Not meaningful in a headless sandbox; fail cleanly.
    return fail("open_app", "open_app is not supported in the sandbox environment")

def handle_list_directory(params):
    path = params.get("path", ".")
    full = _safe_path(path)
    if full is None:
        return fail("list_directory", "Path must be inside the work directory")
    try:
        result = sandbox_exec(f"ls -lAp --time-style=+ {full!r} 2>&1 || true")
        return ok("list_directory", {"path": full, "listing": result.stdout.strip()})
    except Exception as e:
        return fail("list_directory", str(e))

def handle_read_file(params):
    path = params.get("path", "")
    full = _safe_path(path)
    if full is None:
        return fail("read_file", "Path must be inside the work directory")
    max_bytes = params.get("max_bytes", 100_000)
    try:
        result = sandbox_exec(f"head -c {int(max_bytes)} {full!r}")
        if result.returncode != 0:
            return fail("read_file", result.stderr.strip() or "read failed")
        return ok("read_file", {"path": full, "content": redact(result.stdout)})
    except Exception as e:
        return fail("read_file", str(e))

def handle_write_file(params):
    path = params.get("path", "")
    content = params.get("content", "")
    mode = params.get("mode", "write")  # "write" | "append" | "patch"
    full = _safe_path(path)
    if full is None:
        return fail("write_file", "Path must be inside the work directory")
    if mode not in ("write", "append", "patch"):
        return fail("write_file", f"Unknown mode '{mode}'. Use write, append, or patch.")
    try:
        if mode == "append":
            result = sandbox_exec(f"mkdir -p \"$(dirname {full!r})\" && cat >> {full!r}", stdin_data=content)
            if result.returncode != 0:
                return fail("write_file", result.stderr.strip() or "append failed")
            return ok("write_file", f"Appended: {full}")
        if mode == "patch":
            # content must be a unified diff (diff -u / git diff format)
            result = sandbox_exec(f"patch -u {full!r}", stdin_data=content)
            if result.returncode != 0:
                return fail("write_file", result.stderr.strip() or result.stdout.strip() or "patch failed")
            return ok("write_file", f"Patched: {full}")
        # mode == "write" — original behaviour, confirm before overwrite
        if not params.get("confirmed"):
            check = sandbox_exec(f"test -e {full!r} && echo EXISTS || echo NEW")
            if "EXISTS" in check.stdout:
                return confirm("write_file", f"File '{full}' already exists and will be overwritten. Resend with confirmed=true.")
        result = sandbox_exec(f"mkdir -p \"$(dirname {full!r})\" && cat > {full!r}", stdin_data=content)
        if result.returncode != 0:
            return fail("write_file", result.stderr.strip() or "write failed")
        return ok("write_file", f"Written: {full}")
    except Exception as e:
        return fail("write_file", str(e))

def handle_get_network_info(params):
    addrs = {}
    for iface, snics in psutil.net_if_addrs().items():
        addrs[iface] = [{"family": str(s.family), "address": s.address} for s in snics]
    stats = psutil.net_io_counters()
    return ok("get_network_info", {
        "interfaces": addrs,
        "bytes_sent": stats.bytes_sent,
        "bytes_recv": stats.bytes_recv,
    })

# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

HANDLERS = {
    "get_cpu":          handle_get_cpu,
    "get_ram":          handle_get_ram,
    "get_disk":         handle_get_disk,
    "get_system_info":  handle_get_system_info,
    "list_processes":   handle_list_processes,
    "kill_process":     handle_kill_process,
    "run_command":      handle_run_command,
    "open_app":         handle_open_app,
    "list_directory":   handle_list_directory,
    "read_file":        handle_read_file,
    "write_file":       handle_write_file,
    "get_network_info": handle_get_network_info,
}

# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@app.post("/task", response_model=Result)
def execute_task(task: Task):
    handler = HANDLERS.get(task.action)
    if not handler:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown action '{task.action}'. Valid: {sorted(HANDLERS)}"
        )
    return handler(task.params)

@app.get("/actions")
def list_actions():
    return {"actions": sorted(HANDLERS)}

# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)