"""
Post-install actions: add CLI to PATH, add GUI to desktop.
Post-uninstall: remove desktop shortcuts, offer to remove leftover user files.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def get_installed_files(manager_name: str, pkg_name: str) -> list[str]:
    """Return list of installed file paths for a package (pacman/paru/yay)."""
    if manager_name not in ("pacman", "paru", "yay"):
        return []
    try:
        # pacman -Ql lists files for installed package; works with paru/yay too
        out = subprocess.run(
            ["pacman", "-Ql", pkg_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode != 0:
            return []
        paths = []
        for line in out.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) >= 2 and parts[0] == pkg_name:
                paths.append(parts[1].strip())
        return paths
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def get_bin_dirs(files: list[str]) -> set[str]:
    """Return directories that look like binary dirs (contain executables)."""
    bin_dirs = set()
    for path in files:
        if not path or path.endswith("/"):
            continue
        name = os.path.basename(path.rstrip("/"))
        if not name or "/" not in path:
            continue
        parent = os.path.dirname(path)
        # Standard locations for binaries
        if parent in ("/usr/bin", "/usr/local/bin") or parent.endswith("/bin"):
            if not name.startswith(".") and " " not in name:
                bin_dirs.add(parent)
        if parent.startswith("/opt/") and "/bin" in path:
            idx = path.find("/bin")
            bin_dirs.add(path[: idx + 4])
    return bin_dirs


def get_desktop_files(files: list[str]) -> list[str]:
    """Return .desktop files under /usr/share/applications (or similar)."""
    out = []
    for path in files:
        if path.endswith(".desktop") and (
            "/share/applications/" in path or path.startswith("/usr/share/applications/")
        ):
            if os.path.isabs(path) and os.path.exists(path):
                out.append(path)
    return out


def get_path_env() -> str:
    return os.environ.get("PATH", "")


def is_dir_in_path(dir_path: str, path_env: Optional[str] = None) -> bool:
    if path_env is None:
        path_env = get_path_env()
    dir_path = os.path.normpath(dir_path)
    for p in path_env.split(os.pathsep):
        if os.path.normpath(p) == dir_path:
            return True
    return False


def get_shell_config_path() -> Optional[tuple[str, str]]:
    """Return (path_to_config_file, line_to_append) for current $SHELL, or None."""
    shell = os.environ.get("SHELL", "").lower()
    home = os.path.expanduser("~")
    if "fish" in shell:
        # fish: set -gx PATH $PATH /new/path
        config = os.path.join(home, ".config", "fish", "config.fish")
        return (config, 'set -gx PATH $PATH "{path}"')
    if "zsh" in shell:
        config = os.path.join(home, ".zshrc")
        return (config, 'export PATH="$PATH:{path}"')
    if "bash" in shell or not shell:
        config = os.path.join(home, ".bashrc")
        return (config, 'export PATH="$PATH:{path}"')
    # generic
    config = os.path.join(home, ".profile")
    return (config, 'export PATH="$PATH:{path}"')


def add_dir_to_shell_path(dir_path: str) -> bool:
    """Append dir_path to PATH in the user's shell config. Returns True if written."""
    cfg = get_shell_config_path()
    if not cfg:
        return False
    config_path, line_tpl = cfg
    line = line_tpl.format(path=dir_path)
    try:
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "a") as f:
            f.write(f"\n# Added by ecpi\n{line}\n")
        return True
    except OSError:
        return False


def copy_desktop_to_desktop(desktop_path: str) -> bool:
    """Copy a .desktop file to ~/Desktop (or XDG desktop dir). Returns True on success."""
    home = os.path.expanduser("~")
    desktop_dir = os.environ.get("XDG_DESKTOP_DIR", os.path.join(home, "Desktop"))
    if not os.path.isabs(desktop_dir):
        desktop_dir = os.path.join(home, desktop_dir)
    try:
        Path(desktop_dir).mkdir(parents=True, exist_ok=True)
        dest = os.path.join(desktop_dir, os.path.basename(desktop_path))
        shutil.copy2(desktop_path, dest)
        return True
    except OSError:
        return False


