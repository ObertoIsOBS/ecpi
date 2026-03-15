"""
Detect available package managers and perform search/install.
"""

import os
import subprocess
import shutil
import sys
import threading
from typing import Any, Optional


def looks_like_git_repo(query: str) -> bool:
    """True if query looks like a git URL or owner/repo."""
    q = query.strip()
    if not q:
        return False
    if q.startswith(("https://", "http://", "git@", "git://")):
        return True
    # owner/repo (no spaces, one slash)
    if "/" in q and " " not in q and q.count("/") == 1:
        return True
    return False


def git_clone_result(query: str) -> Optional[dict]:
    """If query looks like a repo and git is available, return a single result for git clone."""
    if not looks_like_git_repo(query):
        return None
    path = shutil.which("git")
    if not path:
        return None
    # Normalize to URL for clone (owner/repo -> https://github.com/owner/repo)
    clone_target = query.strip()
    if "/" in clone_target and " " not in clone_target and not any(
        clone_target.startswith(p) for p in ("https://", "http://", "git@", "git://")
    ):
        clone_target = f"https://github.com/{clone_target}"
    return {
        "manager": "git",
        "bin": path,
        "name": clone_target,
        "repo": "",
        "description": "Clone repository with git",
        "source": "git clone",
    }


# (name, binary, search_cmd, install_cmd, list_all_cmd, output_parser)
# list_all_cmd used for fuzzy suggestions when search returns nothing
MANAGER_CONFIG = [
    {
        "name": "pacman",
        "bin": "pacman",
        "search_cmd": ["-Ss"],
        "install_cmd": ["-S", "--noconfirm"],
        "list_all_cmd": ["-Slq"],
        "sources": ["official repos"],
    },
    {
        "name": "paru",
        "bin": "paru",
        "search_cmd": ["-Ss"],
        "install_cmd": ["-S", "--noconfirm"],
        "list_all_cmd": ["-Ssq"],
        "sources": ["official repos", "AUR"],
    },
    {
        "name": "yay",
        "bin": "yay",
        "search_cmd": ["-Ss"],
        "install_cmd": ["-S", "--noconfirm"],
        "list_all_cmd": ["-Ssq"],
        "sources": ["official repos", "AUR"],
    },
    {
        "name": "yum",
        "bin": "yum",
        "search_cmd": ["search"],
        "install_cmd": ["install", "-y"],
        "list_all_cmd": ["list", "all"],
        "sources": ["RHEL/Fedora repos"],
    },
    {
        "name": "fisher",
        "bin": "fisher",
        "search_cmd": [],
        "install_cmd": ["add"],
        "list_all_cmd": [],
        "sources": ["fish plugin registry"],
        "shell": "fish",  # only relevant when $SHELL is fish
    },
]


def detect_managers(include_shell_specific: bool = True) -> list[dict[str, Any]]:
    """Return list of available package managers (path and config).
    When include_shell_specific is True, shell-specific managers (e.g. fisher)
    are only included if they match the current $SHELL.
    """
    shell = os.environ.get("SHELL", "").lower()
    found = []
    for cfg in MANAGER_CONFIG:
        path = shutil.which(cfg["bin"])
        if not path:
            continue
        req_shell = cfg.get("shell")  # e.g. "fish" for fisher
        if req_shell and include_shell_specific and req_shell not in shell:
            continue  # skip fisher when not in fish shell
        active_for_shell = bool(req_shell and req_shell in shell)
        found.append({
            "name": cfg["name"],
            "bin": path,
            "search_cmd": cfg["search_cmd"],
            "install_cmd": cfg["install_cmd"],
            "list_all_cmd": cfg.get("list_all_cmd", []),
            "sources": cfg.get("sources", []),
            "active_for_shell": active_for_shell,
            "shell": req_shell,
        })
    return found


def list_installers_for_display(include_all: bool = True) -> list[dict[str, Any]]:
    """List all detected installers (including those not active for current shell).
    Used for --show-installers. When include_all is True, runs detection twice:
    once with shell filter (normal) and once without to show "available but not for this shell".
    """
    # Get managers that are "in path" (ignore shell filter to show everything)
    shell = os.environ.get("SHELL", "").lower()
    result = []
    for cfg in MANAGER_CONFIG:
        path = shutil.which(cfg["bin"])
        if not path:
            continue
        req_shell = cfg.get("shell")
        active = bool(req_shell and req_shell in shell)
        result.append({
            "name": cfg["name"],
            "bin": path,
            "sources": cfg.get("sources", []),
            "active_for_shell": active,
            "shell": req_shell,
        })
    return result


