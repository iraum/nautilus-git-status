"""
Nautilus extension: live git status emblems on folder icons.

Each git-repo folder gets exactly one emblem encoding two signals on a single
canvas:

  * outer disk  — repo state, with priority dirty > behind > ahead > clean
                  (orange / red / green / white)
  * inner dot   — ownership tier: which of the user's git profiles the repo
                  belongs to (primary / secondary / tertiary). 'external'
                  renders without an inner dot. The inner dot's outline
                  switches to a bolder crimson stroke when the repo has no
                  remote configured — flags purely-local repos at a glance.

Tier is derived (in order of preference) from:
  1. owner slug parsed out of `git remote get-url origin`
  2. `git config user.name` (for purely local repos with no origin)
  3. `git config user.email` (last-resort fallback)

The mapping from identifier -> tier is read from
~/.config/nautilus-git-status/profiles.conf. The file is watched via
Gio.FileMonitor; edits take effect on the next emblem refresh.

Emblems are composited by Nautilus on top of whatever icon the folder
already has, so custom-icon PNGs (e.g. from the companion
`nautilus-folder-icons` tool) keep working underneath the status dot.

Implementation:
  - update_file_info() runs `git status` synchronously on a cache miss.
    `git status` on a healthy repo is fast (typically tens of ms), so the
    first render of a parent directory containing N repos costs ~N quick
    git invocations. Subsequent renders are cache hits.
  - Each repo's .git/ (and refs/heads, refs/remotes) is watched via
    Gio.FileMonitor. Any change drops the cache entry and calls
    invalidate_extension_info() so Nautilus re-queries — the emblem
    refreshes within a fraction of a second.
  - Earlier versions tried to push the git call into a worker thread and
    rely on invalidate_extension_info() to trigger a re-render. In
    practice Nautilus didn't re-call update_file_info reliably after
    that invalidation, so most folders were left without an emblem.
    Going synchronous removes the race.
"""

import configparser
import os
import subprocess
import threading
from collections import OrderedDict
from urllib.parse import unquote, urlparse

import gi
gi.require_version('Nautilus', '3.0')
gi.require_version('Gio', '2.0')
gi.require_version('GLib', '2.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Nautilus, GObject, GLib, Gio, Gtk  # noqa: E402


GIT_BIN = 'git'
GIT_TIMEOUT_SEC = 2

# Hard cap on tracked repos (cache + FileInfo + Gio.FileMonitor). Without
# this, browsing through many parent folders monotonically grows the
# inotify watch count, eventually starving every other watcher in the
# user's session (default per-user limit ~8192). LRU eviction cancels the
# oldest repos' monitors; the next visit reinstates them.
MAX_TRACKED_REPOS = 256

# GIT_OPTIONAL_LOCKS=0 stops `git status` from taking the index lock to
# refresh the stat-cache. Without it, every status call rewrites
# .git/index, which our Gio.FileMonitor on .git/ sees, which drops our
# cache, which makes the next render run status again — a self-fed loop.
# Also avoids fighting concurrent CLI git invocations for the same lock.
GIT_ENV = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}

CONFIG_PATH = os.path.expanduser(
    '~/.config/nautilus-git-status/profiles.conf'
)


def _load_owner_config(path):
    """Parse the ownership config into {identifier_lower: tier_str}.

    File format (lines starting with # are comments):

      primary   = iraum, iraumbo@gmail.com
      secondary = x42i
      tertiary  = iraum-oracle

    Each value is a comma-separated list of owner slugs and/or emails.
    Identifiers not listed in any tier fall through to 'external' at
    lookup time.
    """
    mapping = {}
    try:
        with open(path, 'r') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                key, val = line.split('=', 1)
                tier = key.strip().lower()
                if tier not in ('primary', 'secondary', 'tertiary'):
                    continue
                for raw in val.split(','):
                    name = raw.strip().lower()
                    if name:
                        mapping[name] = tier
    except OSError:
        pass
    return mapping


def _parse_origin_owner(url):
    """Extract the owner slug from a remote URL, or None if unparseable.

    Handles HTTPS (https://host/OWNER/repo[.git]), SSH (git@host:OWNER/repo),
    ssh:// URLs, and trailing .git stripping.
    """
    if not url:
        return None
    s = url.strip()
    if s.endswith('.git'):
        s = s[:-4]
    # SSH "git@host:owner/repo" form has no scheme but contains ':'.
    if '://' not in s and ':' in s:
        path = s.split(':', 1)[1]
    else:
        try:
            path = urlparse(s).path
        except Exception:
            return None
    path = path.lstrip('/')
    if not path:
        return None
    first = path.split('/', 1)[0]
    return first or None