def offer_post_install_actions(
    manager_name: str,
    pkg_name: str,
    skip_confirm: bool = False,
) -> None:
    """
    After a successful install, check if package is CLI or GUI and offer
    to add to PATH or add to desktop. Only for pacman/paru/yay.
    """
    if manager_name not in ("pacman", "paru", "yay"):
        return
    files = get_installed_files(manager_name, pkg_name)
    if not files:
        return
    path_env = get_path_env()
    bin_dirs = get_bin_dirs(files)
    desktop_files = get_desktop_files(files)
    dirs_not_in_path = [d for d in sorted(bin_dirs) if not is_dir_in_path(d, path_env)]

    # Offer add to PATH for CLI (we have bin dirs not in PATH)
    if dirs_not_in_path:
        print("\nThis package added command-line binaries.")
        for d in dirs_not_in_path:
            print(f"  Binary directory: {d}")
        if not skip_confirm:
            try:
                ans = input("Add these to your shell PATH? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                pass
            else:
                if ans in ("y", "yes"):
                    cfg = get_shell_config_path()
                    if cfg:
                        for d in dirs_not_in_path:
                            if add_dir_to_shell_path(d):
                                print(f"  Added {d} to {cfg[0]}. Restart the shell or run: source {cfg[0]}")
                    else:
                        print("  Could not detect shell config file.")

    # Offer add to desktop for GUI
    if desktop_files:
        # Prefer a single main .desktop (e.g. one matching pkg name)
        main_desktop = desktop_files[0]
        for d in desktop_files:
            if pkg_name.lower() in os.path.basename(d).lower():
                main_desktop = d
                break
        print("\nThis package provides a desktop application.")
        if not skip_confirm:
            try:
                ans = input("Add shortcut to desktop? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if ans in ("y", "yes"):
                if copy_desktop_to_desktop(main_desktop):
                    desktop_dir = os.environ.get("XDG_DESKTOP_DIR", os.path.join(os.path.expanduser("~"), "Desktop"))
                    print(f"  Shortcut added to {desktop_dir}.")
                else:
                    print("  Failed to copy shortcut.")


def remove_desktop_shortcuts_for_package(manager_name: str, pkg_name: str) -> int:
    """
    Find desktop shortcuts that were added for this package (by name) and remove them.
    Returns the number of shortcuts removed.
    """
    if manager_name not in ("pacman", "paru", "yay"):
        return 0
    files = get_installed_files(manager_name, pkg_name)
    desktop_files = get_desktop_files(files)
    if not desktop_files:
        return 0
    home = os.path.expanduser("~")
    desktop_dir = os.environ.get("XDG_DESKTOP_DIR", os.path.join(home, "Desktop"))
    if not os.path.isabs(desktop_dir):
        desktop_dir = os.path.join(home, desktop_dir)
    removed = 0
    for sys_desktop in desktop_files:
        basename = os.path.basename(sys_desktop)
        shortcut = os.path.join(desktop_dir, basename)
        if os.path.isfile(shortcut):
            try:
                os.remove(shortcut)
                print(f"  Removed desktop shortcut: {shortcut}")
                removed += 1
            except OSError as e:
                print(f"  Could not remove {shortcut}: {e}")
    return removed


def _leftover_candidates(pkg_name: str) -> list[tuple[str, str]]:
    """Return list of (path, label) for common leftover config/data dirs for a package."""
    home = os.path.expanduser("~")
    # Normalize: firefox-esr -> firefox-esr, firefox_esr; try both
    base = pkg_name.replace("_", "-")
    base_underscore = pkg_name.replace("-", "_")
    candidates = []
    for name in (base, base_underscore, pkg_name):
        if not name:
            continue
        for sub, label in (
            (".config/" + name, "config"),
            (".local/share/" + name, "data"),
            (".cache/" + name, "cache"),
            ("." + name, "dotdir"),
        ):
            path = os.path.join(home, sub)
            if os.path.exists(path) and path not in [p[0] for p in candidates]:
                candidates.append((path, label))
    return candidates


def offer_remove_leftover_files(pkg_name: str, skip_confirm: bool = False) -> None:
    """
    List common leftover user dirs for the package and prompt to remove them.
    """
    candidates = _leftover_candidates(pkg_name)
    if not candidates:
        return
    print("\nLeftover user files for this package may exist:")
    for path, label in candidates:
        print(f"  {path} ({label})")
    if skip_confirm:
        return
    try:
        ans = input("Remove these leftover files/directories? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans not in ("y", "yes"):
        return
    for path, _ in candidates:
        if not os.path.exists(path):
            continue
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
                print(f"  Removed: {path}")
            else:
                os.remove(path)
                print(f"  Removed: {path}")
        except OSError as e:
            print(f"  Could not remove {path}: {e}")
