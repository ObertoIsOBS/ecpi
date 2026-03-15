"""
Detect available package managers and perform search/install.
"""

import subprocess
import shutil
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
        "search_cmd": [],  # fisher doesn't have a standard search; we treat name as plugin identifier
        "install_cmd": ["add"],
        "list_all_cmd": [],
        "sources": ["fish plugin registry"],
    },
]


def detect_managers() -> list[dict[str, Any]]:
    """Return list of available package managers (path and config)."""
    found = []
    for cfg in MANAGER_CONFIG:
        path = shutil.which(cfg["bin"])
        if path:
            found.append({
                "name": cfg["name"],
                "bin": path if path else cfg["bin"],
                "search_cmd": cfg["search_cmd"],
                "install_cmd": cfg["install_cmd"],
                "list_all_cmd": cfg.get("list_all_cmd", []),
                "sources": cfg.get("sources", []),
            })
    return found


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

    # Prefer exact name matches and dedupe by (manager, name)
    seen = set()
    ordered = []
    query_lower = query.lower()
    for r in all_results:
        key = (r["manager"], r["name"])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(r)
    # Sort: exact match first, then by manager preference (pacman > paru > yay > yum > fisher)
    order = {"pacman": 0, "paru": 1, "yay": 2, "yum": 3, "fisher": 4}
    ordered.sort(key=lambda x: (x["name"].lower() != query_lower, order.get(x["manager"], 99)))
    return ordered


def install_package(managers: list[dict], choice: dict) -> bool:
    """Run the install command for the chosen result."""
    manager_name = choice["manager"]
    bin_path = choice["bin"]
    pkg_name = choice["name"]
    if manager_name == "git":
        cmd = [bin_path, "clone", pkg_name]
        print(f"Running: {' '.join(cmd)}")
        return subprocess.run(cmd).returncode == 0
    m = next((x for x in managers if x["name"] == manager_name), None)
    if not m:
        print(f"Unknown manager: {manager_name}")
        return False
    if manager_name == "fisher":
        cmd = [bin_path, "add", pkg_name]
    else:
        cmd = [bin_path] + m["install_cmd"] + [pkg_name]
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode == 0