class GitEmblemsProvider(GObject.GObject,
                         Nautilus.InfoProvider,
                         Nautilus.PropertyPageProvider,
                         Nautilus.MenuProvider):
    def __init__(self):
        super().__init__()
        # _files is the LRU driver: insertion order = recency, oldest first.
        # _cache and _monitors are evicted in lockstep with _files.
        self._cache = {}                  # path -> list[str] emblem names
        self._files = OrderedDict()       # path -> Nautilus.FileInfo (LRU)
        self._monitors = {}               # path -> [Gio.FileMonitor, ...]
        self._lock = threading.Lock()
        self._owner_map = _load_owner_config(CONFIG_PATH)
        self._config_monitor = None
        self._watch_config()

    # ---- Nautilus entry point ----------------------------------------------

    def update_file_info(self, file):
        if file.get_uri_scheme() != 'file':
            return Nautilus.OperationResult.COMPLETE
        if not file.is_directory():
            return Nautilus.OperationResult.COMPLETE

        path = unquote(urlparse(file.get_uri()).path)
        # .git can be a directory (normal repo) or a file (worktree, submodule).
        if not os.path.exists(os.path.join(path, '.git')):
            return Nautilus.OperationResult.COMPLETE

        with self._lock:
            self._files[path] = file
            self._files.move_to_end(path)  # mark as most-recently-used
            cached = self._cache.get(path)
            # Evict LRU repos beyond the cap. Defer monitor.cancel() until
            # after we drop the lock — Gio calls shouldn't run under it.
            evicted_monitors = []
            while len(self._files) > MAX_TRACKED_REPOS:
                old_path, _ = self._files.popitem(last=False)
                self._cache.pop(old_path, None)
                evicted_monitors.extend(self._monitors.pop(old_path, []))

        for mon in evicted_monitors:
            try:
                mon.cancel()
            except Exception:
                pass

        if cached is None:
            cached = self._compute_emblems(path)
            with self._lock:
                self._cache[path] = cached
            self._ensure_monitor(path)

        for emb in cached:
            file.add_emblem(emb)
        return Nautilus.OperationResult.COMPLETE

    # ---- file monitoring ----------------------------------------------------

    def _resolve_git_dir(self, path):
        """Return the absolute git directory for a working tree.

        For ordinary repos this is `<path>/.git`. For worktrees and
        submodules, `.git` is a text file containing `gitdir: <path>` —
        follow the pointer (resolving relative paths) to find the real
        git directory.
        """
        git_path = os.path.join(path, '.git')
        if os.path.isfile(git_path):
            try:
                with open(git_path, 'r') as fh:
                    line = fh.readline().strip()
                if line.startswith('gitdir: '):
                    gd = line[len('gitdir: '):].strip()
                    if not os.path.isabs(gd):
                        gd = os.path.normpath(os.path.join(path, gd))
                    return gd
            except OSError:
                pass
        return git_path

    def _read_git_config(self, path):
        """Parse the repo's `.git/config` and return a ConfigParser.

        Returns an empty parser on any error. Lets us read remote URLs and
        local user.name / user.email without forking `git config` —
        eliminates 2-3 subprocesses on the cold render path. We don't
        follow `[include]` directives or read global ~/.gitconfig here;
        callers must fall back to `git config` for values that may live
        in user-global config (typically user.name / user.email).
        """
        parser = configparser.ConfigParser(
            strict=False,
            interpolation=None,
            comment_prefixes=('#', ';'),
        )
        cfg_path = os.path.join(self._resolve_git_dir(path), 'config')
        try:
            with open(cfg_path, 'r') as fh:
                parser.read_file(fh)
        except (OSError, configparser.Error):
            pass
        return parser

    def _ensure_monitor(self, path):
        with self._lock:
            if path in self._monitors:
                return
            self._monitors[path] = []  # placeholder to dedupe concurrent calls

        git_path = self._resolve_git_dir(path)
        monitors = []
        # .git/ root catches HEAD, index, FETCH_HEAD, packed-refs, config.
        # refs/heads + refs/remotes catch branch updates that don't touch root.
        for sub in ('', 'refs/heads', 'refs/remotes'):
            mon_path = os.path.join(git_path, sub) if sub else git_path
            if not os.path.isdir(mon_path):
                continue
            try:
                gfile = Gio.File.new_for_path(mon_path)
                monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error:
                continue
            monitor.connect('changed', self._on_git_changed, path)
            monitors.append(monitor)

        with self._lock:
            self._monitors[path] = monitors

    def _on_git_changed(self, monitor, gfile, other_file, event_type, path):
        with self._lock:
            self._cache.pop(path, None)
            file = self._files.get(path)
        if file is not None:
            GLib.idle_add(self._invalidate, file)

    def _invalidate(self, file):
        try:
            file.invalidate_extension_info()
        except Exception:
            pass
        return False  # don't repeat

    def _watch_config(self):
        cfg_dir = os.path.dirname(CONFIG_PATH)
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except OSError:
            return
        try:
            gfile = Gio.File.new_for_path(CONFIG_PATH)
            # monitor_file fires whether or not the file currently exists, so
            # editing or first-time-creating the config triggers a reload.
            monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        monitor.connect('changed', self._on_config_changed)
        self._config_monitor = monitor

    def _on_config_changed(self, monitor, gfile, other_file, event_type):
        self._owner_map = _load_owner_config(CONFIG_PATH)
        with self._lock:
            paths = list(self._files.keys())
            self._cache.clear()
        for p in paths:
            f = self._files.get(p)
            if f is not None:
                GLib.idle_add(self._invalidate, f)

    # ---- git ----------------------------------------------------------------

    def _compute_emblems(self, path):
        status = self._compute_status(path)
        if status is None:
            return []
        _, tier, _, has_remote = self._identify(path)
        # External has no inner dot, so the no-remote outline doesn't apply
        # there. For tiered repos, missing remote -> bolder crimson outline.
        if tier in ('primary', 'secondary', 'tertiary') and not has_remote:
            return [f'git-{status}-{tier}-noremote']
        return [f'git-{status}-{tier}']

    def _compute_status(self, path):
        try:
            out = subprocess.check_output(
                [GIT_BIN, '-C', path, 'status', '--porcelain=v2', '--branch',
                 '--no-renames'],
                stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT_SEC,
                env=GIT_ENV,
            ).decode('utf-8', 'replace')
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError):
            return None

        ahead = behind = 0
        dirty = False
        saw_branch_ab = False
        for line in out.splitlines():
            if line.startswith('# branch.ab '):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        ahead = int(parts[2].lstrip('+'))
                        behind = int(parts[3].lstrip('-'))
                    except ValueError:
                        pass
                saw_branch_ab = True
            elif line and not line.startswith('#'):
                # Tracked-modified, staged, untracked (?), unmerged — all dirty.
                dirty = True
                # branch.ab always precedes file entries in v2 output, so
                # once we have it and any dirty line we know the answer.
                # Skips parsing thousands of file lines on huge dirty trees.
                if saw_branch_ab:
                    break

        if dirty:
            return 'dirty'
        if behind:
            return 'behind'
        if ahead:
            return 'ahead'
        return 'clean'

    def _identify(self, path):
        """Return (identifier, tier, source, has_remote).

        - tier ∈ {primary, secondary, tertiary, external}
        - source ∈ {origin, user.name, user.email, unmatched}
        - identifier is whatever string was used to assign the tier (or, for
          unmatched, the best fallback for display).
        - has_remote is True iff `git remote` lists at least one remote.

        Origin wins when present: a clone of github.com/some-stranger/foo is
        external regardless of which local user.email was configured to
        commit to it. user.name / user.email matter only for purely local
        repos that never gained an origin. has_remote is reported separately
        from source because a repo can have a non-origin remote (e.g. only
        an "upstream" remote configured) — that still counts as "has a
        remote" for the visual no-remote signal.

        Reads `.git/config` directly to skip 2-3 subprocesses on the cold
        render path. Falls back to `git config` only for user.name /
        user.email when they aren't set locally — those typically live
        in ~/.gitconfig and need git's include resolution.
        """
        cfg = self._read_git_config(path)
        # `.git/config` uses `[remote "<name>"]` subsection headers; the
        # whole quoted form becomes the configparser section name.
        has_remote = any(s.startswith('remote ') for s in cfg.sections())
        url = ''
        if cfg.has_section('remote "origin"'):
            url = cfg.get('remote "origin"', 'url', fallback='').strip()
        if url:
            slug = _parse_origin_owner(url)
            if slug:
                tier = self._owner_map.get(slug.lower(), 'external')
                return (slug, tier, 'origin', has_remote)

        # Local first, then `git config` (which sees ~/.gitconfig + XDG).
        name = cfg.get('user', 'name', fallback='').strip() \
            or self._run_git(path, ['config', 'user.name']).strip()
        if name:
            tier = self._owner_map.get(name.lower())
            if tier:
                return (name, tier, 'user.name', has_remote)
        email = cfg.get('user', 'email', fallback='').strip() \
            or self._run_git(path, ['config', 'user.email']).strip()
        if email:
            tier = self._owner_map.get(email.lower())
            if tier:
                return (email, tier, 'user.email', has_remote)
        return (name or email or '', 'external', 'unmatched', has_remote)

    # ---- Properties dialog: "Git" tab --------------------------------------

    def get_property_pages(self, files):
        if len(files) != 1:
            return []
        f = files[0]
        if f.get_uri_scheme() != 'file' or not f.is_directory():
            return []
        path = unquote(urlparse(f.get_uri()).path)
        if not os.path.exists(os.path.join(path, '.git')):
            return []

        info = self._gather_git_info(path)
        page = self._build_property_page(info)
        page.show_all()
        label = Gtk.Label(label='Git')
        label.show()
        return [Nautilus.PropertyPage(
            name='GitEmblems::git', label=label, page=page,
        )]

    def _gather_git_info(self, path):
        info = {
            'branch': None, 'upstream': None,
            'ahead': 0, 'behind': 0,
            'staged': 0, 'modified': 0, 'untracked': 0, 'unmerged': 0,
            'origin_url': None, 'last_commit': None,
            'identity': None, 'tier': None, 'identity_source': None,
            'has_remote': True,
        }
        out = self._run_git(
            path, ['status', '--porcelain=v2', '--branch', '--no-renames'],
        )
        for line in out.splitlines():
            if line.startswith('# branch.head '):
                info['branch'] = line[len('# branch.head '):].strip()
            elif line.startswith('# branch.upstream '):
                info['upstream'] = line[len('# branch.upstream '):].strip()
            elif line.startswith('# branch.ab '):
                parts = line.split()
                try:
                    info['ahead'] = int(parts[2].lstrip('+'))
                    info['behind'] = int(parts[3].lstrip('-'))
                except (IndexError, ValueError):
                    pass
            elif line.startswith('1 ') or line.startswith('2 '):
                # "<XY> ..." — X is index status, Y is worktree status.
                fields = line.split(None, 2)
                if len(fields) >= 2 and len(fields[1]) >= 2:
                    if fields[1][0] != '.':
                        info['staged'] += 1
                    if fields[1][1] != '.':
                        info['modified'] += 1
            elif line.startswith('? '):
                info['untracked'] += 1
            elif line.startswith('u '):
                info['unmerged'] += 1

        url = self._run_git(path, ['remote', 'get-url', 'origin']).strip()
        info['origin_url'] = url or None
        last = self._run_git(
            path, ['log', '-1', '--pretty=format:%s  —  %cr'],
        ).strip()
        info['last_commit'] = last or None

        ident, tier, src, has_remote = self._identify(path)
        info['identity'] = ident or None
        info['tier'] = tier
        info['identity_source'] = src
        info['has_remote'] = has_remote
        return info

    def _run_git(self, path, args):
        try:
            return subprocess.check_output(
                [GIT_BIN, '-C', path] + args,
                stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT_SEC,
                env=GIT_ENV,
            ).decode('utf-8', 'replace')
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError):
            return ''

    # ---- Right-click menu --------------------------------------------------

    def get_file_items(self, files):
        if len(files) != 1:
            return []
        f = files[0]
        if f.get_uri_scheme() != 'file' or not f.is_directory():
            return []
        path = unquote(urlparse(f.get_uri()).path)
        if not os.path.exists(os.path.join(path, '.git')):
            return []

        info = self._gather_git_info(path)
        top = Nautilus.MenuItem(
            name='GitEmblems::menu',
            label='Git — ' + self._menu_headline(info),
            tip='Git status for this repo',
        )
        submenu = Nautilus.Menu()
        for it in self._build_menu_items(info):
            submenu.append_item(it)
        top.set_submenu(submenu)
        return [top]

    def get_background_items(self, file):
        # Right-click on the folder background of an already-open repo.
        return self.get_file_items([file])

    def _menu_headline(self, info):
        branch = info['branch'] or '—'
        n_changed = info['staged'] + info['modified'] + info['untracked'] + info['unmerged']
        if n_changed:
            noun = 'change' if n_changed == 1 else 'changes'
            return f'dirty — {n_changed} {noun} ({branch})'
        if info['behind'] and info['ahead']:
            return f'↑{info["ahead"]} ↓{info["behind"]} ({branch})'
        if info['behind']:
            return f'↓{info["behind"]} behind ({branch})'
        if info['ahead']:
            return f'↑{info["ahead"]} ahead ({branch})'
        return f'clean ({branch})'

    def _build_menu_items(self, info):
        rows = []
        rows.append(('identity', f'Identity: {self._format_identity(info)}'))
        rows.append(('branch', f'Branch: {info["branch"] or "(unknown)"}'))
        if info['upstream']:
            up = info['upstream']
            if info['ahead'] or info['behind']:
                up += f'  (↑{info["ahead"]} ↓{info["behind"]})'
            rows.append(('upstream', f'Upstream: {up}'))
        parts = []
        if info['staged']:    parts.append(f'{info["staged"]} staged')
        if info['modified']:  parts.append(f'{info["modified"]} modified')
        if info['untracked']: parts.append(f'{info["untracked"]} untracked')
        if info['unmerged']:  parts.append(f'{info["unmerged"]} unmerged')
        rows.append(('status', 'Status: ' + (', '.join(parts) if parts else 'clean')))
        if info['origin_url']:
            rows.append(('origin', f'Origin: {info["origin_url"]}'))
        if info['last_commit']:
            rows.append(('last', f'Last commit: {info["last_commit"]}'))

        items = []
        for key, label in rows:
            it = Nautilus.MenuItem(name=f'GitEmblems::menu::{key}', label=label)
            # Display-only: not actionable, but kept enabled so the text
            # renders at full contrast rather than dimmed-out gray.
            items.append(it)
        return items

    def _format_identity(self, info):
        ident = info['identity'] or '—'
        tier = info['tier'] or 'external'
        parts = [tier]
        if info.get('has_remote') is False:
            parts.append('no remote')
        return f'{ident} ({", ".join(parts)})'

    # ---- Properties dialog page builder ------------------------------------

    def _build_property_page(self, info):
        # Status line — same priority as the emblem.
        if info['staged'] or info['modified'] or info['untracked'] or info['unmerged']:
            status = 'Dirty'
            parts = []
            if info['staged']:    parts.append(f"{info['staged']} staged")
            if info['modified']:  parts.append(f"{info['modified']} modified")
            if info['untracked']: parts.append(f"{info['untracked']} untracked")
            if info['unmerged']:  parts.append(f"{info['unmerged']} unmerged")
            status += '  —  ' + ', '.join(parts)
        elif info['behind']:
            status = f"Behind upstream by {info['behind']}"
        elif info['ahead']:
            status = f"Ahead of upstream by {info['ahead']}"
        else:
            status = 'Clean'

        rows = [
            ('Status', status),
            ('Identity', self._format_identity(info)),
            ('Branch', info['branch'] or '(unknown)'),
        ]
        if info['upstream']:
            up = info['upstream']
            if info['ahead'] or info['behind']:
                up += f"  (ahead {info['ahead']}, behind {info['behind']})"
            rows.append(('Upstream', up))
        if info['origin_url']:
            rows.append(('Origin', info['origin_url']))
        if info['last_commit']:
            rows.append(('Last commit', info['last_commit']))

        grid = Gtk.Grid(
            column_spacing=18, row_spacing=8,
            margin_start=18, margin_end=18,
            margin_top=18, margin_bottom=18,
        )
        for r, (key, val) in enumerate(rows):
            k = Gtk.Label(label=key, xalign=0.0)
            k.get_style_context().add_class('dim-label')
            v = Gtk.Label(label=val, xalign=0.0, selectable=True)
            v.set_line_wrap(True)
            v.set_line_wrap_mode(2)  # PANGO_WRAP_WORD_CHAR
            grid.attach(k, 0, r, 1, 1)
            grid.attach(v, 1, r, 1, 1)
        return grid