def _parse_pacman_like(stdout: str, manager_name: str, bin_path: str) -> list[dict]:
    """Parse pacman/paru/yay -Ss output."""
    results = []
    # Format: "repo name" or "repo/name" then "    description"
    current_name = None
    current_repo = None
    current_desc = []
    for line in stdout.splitlines():
        if line.startswith(" ") or line.startswith("\t"):
            current_desc.append(line.strip())
            continue
        if current_name is not None and current_desc:
            results.append({
                "manager": manager_name,
                "bin": bin_path,
                "name": current_name,
                "repo": current_repo or "",
                "description": " ".join(current_desc).strip() or "(no description)",
                "source": current_repo or "repo",
            })
        current_desc = []
        stripped = line.strip()
        if not stripped:
            continue
        # "repo/package" or "repo package"
        if "/" in stripped:
            repo, name = stripped.split("/", 1)
            current_repo = repo.strip()
            current_name = name.strip().split()[0] if name.strip() else name.strip()
        else:
            parts = stripped.split(None, 1)
            current_repo = parts[0] if parts else ""
            current_name = parts[0] if parts else ""
    if current_name is not None and current_desc:
        results.append({
            "manager": manager_name,
            "bin": bin_path,
            "name": current_name,
            "repo": current_repo or "",
            "description": " ".join(current_desc).strip() or "(no description)",
            "source": current_repo or "repo",
        })
    return results


def _parse_yum_search(stdout: str, manager_name: str, bin_path: str, query: str) -> list[dict]:
    """Parse yum search output (name and summary)."""
    results = []
    # Typical: "Name : pkgname" and "Summary : ..."
    name = None
    summary = []
    for line in stdout.splitlines():
        if line.startswith("Name ") or line.startswith("Name:"):
            if name and summary:
                results.append({
                    "manager": manager_name,
                    "bin": bin_path,
                    "name": name,
                    "repo": "",
                    "description": " ".join(summary).strip() or "(no description)",
                    "source": "yum",
                })
            name = line.split(":", 1)[-1].strip()
            summary = []
        elif line.startswith("Summary ") or line.startswith("Summary:"):
            summary.append(line.split(":", 1)[-1].strip())
    if name and summary:
        results.append({
            "manager": manager_name,
            "bin": bin_path,
            "name": name,
            "repo": "",
            "description": " ".join(summary).strip() or "(no description)",
            "source": "yum",
        })
    return results


