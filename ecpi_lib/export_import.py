"""
Export/import helper for ecpi.

Export format v1 (simple requirements.txt-like manifest):

  # ecpi-export v1
  # generated_at=2026-01-01T12:34:56Z
  pacman <name> <version>
  aur <name> <version>
  yum <name> <version>
  fisher <plugin>

Notes:
- "aur" is based on pacman foreign packages (-Qm): packages not in official repos.
- Import is supported by parsing this manifest; installing is handled by ecpi.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
from typing import Any, Optional


def _run_capture(cmd: list[str], timeout: int = 60) -> Optional[str]:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    # Some tools (including plugin managers) may return non-zero even when
    # useful info is printed. Prefer stdout, but fall back to stderr.
    stdout = out.stdout or ""
    stderr = out.stderr or ""
    if stdout.strip():
        return stdout
    if stderr.strip():
        return stderr
    return None


def _parse_pacman_Q(output: str) -> dict[str, str]:
    """Parse `pacman -Q` into {name: version}."""
    pkgs: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # name version
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        name, version = parts[0].strip(), parts[1].strip()
        if name:
            pkgs[name] = version
    return pkgs


def _parse_pacman_Qm(output: str) -> dict[str, str]:
    """Parse `pacman -Qm` (foreign packages) into {name: version}."""
    return _parse_pacman_Q(output)


def _parse_yum_list_installed(output: str) -> list[tuple[str, str]]:
    """Parse `yum list installed` into [(name, version), ...]."""
    out: list[tuple[str, str]] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip headers and junk
        if line.lower().startswith("installed packages") or line.lower().startswith("loaded plugins"):
            continue
        # Typical line:
        # name.arch   version   repo
        # sometimes extra columns exist; we only need name+version
        parts = line.split()
        if len(parts) < 2:
            continue
        name_arch = parts[0]
        version = parts[1]
        name = name_arch.split(".", 1)[0] if "." in name_arch else name_arch
        if name and version:
            out.append((name, version))
    return out


def _parse_fisher_list(output: str) -> list[str]:
    """Parse `fisher list` into [plugin, ...]."""
    candidates: list[str] = []
    for line in output.splitlines():
        s = line.strip()
        if not s:
            continue
        # Typical fisher plugin identifier looks like "user/repo".
        # Ignore obvious non-plugin lines.
        if "/" in s and not s.lower().startswith(("installed", "no ", "fisher")):
            candidates.append(s)

    # If heuristic finds nothing, fall back to all non-empty lines.
    if not candidates:
        for line in output.splitlines():
            s = line.strip()
            if s:
                candidates.append(s)

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        deduped.append(p)
    return deduped


def export_installed_packages(
    managers: list[dict[str, Any]],
    output_path: str,
) -> bool:
    """
    Export installed packages to a proprietary ecpi manifest.

    Returns True on success.
    """
    output_path = os.path.expanduser(output_path)
    parent = os.path.dirname(output_path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError:
        pass

    pacman_bin = next((m["bin"] for m in managers if m.get("name") == "pacman"), None) or shutil.which("pacman")
    fisher_bin = next((m["bin"] for m in managers if m.get("name") == "fisher"), None) or shutil.which("fisher")
    yum_bin = next((m["bin"] for m in managers if m.get("name") == "yum"), None) or shutil.which("yum")
    fish_bin = shutil.which("fish")

    pacman_pkgs: dict[str, str] = {}
    pacman_foreign: dict[str, str] = {}
    if pacman_bin:
        pacman_out = _run_capture([pacman_bin, "-Q"])
        if pacman_out:
            pacman_pkgs = _parse_pacman_Q(pacman_out)
        foreign_out = _run_capture([pacman_bin, "-Qm"])
        if foreign_out:
            pacman_foreign = _parse_pacman_Qm(foreign_out)

    yum_pkgs: list[tuple[str, str]] = []
    if yum_bin:
        yum_out = _run_capture([yum_bin, "list", "installed"], timeout=90)
        if yum_out:
            yum_pkgs = _parse_yum_list_installed(yum_out)

    fisher_plugins: list[str] = []
    if fisher_bin:
        fisher_out = _run_capture([fisher_bin, "list"], timeout=30)
        if fisher_out:
            fisher_plugins = _parse_fisher_list(fisher_out)
    elif fish_bin:
        # fisher is often installed as a fish function, not an external binary.
        fisher_out = _run_capture([fish_bin, "-ic", "fisher list"], timeout=30)
        if fisher_out:
            fisher_plugins = _parse_fisher_list(fisher_out)

    if not pacman_pkgs and not yum_pkgs and not fisher_plugins:
        return False

    generated_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = []
    lines.append("# ecpi-export v1")
    lines.append(f"# generated_at={generated_at}")

    # Export pacman vs aur (foreign)
    foreign_names = set(pacman_foreign.keys())
    pacman_export = []
    aur_export = []
    for name, version in pacman_pkgs.items():
        if name in foreign_names:
            aur_export.append((name, pacman_foreign.get(name, version)))
        else:
            pacman_export.append((name, version))
    pacman_export.sort(key=lambda x: x[0].lower())
    aur_export.sort(key=lambda x: x[0].lower())

    for name, version in pacman_export:
        lines.append(f"pacman {name} {version}")
    for name, version in aur_export:
        lines.append(f"aur {name} {version}")

    # Export yum
    yum_pkgs_sorted = sorted(set(yum_pkgs), key=lambda x: x[0].lower())
    for name, version in yum_pkgs_sorted:
        lines.append(f"yum {name} {version}")

    # Export fisher
    for plugin in sorted(set(fisher_plugins), key=lambda x: x.lower()):
        lines.append(f"fisher {plugin}")

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        return False

    print(f"Exported installed packages to: {output_path}")
    return True


def parse_export_manifest(manifest_path: str) -> list[dict[str, str]]:
    """
    Parse an ecpi-export manifest into a list of install requests.

    Returns a list of dicts with keys:
      - kind: one of {'pacman', 'aur', 'yum', 'fisher'}
      - name: package name (or plugin id for fisher)
      - version: optional version string (empty if not present)
    """
    manifest_path = os.path.expanduser(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        raw_lines = f.read().splitlines()

    entries: list[dict[str, str]] = []
    for line in raw_lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if not parts:
            continue
        kind = parts[0].lower()
        if kind in ("pacman", "aur", "yum"):
            if len(parts) < 2:
                continue
            name = parts[1].strip()
            version = parts[2].strip() if len(parts) >= 3 else ""
            entries.append({"kind": kind, "name": name, "version": version})
        elif kind == "fisher":
            if len(parts) < 2:
                continue
            plugin = parts[1].strip()
            entries.append({"kind": "fisher", "name": plugin, "version": ""})
        else:
            # Unknown entry; ignore for forward compatibility
            continue

    return entries

