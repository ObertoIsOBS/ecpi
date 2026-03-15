# ecpi — Easy Cross-Package Installer

A small Arch Linux helper that picks the right package manager for you, searches across **pacman**, **paru**, **yay**, **yum**, **fisher**, and **git clone**, suggests similar packages when there’s no exact match, and asks for confirmation before running any install.

## Features

- **Auto-detect** package managers: uses whatever is installed (pacman, paru, yay, yum, fisher, git).
- **Unified search**: one query searches official repos and, if available, AUR (via paru/yay).
- **Fuzzy suggestions**: if nothing matches, suggests similar package names (e.g. `vimx` → vim, vimb, vifm, gvim).
- **Clear results**: shows source (repo/AUR) and short description for each match.
- **Confirmation**: explains what will be installed and asks before running the install command.
- **Pagination**: search results show 10 at a time; use **n** (next), **p** (prev), **q** (quit). When installing, type the result number to select.
- **Git fallback**: if the query looks like a repo (URL or `owner/repo`) and no package is found, offers to clone with `git clone`.

## Requirements

- Python 3.9+
- At least one of: **pacman**, **paru**, **yay**, **yum**, **fisher** (and **git** for the clone fallback).

## Installation

Run from the project directory, or link into `/usr/local/bin`:

```bash
# Run in place
./ecpi <package>

# Link into /usr/local/bin (installs a wrapper so ecpi finds ecpi_lib)
./link-ecpi.sh   # uses sudo

# Or copy manually (e.g. ~/.local/bin)
cp ecpi ~/.local/bin/ && cp -r ecpi_lib ~/.local/bin/
```

## Usage

```bash
ecpi <package|search term>   # Search and then optionally install
ecpi --search-only <term>    # Only search, do not install
ecpi -y <package>             # Install without confirmation (use with care)
ecpi --no-fuzzy <term>       # Disable similar-package suggestions
```

### Examples

```bash
ecpi vim              # Search for vim, show matches, ask to install
ecpi neovim           # Same with neovim
ecpi --search-only vim   # List all vim-related packages, no install
ecpi jorgebucaran/fisher  # If not a package, offer git clone
ecpi -y firefox       # Install firefox without prompting
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
