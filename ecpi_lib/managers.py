"""
Detect available package managers and perform search/install.
"""

import os
import subprocess
import shutil
import sys
import threading
import time
from typing import Any, Optional

# When install output is quiet for this many seconds but process still running, show a message
INSTALL_QUIET_THRESHOLD_SEC = 45
# After showing "still running", don't show again for this many seconds (repeat if still quiet)
INSTALL_STILL_RUNNING_INTERVAL_SEC = 55


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


# (name, binary, search_cmd, install_cmd, uninstall_cmd, list_all_cmd, query_installed_cmd)
# list_all_cmd used for fuzzy suggestions when search returns nothing
# query_installed_cmd used for uninstall to find installed packages matching a query
MANAGER_CONFIG = [
    {
        "name": "pacman",
        "bin": "pacman",
        "search_cmd": ["-Ss"],
        "install_cmd": ["-S", "--noconfirm"],
        "uninstall_cmd": ["-Rns"],
        "list_all_cmd": ["-Slq"],
        "query_installed_cmd": ["-Qs"],
        "sources": ["official repos"],
    },
    {
        "name": "paru",
        "bin": "paru",
        "search_cmd": ["-Ss"],
        "install_cmd": ["-S", "--noconfirm"],
        "uninstall_cmd": ["-Rns"],
        "list_all_cmd": ["-Ssq"],
        "query_installed_cmd": ["-Qs"],
        "sources": ["official repos", "AUR"],
    },
    {
        "name": "yay",
        "bin": "yay",
        "search_cmd": ["-Ss"],
        "install_cmd": ["-S", "--noconfirm"],
        "uninstall_cmd": ["-Rns"],
        "list_all_cmd": ["-Ssq"],
        "query_installed_cmd": ["-Qs"],
        "sources": ["official repos", "AUR"],
    },
    {
        "name": "yum",
        "bin": "yum",
        "search_cmd": ["search"],
        "install_cmd": ["install", "-y"],
        "uninstall_cmd": ["remove", "-y"],
        "list_all_cmd": ["list", "all"],
        "query_installed_cmd": ["list", "installed"],
        "sources": ["RHEL/Fedora repos"],
    },
    {
        "name": "fisher",
        "bin": "fisher",
        "search_cmd": [],
        "install_cmd": ["add"],
        "uninstall_cmd": ["remove"],
        "list_all_cmd": [],
        "query_installed_cmd": ["list"],
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
            "uninstall_cmd": cfg.get("uninstall_cmd", []),
            "list_all_cmd": cfg.get("list_all_cmd", []),
            "query_installed_cmd": cfg.get("query_installed_cmd", []),
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


def _parse_yum_list_installed(stdout: str, manager_name: str, bin_path: str, query: str) -> list[dict]:
    """Parse 'yum list installed' output; filter by query (case-insensitive substring)."""
    results = []
    query_lower = query.lower()
    for line in stdout.splitlines():
        # Format: "name.version  repo" or "name.version"
        parts = line.split()
        if not parts:
            continue
        name_ver = parts[0]
        if "." in name_ver:
            name = name_ver.rsplit(".", 1)[0]
        else:
            name = name_ver
        if query_lower not in name.lower():
            continue
        repo = parts[1] if len(parts) >= 2 else "installed"
        results.append({
            "manager": manager_name,
            "bin": bin_path,
            "name": name,
            "repo": repo,
            "description": f"Installed ({repo})",
            "source": repo,
        })
    return results


def get_installed_results(managers: list[dict], query: str) -> list[dict]:
    """Search installed packages across managers; return list of matches (for uninstall)."""
    all_results = []
    for m in managers:
        name, bin_path = m["name"], m["bin"]
        qicmd = m.get("query_installed_cmd") or []
        if not qicmd:
            continue
        if name in ("pacman", "paru", "yay"):
            cmd = [bin_path] + qicmd + [query]
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if out.returncode == 0 and out.stdout:
                    all_results.extend(_parse_pacman_like(out.stdout, name, bin_path))
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        elif name == "yum":
            # yum list installed [glob] - filter by query
            cmd = [bin_path] + qicmd
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                if out.returncode == 0 and out.stdout:
                    all_results.extend(_parse_yum_list_installed(out.stdout, name, bin_path, query))
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        elif name == "fisher":
            cmd = [bin_path] + qicmd
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if out.returncode != 0 or not out.stdout:
                    continue
                query_lower = query.lower()
                for line in out.stdout.splitlines():
                    pkg = line.strip()
                    if not pkg or query_lower not in pkg.lower():
                        continue
                    all_results.append({
                        "manager": "fisher",
                        "bin": bin_path,
                        "name": pkg,
                        "repo": "",
                        "description": "Fish shell plugin (installed via fisher)",
                        "source": "fisher",
                    })
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

    # Sort: exact name match first, then partial
    manager_order = {"pacman": 0, "paru": 1, "yay": 2, "yum": 3, "fisher": 4}

    def sort_key(x: dict) -> tuple:
        name_lower = x["name"].lower()
        if name_lower == query_lower:
            tier = 0
        elif query_lower in name_lower:
            tier = 1
        else:
            tier = 2
        return (tier, manager_order.get(x["manager"], 99), name_lower)

    ordered.sort(key=sort_key)
    return ordered


def get_installed_names_for_fuzzy(managers: list[dict]) -> list[tuple[str, dict]]:
    """Return list of (pkg_name, result_dict) for all installed packages (for fuzzy uninstall).
    Dedupes by package name (first manager wins) so we don't list the same pkg twice.
    """
    seen_names: set[str] = set()
    results = []
    for m in managers:
        name, bin_path = m["name"], m["bin"]
        if name in ("pacman", "paru", "yay"):
            try:
                out = subprocess.run(
                    [bin_path, "-Qq"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if out.returncode != 0 or not out.stdout:
                    continue
                for pkg in out.stdout.splitlines():
                    pkg = pkg.strip()
                    if not pkg or pkg in seen_names:
                        continue
                    seen_names.add(pkg)
                    results.append((pkg, {
                        "manager": name,
                        "bin": bin_path,
                        "name": pkg,
                        "repo": "local",
                        "description": "Installed package",
                        "source": "local",
                    }))
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        elif name == "yum":
            try:
                out = subprocess.run(
                    [bin_path, "list", "installed"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if out.returncode != 0 or not out.stdout:
                    continue
                for line in out.stdout.splitlines():
                    parts = line.split()
                    if not parts:
                        continue
                    pkg = parts[0].rsplit(".", 1)[0] if "." in parts[0] else parts[0]
                    if pkg and pkg not in seen_names:
                        seen_names.add(pkg)
                        results.append((pkg, {
                            "manager": name,
                            "bin": bin_path,
                            "name": pkg,
                            "repo": "installed",
                            "description": "Installed (yum)",
                            "source": "installed",
                        }))
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        elif name == "fisher":
            try:
                out = subprocess.run([bin_path, "list"], capture_output=True, text=True, timeout=10)
                if out.returncode != 0 or not out.stdout:
                    continue
                for pkg in out.stdout.splitlines():
                    pkg = pkg.strip()
                    if pkg and pkg not in seen_names:
                        seen_names.add(pkg)
                        results.append((pkg, {
                            "manager": "fisher",
                            "bin": bin_path,
                            "name": pkg,
                            "repo": "",
                            "description": "Fish plugin (fisher)",
                            "source": "fisher",
                        }))
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
    return results


def get_search_results(
    managers: list[dict],
    query: str,
    search_by_description: bool = False,
) -> list[dict]:
    """Search all managers for query and return unified list of matches.
    If search_by_description is True, AUR (paru/yay) uses --searchby name-desc for
    description-aware search, and results are sorted to prioritize description matches.
    """
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
            cmd = [bin_path] + list(m["search_cmd"])
            if search_by_description and name in ("paru", "yay"):
                cmd.extend(["--searchby", "name-desc"])
            cmd.append(query)
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

    # Sort: when search_by_description, prioritize description matches (broad/function search).
    # Otherwise: exact name, name prefix, query in name, then description-only.
    manager_order = {"pacman": 0, "paru": 1, "yay": 2, "yum": 3, "fisher": 4}

    def sort_key(x: dict) -> tuple:
        name_lower = x["name"].lower()
        desc = (x.get("description") or "").lower()
        desc_match = query_lower in desc
        name_exact = name_lower == query_lower
        name_prefix = name_lower.startswith(query_lower + "-") or name_lower.startswith(query_lower + "_")
        name_contains = query_lower in name_lower
        if search_by_description:
            # Broad search: description match first, then name match
            if desc_match and name_exact:
                tier = 0
            elif desc_match:
                tier = 1
            elif name_exact:
                tier = 2
            elif name_prefix or name_contains:
                tier = 3
            else:
                tier = 4
        else:
            if name_exact:
                tier = 0
            elif name_prefix:
                tier = 1
            elif name_contains:
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


def _stream_output(pipe, stream, buffer: list, is_stderr: bool, last_output_time: Optional[list] = None):
    """Read from pipe, write to stream, append to buffer. Runs in thread.
    If last_output_time is a list of one float, update it when we write data.
    """
    try:
        for line in iter(pipe.readline, ""):
            if line:
                if last_output_time is not None:
                    last_output_time[0] = time.monotonic()
                stream.write(line)
                stream.flush()
                buffer.append(line)
    finally:
        pipe.close()


def _run_install_cmd(cmd: list, use_sudo: bool, stream: bool = True) -> tuple[int, str]:
    """Run install command, optionally with sudo.
    When stream is True, stdout/stderr are shown in real time; output is still
    buffered for permission-error detection. If output goes quiet for a while
    but the process is still running, prints a "still running, be patient" message.
    Returns (returncode, combined_output).
    """
    run_cmd = ["sudo", "--"] + cmd if use_sudo else cmd
    if not stream:
        result = subprocess.run(run_cmd, capture_output=True, text=True)
        out = (result.stdout or "") + "\n" + (result.stderr or "")
        return (result.returncode, out)
    buffer: list[str] = []
    last_output_time: list = [time.monotonic()]
    proc = subprocess.Popen(
        run_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    t1 = threading.Thread(
        target=_stream_output,
        args=(proc.stdout, sys.stdout, buffer, False, last_output_time),
    )
    t2 = threading.Thread(
        target=_stream_output,
        args=(proc.stderr, sys.stderr, buffer, True, last_output_time),
    )
    t1.daemon = True
    t2.daemon = True
    t1.start()
    t2.start()

    last_notify_time = 0.0
    while True:
        ret = proc.poll()
        if ret is not None:
            break
        now = time.monotonic()
        quiet_for = now - last_output_time[0]
        if quiet_for >= INSTALL_QUIET_THRESHOLD_SEC:
            if now - last_notify_time >= INSTALL_STILL_RUNNING_INTERVAL_SEC:
                print("\n[ecpi] Installer still running (no output lately). Please be patient…\n", flush=True)
                last_notify_time = now
        time.sleep(1)

    t1.join(timeout=5)
    t2.join(timeout=5)
    return (proc.returncode, "".join(buffer))


def _build_install_cmd(manager: dict, pkg_name: str, verbose_install: bool) -> list[str]:
    """Build the install command list, optionally with extra verbose flags."""
    bin_path = manager["bin"]
    base_cmd = manager["install_cmd"]
    if not verbose_install:
        return [bin_path] + list(base_cmd) + [pkg_name]
    name = manager["name"]
    if name == "pacman":
        # -v after -S for verbose sync output
        cmd = [bin_path, "-S", "-v", "--noconfirm", pkg_name]
        return cmd
    if name in ("paru", "yay"):
        # AUR: no standard makepkg stdout-verbose flag; keep default
        return [bin_path] + list(base_cmd) + [pkg_name]
    if name == "yum":
        return [bin_path, "install", "-y", "-v", pkg_name]
    return [bin_path] + list(base_cmd) + [pkg_name]


def install_package(managers: list[dict], choice: dict, verbose_install: bool = False) -> bool:
    """Run the install command for the chosen result.
    pacman is run with sudo; AUR helpers (paru, yay) run as user and prompt as needed.
    On permission-style errors, prompts to retry with sudo.
    If verbose_install is True (or ECPI_VERBOSE=1), adds -v for pacman/yum to reduce quiet periods.
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
        cmd = _build_install_cmd(m, pkg_name, verbose_install)
        use_sudo = manager_name == "pacman"
    if verbose_install and manager_name in ("pacman", "yum"):
        print("[ecpi] Verbose install enabled (more output during long installs).")
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


def uninstall_package(managers: list[dict], choice: dict) -> bool:
    """Run the uninstall command for the chosen installed package.
    Logs verbosely like install; prompts to retry with sudo on permission errors.
    """
    manager_name = choice["manager"]
    bin_path = choice["bin"]
    pkg_name = choice["name"]
    m = next((x for x in managers if x["name"] == manager_name), None)
    if not m:
        print(f"Unknown manager: {manager_name}")
        return False
    uninstall_cmd = m.get("uninstall_cmd") or []
    if not uninstall_cmd:
        print(f"Uninstall not supported for manager: {manager_name}")
        return False
    if manager_name == "git":
        print("Uninstall for git clones is not supported; remove the repo directory manually.")
        return False
    cmd = [bin_path] + uninstall_cmd + [pkg_name]
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
