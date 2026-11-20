"""Periodic reindexing and the shared MCP server: launchd on macOS, systemd user units on Linux."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "dev.session-rag.index"
SERVE_LABEL = "dev.session-rag.serve"


def _command(*args: str) -> list[str]:
    exe = shutil.which("session-rag")
    return [exe, *args] if exe else [sys.executable, "-m", "session_rag.cli", *args]


def _path() -> str:
    # launchd/systemd start with a minimal PATH; knowledge updates need the `claude` CLI
    dirs = [str(Path(x).parent) for x in (shutil.which("claude"), shutil.which("session-rag")) if x]
    return ":".join(dict.fromkeys([*dirs, "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]))


def install(minutes: int = 30) -> str:
    cmd = _command("index", "--quiet")
    if sys.platform == "darwin":
        from .config import DATA_DIR

        plist = Path.home() / f"Library/LaunchAgents/{LABEL}.plist"
        args = "".join(f"<string>{a}</string>" for a in cmd)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict><key>HF_HUB_OFFLINE</key><string>1</string><key>PATH</key><string>{_path()}</string></dict>
  <key>StartInterval</key><integer>{minutes * 60}</integer>
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>{DATA_DIR}/index.log</string>
  <key>StandardErrorPath</key><string>{DATA_DIR}/index.log</string>
</dict></plist>
""")
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{LABEL}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)], check=True)
        return f"installed {plist}"
    unit = Path.home() / ".config/systemd/user"
    unit.mkdir(parents=True, exist_ok=True)
    (unit / "session-rag.service").write_text(
        f"[Unit]\nDescription=session-rag index\n\n[Service]\nType=oneshot\nNice=10\n"
        f"Environment=HF_HUB_OFFLINE=1\nEnvironment=PATH={_path()}\nExecStart={' '.join(cmd)}\n"
    )
    (unit / "session-rag.timer").write_text(
        f"[Unit]\nDescription=session-rag periodic index\n\n[Timer]\nOnBootSec=2min\n"
        f"OnUnitActiveSec={minutes}min\n\n[Install]\nWantedBy=timers.target\n"
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "session-rag.timer"], check=True)
    return f"installed {unit}/session-rag.timer"


def install_server(port: int = 47817) -> str:
    """One MCP server for every Claude Code session on the machine (``session-rag serve --http``), kept alive.
    Register it in ~/.claude.json as {"type": "http", "url": "http://127.0.0.1:<port>/mcp"} instead of the stdio
    command, so a new session connects to it instead of starting a copy with its own model (~1 GB each)."""
    cmd = _command("serve", "--http", "--port", str(port))
    env = {"HF_HUB_OFFLINE": "1", "PATH": _path(), "SESSION_RAG_THREADS": "2", "OMP_NUM_THREADS": "2",
           "TOKENIZERS_PARALLELISM": "false"}
    if sys.platform == "darwin":
        from .config import DATA_DIR

        plist = Path.home() / f"Library/LaunchAgents/{SERVE_LABEL}.plist"
        args = "".join(f"<string>{a}</string>" for a in cmd)
        envs = "".join(f"<key>{k}</key><string>{v}</string>" for k, v in env.items())
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{SERVE_LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict>{envs}</dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>5</integer>
  <key>StandardOutPath</key><string>{DATA_DIR}/serve.log</string>
  <key>StandardErrorPath</key><string>{DATA_DIR}/serve.log</string>
</dict></plist>
""")
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{SERVE_LABEL}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)], check=True)
        return f"installed {plist} (http://127.0.0.1:{port}/mcp)"
    unit = Path.home() / ".config/systemd/user"
    unit.mkdir(parents=True, exist_ok=True)
    envlines = "".join(f"Environment={k}={v}\n" for k, v in env.items())
    (unit / "session-rag-serve.service").write_text(
        f"[Unit]\nDescription=session-rag MCP server\n\n[Service]\nRestart=always\nRestartSec=5\nNice=5\n"
        f"{envlines}ExecStart={' '.join(cmd)}\n\n[Install]\nWantedBy=default.target\n"
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "session-rag-serve.service"], check=True)
    return f"installed {unit}/session-rag-serve.service (http://127.0.0.1:{port}/mcp)"


def uninstall_server() -> str:
    if sys.platform == "darwin":
        plist = Path.home() / f"Library/LaunchAgents/{SERVE_LABEL}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{SERVE_LABEL}"], capture_output=True)
        plist.unlink(missing_ok=True)
        return f"removed {plist}"
    subprocess.run(["systemctl", "--user", "disable", "--now", "session-rag-serve.service"], capture_output=True)
    return "disabled session-rag-serve.service"


def uninstall() -> str:
    if sys.platform == "darwin":
        plist = Path.home() / f"Library/LaunchAgents/{LABEL}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{LABEL}"], capture_output=True)
        plist.unlink(missing_ok=True)
        return f"removed {plist}"
    subprocess.run(["systemctl", "--user", "disable", "--now", "session-rag.timer"], capture_output=True)
    return "disabled session-rag.timer"


def _uid() -> int:
    import os

    return os.getuid()