def get_search_results(managers: list[dict], query: str) -> list[dict]:
    """Search all managers for query and return unified list of matches."""
    all_results = []
    for m in managers:
        name, bin_path = m["name"], m["bin"]
        if name == "fisher":
            # Fisher: treat query as plugin name (e.g. jorgebucaran/fisher)
            all_results.append({
                "manager": "fisher",
                "bin": bin_path,
                "name": query,
                "repo": "",
                "description": "Fish shell plugin (fisher)",
                "source": "fisher",
            })
            continue
        if name in ("pacman", "paru", "yay"):
            cmd = [bin_path] + m["search_cmd"] + [query]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if out.returncode == 0 and out.stdout:
                    all_results.extend(_parse_pacman_like(out.stdout, name, bin_path))
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        elif name == "yum":
            cmd = [bin_path] + m["search_cmd"] + [query]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if out.returncode == 0 and out.stdout:
                    all_results.extend(_parse_yum_search(out.stdout, name, bin_path, query))
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    # Dedupe by (manager, name)
    seen = set()
    ordered = []
    query_lower = query.lower()
    for r in all_results:
        key = (r["manager"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(r)

    # Sort so exact matches from ANY manager come first, then partial matches.
    # Tier 0: exact name match (cursor == cursor)
    # Tier 1: name starts with query (cursor-bin, cursor-theme)
    # Tier 2: query in name (xcursor, breeze-cursors)
    # Tier 3: other (query only in description)
    # Within same tier, prefer pacman then paru then yay then yum then fisher.
    manager_order = {"pacman": 0, "paru": 1, "yay": 2, "yum": 3, "fisher": 4}

    def sort_key(x: dict) -> tuple:
        name_lower = x["name"].lower()
        if name_lower == query_lower:
            tier = 0
        elif name_lower.startswith(query_lower + "-") or name_lower.startswith(query_lower + "_"):
            tier = 1
        elif query_lower in name_lower:
            tier = 2
        else:
            tier = 3
        return (tier, manager_order.get(x["manager"], 99), name_lower)

    ordered.sort(key=sort_key)
    return ordered


def _looks_like_permission_error(stderr: str, stdout: str) -> bool:
    """True if combined output suggests a root/permission denied error."""
    text = (stderr + "\n" + stdout).lower()
    keywords = (
        "root",
        "permission denied",
        "access denied",
        "no permission",
        "not permitted",
        "operation not permitted",
        "eacces",
        "eperm",
        "cannot create",
        "failed to create",
        "unable to create",
        "must be run as root",
        "run as root",
        "requires root",
        "need root",
        "privileges",
    )
    return any(k in text for k in keywords)


def _stream_output(pipe, stream, buffer: list, is_stderr: bool):
    """Read from pipe, write to stream, append to buffer. Runs in thread."""
    try:
        for line in iter(pipe.readline, ""):
            if line:
                stream.write(line)
                stream.flush()
                buffer.append(line)
    finally:
        pipe.close()


def _run_install_cmd(cmd: list, use_sudo: bool, stream: bool = True) -> tuple[int, str]:
    """Run install command, optionally with sudo.
    When stream is True, stdout/stderr are shown in real time; output is still
    buffered for permission-error detection. Returns (returncode, combined_output).
    """
    run_cmd = ["sudo", "--"] + cmd if use_sudo else cmd
    if not stream:
        result = subprocess.run(run_cmd, capture_output=True, text=True)
        out = (result.stdout or "") + "\n" + (result.stderr or "")
        return (result.returncode, out)
    buffer: list[str] = []
    proc = subprocess.Popen(
        run_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    t1 = threading.Thread(target=_stream_output, args=(proc.stdout, sys.stdout, buffer, False))
    t2 = threading.Thread(target=_stream_output, args=(proc.stderr, sys.stderr, buffer, True))
    t1.daemon = True
    t2.daemon = True
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    proc.wait()
    return (proc.returncode, "".join(buffer))


def install_package(managers: list[dict], choice: dict) -> bool:
    """Run the install command for the chosen result.
    pacman is run with sudo; AUR helpers (paru, yay) run as user and prompt as needed.
    On permission-style errors, prompts to retry with sudo.
    """
    manager_name = choice["manager"]
    bin_path = choice["bin"]
    pkg_name = choice["name"]
    if manager_name == "git":
        cmd = [bin_path, "clone", pkg_name]
        print(f"Running: {' '.join(cmd)}")
        returncode, output = _run_install_cmd(cmd, use_sudo=False)
        if returncode != 0 and _looks_like_permission_error(output, ""):
            try:
                ans = input("Retry with sudo? [y/N] ").strip().lower()
                if ans in ("y", "yes"):
                    print(f"Running: sudo -- {' '.join(cmd)}")
                    returncode, _ = _run_install_cmd(cmd, use_sudo=True)
            except (EOFError, KeyboardInterrupt):
                pass
        return returncode == 0
    m = next((x for x in managers if x["name"] == manager_name), None)
    if not m:
        print(f"Unknown manager: {manager_name}")
        return False
    if manager_name == "fisher":
        cmd = [bin_path, "add", pkg_name]
        use_sudo = False
    else:
        cmd = [bin_path] + m["install_cmd"] + [pkg_name]
        use_sudo = manager_name == "pacman"
    print(f"Running: {' '.join(['sudo', '--'] + cmd if use_sudo else cmd)}")
    returncode, output = _run_install_cmd(cmd, use_sudo=use_sudo)
    if returncode != 0 and _looks_like_permission_error(output, "") and not use_sudo:
        try:
            ans = input("Retry with sudo? [y/N] ").strip().lower()
            if ans in ("y", "yes"):
                print(f"Running: sudo -- {' '.join(cmd)}")
                returncode, _ = _run_install_cmd(cmd, use_sudo=True)
        except (EOFError, KeyboardInterrupt):
            pass
    return returncode == 0
