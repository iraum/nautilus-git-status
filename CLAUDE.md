# nautilus-git-status

A Nautilus Python extension that overlays a single live git emblem on
every git-repo folder in the file manager. The emblem is one layered
artwork: an outer disk encodes status with fixed priority
(dirty (orange) > behind (red) > ahead (green) > clean (white)) and a
small black-outlined inner dot encodes ownership tier (primary /
secondary / tertiary). The 'external' tier renders as the plain status
disk with no inner dot. The inner dot's outline switches to a bolder
crimson stroke for tiered repos with no remote configured, so
purely-local repos read at a glance. Tier comes from the user's
profile config at `~/.config/nautilus-git-status/profiles.conf`.
Exactly one emblem per repo; no stacking. Adds a "Git" submenu to the
right-click context menu (headline + full breakdown, including
identity) and a matching "Git" tab to the Properties dialog.

The companion project
[`nautilus-folder-icons`](https://github.com/iraum/nautilus-folder-icons)
puts a custom logo on a chosen folder; this extension's emblem
composites on top of whatever icon a folder has, so the two stack
without coordination.

Requires the `nautilus-python` package from EPEL.

## Layout

- `nautilus-git-status.py` — the Nautilus extension (Python 3, Nautilus
  3.0 API via gobject-introspection).
- `install.sh` — copies the extension to
  `~/.local/share/nautilus-python/extensions/`, copies the 28 emblem
  SVGs to `~/.local/share/icons/hicolor/scalable/emblems/`, refreshes
  the GTK icon cache, seeds `~/.config/nautilus-git-status/profiles.conf`
  on first run, and bounces Nautilus.
- `icons/generate.py` — source of truth for the 28 emblem SVGs.
- `icons/emblem-git-*.svg` — generated artwork, committed alongside
  the generator so the install path doesn't need Python at install
  time and so the SVGs are inspectable on the forge UI.
- `README.md` — user-facing install / usage / migration guide.

## Usage

```bash
sudo dnf install -y nautilus-python   # one-time; ol9_developer_EPEL
./install.sh
```

Open any parent folder in Nautilus to see the dots; right-click a repo
folder → Properties → Git for the rich view.

## Dependencies

- `nautilus-python` — Nautilus extension binding for Python via
  gobject-introspection. Lives in `ol9_developer_EPEL` on Oracle
  Linux 9, which ships disabled even after `oracle-epel-release-el9`
  is installed:
  ```bash
  sudo dnf install -y oracle-epel-release-el9
  sudo dnf config-manager --enable ol9_developer_EPEL
  sudo dnf install -y nautilus-python
  ```
- GTK 3 (for the Properties → Git tab) and Nautilus 3.0 extension
  API. Nautilus 40 on OL9 ships `libnautilus-extension` 3.0; this is
  what `gi.require_version('Nautilus', '3.0')` matches. On Nautilus
  43+ the API version is 4.0 — the extension would need an updated
  `require_version` and likely GTK 4 widgets there.

## Design choices worth preserving

- **`update_file_info()` is synchronous.** An earlier version pushed
  `git status` into a worker thread and relied on
  `invalidate_extension_info()` to make Nautilus re-call
  `update_file_info` once the cache was filled. In practice Nautilus
  didn't re-query reliably after that signal, so most folders never
  got their emblem applied (symptom: 2 of ~20 repos marked). Going
  synchronous removes the race. The cost is one short `git status`
  per visible repo on first render — tens of milliseconds each on a
  healthy repo — and every render after that is a cache hit.
- **Single emblem per repo, fixed priority.** dirty > behind > ahead
  > clean. The user explicitly chose color-only over multi-emblem
  stacks. Don't reintroduce stacking; it conflicts with that
  decision. The ownership tier was added by *layering* (a small
  black-outlined dot at the center of the status disk in the same
  SVG), not by adding a second emblem — this preserves the
  single-emblem rule. If another signal is wanted later, prefer
  extending the same emblem layer-wise over emitting a second
  emblem.
- **Ownership tiers: 4 (primary/secondary/tertiary/external) encoded
  as a small inner dot inside the status disk.** The status disk
  stays the dominant visual (its color reads as a ring around the
  inner dot); the inner dot identifies which profile owns the repo.
  The 'external' tier renders without an inner dot — a plain status
  disk signals "no ownership to declare", matching the
  pre-ownership look. The mapping from identifier to tier lives in
  `~/.config/nautilus-git-status/profiles.conf`, keyed by either
  GitHub owner slug, `user.name`, or `user.email` (lookup is
  case-insensitive). Detection priority is **origin URL owner >
  user.name > user.email**: a clone of someone else's repo is
  "external" regardless of which local profile committed to it.
  Origin is the source of truth for ownership; user.name/email only
  matter for purely-local repos that have no origin. The config is
  watched via `Gio.FileMonitor` (`monitor_file`, which fires whether
  or not the file exists) so edits repaint emblems within a fraction
  of a second.
- **No-remote signal: bolder crimson outline on the inner tier dot.**
  Computed from `git remote` (any remote, not just origin), so a
  repo with only an `upstream` remote still counts as "has a
  remote". When a tiered repo has no remotes at all, the inner
  dot's outline is swapped from soft near-black (`#1a1a1a`) to a
  bolder crimson (`#e11d48`) at 2.0px stroke. External repos don't
  participate — there's no inner dot to outline. This adds 12 SVG
  variants (4 statuses × 3 tiers, with `-noremote` filename suffix),
  bringing the total to 28 emblems. The signal also surfaces in the
  menu / Properties Identity row as `iraum (primary, no remote)`.
- **`icons/generate.py` is the source of truth for emblem artwork.**
  Edit colors / ring widths / dot radii there, run
  `python3 icons/generate.py` from the `icons/` directory, and the
  28 SVGs regenerate. The generated SVGs are committed alongside
  the generator so the install path doesn't require running Python
  at install time and so the artwork is inspectable on the forge
  UI. Do not hand-edit the SVGs — re-run the generator instead.
- **Emblem SVG filenames stay `emblem-git-*`, not
  `emblem-nautilus-git-status-*`.** They identify a *git emblem
  visual concept*, not the project; the longer name is uglier and
  forces churn through `generate.py`, the Python's `add_emblem()`
  calls, and the freedesktop icon cache.
- **`.git` as a file is supported** (worktrees and submodules — a
  text file with `gitdir: <path>`). The recognition check is
  `os.path.exists('.git')`, not `isdir`. The `Gio.FileMonitor`
  follows the `gitdir:` pointer when `.git` is a file so live
  updates still work for worktrees.
- **Emblem visual size is tuned by SVG content, not canvas.** All
  four dots are radius 10 inside a 64×64 viewBox (~31% of canvas).
  Nautilus scales the SVG to whatever pixel size the emblem cell
  needs — shrink or grow the *content* within the 64-canvas, never
  the canvas itself, so the rendered output stays sharp and the
  four dots remain visually identical in size.
- **Cache + monitor pair is per-repo path.** `_cache`, `_files`, and
  `_monitors` are dicts keyed by absolute folder path. Don't replace
  path keys with FileInfo refs — Nautilus FileInfo objects can be
  transient, but the path is stable, and we want monitor callbacks
  to find the latest live FileInfo for that path via `_files[path]`.
- **Properties → Git tab does its own git calls,** not the cached
  emblem result. The dialog is opened on-demand, so a fresh
  `git status` / `git log` / `git remote` is cheap and gives the
  most accurate snapshot. Don't try to share state with the emblem
  cache — different surfaces, different lifetimes.
- **Right-click menu also queries fresh.** `MenuProvider.get_file_items`
  runs the same `_gather_git_info` as the Properties tab. Menu
  generation happens at right-click rate (human-scale), so a single
  `git status` per click is cheap. Items are kept `sensitive=True`
  even though they're display-only — disabling them dims the text
  to the point that the headline becomes hard to read.
- **Internal Nautilus action / class names keep the `GitEmblems`
  prefix** (`GitEmblems::menu`, `GitEmblems::git`, class
  `GitEmblemsProvider`). They're not user-visible and renaming
  buys nothing — the user-facing label is just "Git".
- **Nautilus must fully reload to pick up extension changes.** The
  installer runs `nautilus -q`, but with `--gapplication-service`
  (modern default) an active window can keep the process alive and
  the old extension stays loaded. Symptom: install reports success
  but the new surface doesn't appear. Recovery is
  `pkill -u $USER nautilus && sleep 1 && nautilus &`. Don't put
  `pkill` in `install.sh` — it's user-scoped on this box but a
  surprising default for a setup script.
- **Migration from the old `git-emblems` install** (when this lived
  inside the `nautilus-folder-icons` repo): config dir moved from
  `~/.config/nautilus-folder-icons/` to `~/.config/nautilus-git-status/`
  and the config file was renamed `git-emblems.conf` → `profiles.conf`.
  `install.sh` removes the old `~/.local/share/nautilus-python/extensions/git-emblems.py`
  on every run so that running it after an old install can't leave
  two competing copies in place. The config move is a manual step
  documented in the README; we don't move user files automatically.
