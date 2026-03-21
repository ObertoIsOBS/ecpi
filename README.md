# ecpi — Easy Cross-Package Installer

A small Arch Linux helper that picks the right package manager for you, searches across **pacman**, **paru**, **yay**, **yum**, **fisher**, and **git clone**, suggests similar packages when there’s no exact match. Manages whole system import and export among all installed pakcage managers.

**Repository:** [https://github.com/ObertoIsOBS/ecpi](https://github.com/ObertoIsOBS/ecpi)

## Features

- **Auto-detect** package managers: uses whatever is installed (pacman, paru, yay, yum, fisher, git).
- **Unified search**: one query searches official repos and, if available, AUR (via paru/yay).
- **Fuzzy suggestions**: if nothing matches, suggests similar package names (e.g. `vimx` → vim, vimb, vifm, gvim).
- **Clear results**: shows source (repo/AUR) and short description for each match.
- **Confirmation**: explains what will be installed and asks before running the install command.
- **Pagination**: search results show 10 at a time; use **n** (next), **p** (prev), **q** (quit). When installing, type the result number to select.
- **Git fallback**: if the query looks like a repo (URL or `owner/repo`) and no package is found, offers to clone with `git clone`.
- **Environment-aware installers**: shell-specific managers (e.g. **fisher** for Fish) are only offered when they match your `$SHELL`. Use `--show-installers` to see what’s available for the current environment.
- **Post-install**: after installing via pacman/paru/yay, ecpi can add CLI binaries to your shell PATH (if not already there) and offer to add a desktop shortcut for GUI apps.
- **Export Packages**: Creates a detailed export file of all packages installed in the enviornment.
- **Import Packages**: Installs packages from exported package list sequentially.

## Requirements

- Python 3.9+
- At least one of: **pacman**, **paru**, **yay**, **yum**, **fisher** (and **git** for the clone fallback).

## Installation

### Install via Git clone

Clone the repository and run `ecpi` from the clone directory, or install it system-wide:

```bash
# Clone the repo
git clone https://github.com/ObertoIsOBS/ecpi.git
cd ecpi

# Option A: Run in place (no install)
./ecpi <package>

# Option B: Install system-wide (sudo) — keeps repo in place, adds ecpi to PATH
./link-ecpi.sh
# Then run from anywhere: ecpi <package>

# Option C: Copy into your user PATH (no sudo)
mkdir -p ~/.local/bin
cp ecpi ~/.local/bin/ && cp -r ecpi_lib ~/.local/bin/
# Ensure ~/.local/bin is in your PATH, then run: ecpi <package>
```

**Note:** With Option B, the cloned directory must stay where it is; `link-ecpi.sh` installs a wrapper that runs `ecpi` from that directory. With Option C, `ecpi` and `ecpi_lib` must remain together in the same directory (e.g. `~/.local/bin`).

Quick try without installing (run from a temporary clone):

```bash
git clone https://github.com/ObertoIsOBS/ecpi.git /tmp/ecpi && /tmp/ecpi/ecpi --help
```

## Usage

```bash
ecpi <package|search term>   # Search and then optionally install
ecpi -U, --uninstall <pkg>   # Uninstall: search installed packages, remove shortcuts, offer leftover cleanup
ecpi --export <FILE>        # Export installed packages manifest
ecpi --import <FILE>       # Import manifest and install packages (from `--export`)
ecpi --pin-versions        # When importing, try installing pacman/yum entries with exported versions
ecpi --skip-existing       # When importing, skip packages already installed (faster)
ecpi --show-installers       # List available installers for current $SHELL
ecpi --search-only <term>    # Only search, do not install
ecpi -y <package>             # Install without confirmation (use with care)
ecpi -y -U <package>          # Uninstall without confirmation prompts
ecpi --verbose-install       # Extra verbose install (pacman -v, yum -v); helps when output goes quiet
ecpi -D, --by-description    # Search by function/description (broad search); works with --search-only
ecpi --no-fuzzy <term>       # Disable similar-package suggestions
```

**Search by description (`-D` / `--by-description`):** Broad search by what packages do, not just by name. Prioritizes matches in package descriptions and, for AUR (paru/yay), uses description-aware search. Use e.g. `ecpi -D --search-only "terminal emulator"` or `ecpi -D "text editor"` to find by function.

**Long installs:** If the installer produces no output for a while but is still running, ecpi prints `[ecpi] Installer still running (no output lately). Please be patient…` and repeats periodically. Use `--verbose-install` or `ECPI_VERBOSE=1` to get more output from pacman/yum and reduce quiet periods.

**Export (`ecpi --export <FILE>`):** Writes a manifest of *installed* packages in an ecpi-specific format (pacman vs foreign/AUR vs yum vs fisher). It is meant to be human-readable like `requirements.txt`, but with manager metadata so you can import/install with `ecpi --import`.

**Import (`ecpi --import <FILE>`):** Reads the manifest and installs packages with the best available installer:
`pacman` entries use `pacman`, `aur` entries use `paru` (or `yay` if `paru` isn’t available), `yum` entries use `yum`, and `fisher` entries use `fisher` (when available for your shell). If a needed manager is missing, those entries are skipped.

Use `--pin-versions` to try to install pacman/yum entries using the exported versions (best-effort). Versions for AUR/fisher are ignored.

`--skip-existing` (alias `--skip`) will skip packages that are already installed, regardless of the exported version, to speed up imports of large manifests.

Example:

```bash
ecpi --export ~/my-ecpi.packages
# copy ~/my-ecpi.packages to the other machine
ecpi -y --import ~/my-ecpi.packages
```

After a successful install, ecpi may prompt to add CLI binaries to your shell PATH (if the install path isn’t already in PATH) or to add a desktop shortcut for GUI applications.

**Uninstall (`ecpi -U <package>`):** Searches installed packages (pacman/paru/yay/yum/fisher), lets you choose if there are multiple matches, then runs the manager's uninstall command (with verbose output and optional sudo retry). It then removes any desktop shortcut that was added for the package and prompts to remove leftover user files (e.g. `~/.config/<pkg>`, `~/.local/share/<pkg>`). If no exact match is found, similar installed packages are suggested.

### Examples

```bash
ecpi vim              # Search for vim, show matches, ask to install
ecpi neovim           # Same with neovim
ecpi --search-only vim   # List all vim-related packages, no install
ecpi jorgebucaran/fisher  # If not a package, offer git clone
ecpi -y firefox       # Install firefox without prompting
ecpi -U firefox       # Uninstall firefox, remove shortcut, optionally remove leftover config
```

## How it works

1. **Detection**: Finds which of pacman, paru, yay, yum, and fisher are available.
2. **Search**: Runs search on each (e.g. `pacman -Ss`, `paru -Ss`) and merges results.
3. **Fuzzy**: If there are no matches, gets package names from the repo list and suggests close matches.
4. **Git**: If still no match and the query looks like a URL or `owner/repo`, offers `git clone`.
5. **Explain**: Prints matches with manager, source, and description.
6. **Confirm**: Asks you to choose (if multiple) and confirm before running the install command.

## License

Use and modify as you like.
