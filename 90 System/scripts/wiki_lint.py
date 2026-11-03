#!/usr/bin/env python3
"""Structural checks for the LLM Wiki: lint, health, explicit graph, evidence report, raw registration.

Standard library only. lint/health/graph/evidence never write inside the Vault. register-raw
only appends a new baseline to raw/manifest.json. Passing these checks means the
structure, paths, links and raw bytes are consistent; it does NOT mean that a
source semantically supports a claim.
"""
import argparse
import datetime as dt
try:
    import fcntl
except ImportError:  # Windows: no flock; register-raw then runs without the lock
    fcntl = None
import hashlib
import json
import os
import posixpath
import re
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

CONFIG_NAME = 'wiki-config.json'
DEFAULT_CONFIG = {
    'schema_version': 1,
    'deployment_status': 'preparing',
    'inbox': '00 Inbox/Clips',
    'legacy_inboxes': [],
    'wiki': 'wiki',
    'index': 'wiki/index.md',
    'legacy_course_roots': ['05 Courses'],
    'legacy_roots': ['99 Legacy', '04 Maps', '05 Courses', '90 System'],
    'raw': 'raw',
    'manifest': 'raw/manifest.json',
    'outputs': 'outputs',
    'templates': '90 System/wiki-templates',
}
PATH_KEYS = ('inbox', 'wiki', 'index', 'raw', 'manifest', 'outputs', 'templates')
LIST_KEYS = ('legacy_inboxes', 'legacy_course_roots', 'legacy_roots')
TOP_DIR_KEYS = ('inbox', 'wiki', 'raw', 'outputs', 'templates')
LEGACY_INDEX = '04 Maps/Index.md'
LEGACY_INDEX_SKIP = ('90 System',)   # rules, tasks, templates and logs are not map entries
OPEN_ITEM_RE = re.compile(r'\s*(?:[-*+]|\d+[.)])\s+(?:확인 필요|쓰기 전에 할 일|역질문 방향|찾지 못한 것|미해결 질문|사용자 결정 대기|要確認|書く前にやること|逆質問の方向|見つからなかったこと|未解決の質問|本人の決定待ち)[^:：]{0,40}[:：]')
LOCK_DIR = '.cache/wiki_lint'
BRIEF_LIMIT = 5           # rows per list in the health brief

TYPES = ('course', 'concept', 'entity', 'method', 'project', 'decision', 'synthesis', 'source-summary')
STATUSES = ('draft', 'verified', 'conflicted', 'outdated')
CONFIDENCES = ('low', 'medium', 'high')
REQUIRED = ('title', 'type', 'status', 'sources', 'created', 'updated', 'confidence')
FIELD_KINDS = {
    'title': 'str', 'type': 'str', 'status': 'str', 'confidence': 'str', 'confidence_reason': 'str',
    'created': 'date', 'updated': 'date', 'sample': 'bool', 'aliases': 'strs',
    'sources': 'paths', 'legacy_sources': 'paths', 'courses': 'paths', 'projects': 'paths', 'supersedes': 'paths',
    'review_after': 'date',
}
KIND_TEXT = {'str': 'a double-quoted string', 'date': 'a YYYY-MM-DD date', 'bool': 'true or false',
             'strs': 'a list of strings', 'paths': 'a list of Vault-relative paths'}
STALE_DAYS = 180
REVIEW_SOON_DAYS = 14     # health brief: review_after within this many days

DATE_RE = re.compile(r'\d{4}-\d{2}-\d{2}')
HEADING_RE = re.compile(r' {0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$')
SETEXT_RE = re.compile(r' {0,3}(=+|-+)[ \t]*')
BLOCK_ID_RE = re.compile(r'(?:^|\s)\^([A-Za-z0-9-]+)[ \t]*$', re.M)
FENCE_RE = re.compile(r' {0,3}(`{3,}|~{3,})')
WIKILINK_RE = re.compile(r'(!?)\[\[([^\[\]\n]*)\]\]')
MDLINK_RE = re.compile(
    r'(!?)\[((?:[^\[\]\n]|\[[^\[\]\n]*\])*)\]\(\s*(<[^>\n]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)'
    r'(?:\s+(?:"[^"\n]*"|\'[^\'\n]*\'))?\s*\)')
SCHEME_RE = re.compile(r'([A-Za-z][A-Za-z0-9+.-]+):')
URL_RE = re.compile(r'\b[A-Za-z][A-Za-z0-9+.-]+://\S+')
FOOTNOTE_DEF_RE = re.compile(r'\s{0,3}\[\^([^\]]+)\]:(.*)')
FOOTNOTE_REF_RE = re.compile(r'\[\^([^\]]+)\]')
LIST_ITEM_RE = re.compile(r'^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?', re.M)
TABLE_RULE_RE = re.compile(r'^\s*\|?[\s:|-]*\|[\s:|-]*$', re.M)
NUMBER_RE = re.compile(r'\d[\d,.]*\d|\d+\s*(?:%|％|倍|배|万|億|억|円|엔|원|명|人|社|개|件|年|월|月|일|日|년)')


class UsageError(Exception):
    """Command, configuration or environment problem (exit 2)."""


class DataError(Exception):
    """Invalid data that stops a command (exit 1)."""


class PathProblem(ValueError):
    def __init__(self, message, escape=False):
        super().__init__(message)
        self.escape = escape


class FrontmatterError(ValueError):
    def __init__(self, line, message, code='INVALID_FRONTMATTER'):
        super().__init__(message)
        self.line, self.code = line, code


def nfc(text):
    return unicodedata.normalize('NFC', text)


def fold(text):
    return nfc(text).casefold()


def split_rel(value):
    """Validate a Vault-relative '/'-separated path and return its NFC form."""
    if not isinstance(value, str) or not value:
        raise PathProblem('path must be a non-empty string')
    if value.startswith('/') or re.match(r'[A-Za-z]:', value):
        raise PathProblem(f'absolute path is not allowed: {value}', escape=True)
    if '\\' in value:
        raise PathProblem(f'use "/" as the path separator: {value}')
    parts = value.split('/')
    if '..' in parts:
        raise PathProblem(f'".." is not allowed: {value}', escape=True)
    if any(part in ('', '.') for part in parts):
        raise PathProblem(f'empty or "." path segment: {value}')
    return nfc(value)


def within(rel, base):
    return rel == base or rel.startswith(base + '/')


def on_disk(root, rel):
    """Real path of an NFC rel. Linux and Windows keep NFD names (from macOS) as they are."""
    path = root
    for part in rel.split('/'):
        if not os.path.lexists(path / part):
            try:
                part = next((name for name in os.listdir(path) if nfc(name) == part), part)
            except OSError:
                pass
        path = path / part
    return path


def resolves_inside(root, rel):
    try:
        on_disk(root, rel).resolve().relative_to(root)
    except ValueError:
        return False
    return True


def no_duplicate_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f'duplicate JSON key: {key}')
        data[key] = value
    return data


# ---------------------------------------------------------------- configuration

def load_config(root):
    path = root / CONFIG_NAME
    if not path.exists() and not path.is_symlink():
        return json.loads(json.dumps(DEFAULT_CONFIG)), 'default'
    try:
        data = json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=no_duplicate_keys)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise UsageError(f'{CONFIG_NAME} is invalid: {error}')
    if not isinstance(data, dict):
        raise UsageError(f'{CONFIG_NAME} must contain a JSON object')
    expected = {'schema_version', 'deployment_status', *PATH_KEYS, *LIST_KEYS}
    if set(data) - expected:
        raise UsageError(f'{CONFIG_NAME} has unknown keys: {sorted(set(data) - expected)}')
    if expected - set(data):
        raise UsageError(f'{CONFIG_NAME} is missing keys: {sorted(expected - set(data))}')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise UsageError('schema_version must be the integer 1')
    if data['deployment_status'] not in ('preparing', 'active'):
        raise UsageError('deployment_status must be "preparing" or "active"')
    config = {'schema_version': 1, 'deployment_status': data['deployment_status']}

    def checked(key, value):
        try:
            rel = split_rel(value)
        except PathProblem as problem:
            raise UsageError(f'{CONFIG_NAME} {key}: {problem}')
        if not resolves_inside(root, rel):
            raise UsageError(f'{CONFIG_NAME} {key}: resolves outside the Vault: {rel}')
        return rel

    for key in PATH_KEYS:
        config[key] = checked(key, data[key])
    for key in LIST_KEYS:
        if not isinstance(data[key], list):
            raise UsageError(f'{CONFIG_NAME} {key} must be a list of paths')
        config[key] = [checked(key, value) for value in data[key]]
    if not config['manifest'].startswith(config['raw'] + '/'):
        raise UsageError('manifest must be inside raw')
    if not config['index'].startswith(config['wiki'] + '/'):
        raise UsageError('index must be inside wiki')
    tops = [(key, config[key]) for key in TOP_DIR_KEYS]
    legacy = [(key, value) for key in LIST_KEYS for value in config[key]]
    for i, (key, value) in enumerate(tops):
        for other_key, other in tops[i + 1:] + legacy:
            if key == 'templates' and other_key in LIST_KEYS:
                continue  # templates are never scanned as knowledge, so they may sit inside a legacy root
            if within(fold(value), fold(other)) or within(fold(other), fold(value)):
                raise UsageError(f'{CONFIG_NAME}: {key}={value} overlaps {other_key}={other}')
    return config, 'file'


# ---------------------------------------------------------------- markdown helpers

def decode_markdown(data):
    text = data.decode('utf-8')
    if text.startswith('﻿'):
        text = text[1:]
    return text.replace('\r\n', '\n')


def split_frontmatter(lines):
    """Return (frontmatter lines or None, index of the first body line)."""
    if not lines or lines[0].rstrip() != '---':
        return None, 0
    for index in range(1, len(lines)):
        if lines[index].rstrip() == '---':
            return lines[1:index], index + 1
    raise FrontmatterError(1, 'frontmatter block is not closed with ---')


def parse_string(value, line):
    if not value.startswith('"'):
        raise FrontmatterError(line, f'list items must be double-quoted strings: {value}')
    try:
        parsed = json.loads(value)
    except ValueError:
        raise FrontmatterError(line, f'invalid double-quoted string (JSON escapes, nothing after the quote): {value}')
    if not isinstance(parsed, str):
        raise FrontmatterError(line, f'invalid double-quoted string: {value}')
    return parsed


def parse_scalar(value, line):
    if value.startswith('"'):
        return parse_string(value, line)
    if value == '[]':
        return []
    if value in ('true', 'false'):
        return value == 'true'
    if DATE_RE.fullmatch(value):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            raise FrontmatterError(line, f'not a real calendar date: {value}', 'INVALID_FIELD')
    if value.startswith("'"):
        raise FrontmatterError(line, 'single-quoted strings are not supported; use double quotes')
    if value[0] in '|>':
        raise FrontmatterError(line, 'multiline scalars are not supported')
    if value[0] in '[{':
        raise FrontmatterError(line, 'inline lists/maps are not supported; use [] or a "  - " list')
    raise FrontmatterError(line, f'unquoted value is not supported: {value}')


def parse_frontmatter(lines):
    """Parse the supported subset. Returns ({key: value}, {key: line number})."""
    data, line_of, block_lists = {}, {}, set()
    open_list = None

    def close(key):
        if key is not None and not data[key]:
            raise FrontmatterError(line_of[key], f'{key} has no value; use [] for an empty list')

    for index, raw in enumerate(lines):
        number = index + 2
        stripped = raw.strip()
        indent = raw[:len(raw) - len(raw.lstrip())]
        if '\t' in indent:
            raise FrontmatterError(number, 'tab indentation is not supported')
        if not stripped or stripped.startswith('#'):
            continue
        if raw[0] == ' ':
            item = re.fullmatch(r'  - (.*)', raw.rstrip())
            if open_list is None or not item:
                raise FrontmatterError(number, 'unsupported indentation, nested map or nested list')
            data[open_list].append(parse_string(item.group(1).strip(), number))
            continue
        close(open_list)
        open_list = None
        match = re.fullmatch(r'([^:\s][^:]*):(?: (.*))?', raw.rstrip())
        if not match:
            raise FrontmatterError(number, f'expected "key: value": {stripped}')
        key, value = match.group(1), (match.group(2) or '').strip()
        if not re.fullmatch(r'[a-z][a-z0-9_]*', key):
            raise FrontmatterError(number, f'keys must be lowercase snake_case: {key}')
        if key in data:
            raise FrontmatterError(number, f'duplicate key: {key}')
        line_of[key] = number
        if value:
            data[key] = parse_scalar(value, number)
        else:
            data[key], open_list = [], key
            block_lists.add(key)
    close(open_list)
    return data, line_of


def mask_markdown(text):
    """Blank code fences, HTML/Obsidian comments and inline code; keep offsets and newlines."""
    out, fence = [], None
    for line in text.split('\n'):
        if fence:
            closing = re.fullmatch(r' {0,3}(`{3,}|~{3,})\s*', line)
            if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
                fence = None
            out.append(' ' * len(line))
            continue
        opening = FENCE_RE.match(line)
        if opening:
            fence = opening.group(1)
            out.append(' ' * len(line))
            continue
        out.append(line)
    masked = '\n'.join(out)

    def blank(match):
        return re.sub(r'[^\n]', ' ', match.group(0))

    masked = re.sub(r'<!--.*?(?:-->|\Z)', blank, masked, flags=re.S)
    masked = re.sub(r'%%.*?(?:%%|\Z)', blank, masked, flags=re.S)
    return re.sub(r'(`+)(?!`)(?:(?!\n[ \t]*\n).)*?(?<!`)\1(?!`)', blank, masked, flags=re.S)


def norm_heading(text):
    text = re.sub(r'[#|^:\[\]\\%]', ' ', nfc(text))
    return ' '.join(text.split()).casefold()


def parse_wikilink(inner):
    target = inner.replace('\\|', '|').split('|', 1)[0]
    path, sep, fragment = target.partition('#')
    return path.strip(), (fragment.strip() if sep else None)


# ---------------------------------------------------------------- vault index

class Vault:
    """Read-only file index used to resolve links (hidden folders excluded, like Obsidian)."""

    def __init__(self, root):
        self.root = root
        self.files = defaultdict(list)       # folded path -> [rel]
        self.names = defaultdict(list)       # folded file name -> [rel]
        self.stems = defaultdict(list)       # folded .md stem -> [rel]
        self.escapes = {}                    # folded path -> rel of symlinks leaving the Vault
        self.dirs = set()
        self._anchors = {}
        for current, dirnames, filenames in os.walk(root):
            base = Path(current).relative_to(root).as_posix()
            base = '' if base == '.' else nfc(base) + '/'
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))
            for name in dirnames:
                self.dirs.add(fold(base + name))
            for name in sorted(filenames):
                if name == '.DS_Store':
                    continue
                rel = base + nfc(name)
                if os.path.islink(os.path.join(current, name)) and not resolves_inside(root, rel):
                    self.escapes[fold(rel)] = rel
                    continue
                self.files[fold(rel)].append(rel)
                self.names[fold(name)].append(rel)
                if name.lower().endswith('.md'):
                    self.stems[fold(name[:-3])].append(rel)

    def exact(self, rel):
        """Exact NFC path lookup for metadata paths: 'ok' | 'missing' | 'escape'."""
        if fold(rel) in self.escapes:
            return 'escape'
        return 'ok' if nfc(rel) in self.files.get(fold(rel), []) else 'missing'

    def resolve(self, target, doc):
        """Resolve a link path. Returns (state, value): ok/missing/ambiguous/escape."""
        if target.startswith('/'):
            return 'escape', 'absolute link paths are not portable'
        if target.startswith(('./', '../')):
            joined = posixpath.normpath(posixpath.join(posixpath.dirname(doc), target))
            if joined == '..' or joined.startswith('../'):
                return 'escape', f'link leaves the Vault: {target}'
            return self._lookup(joined)
        if '/' in target:
            if '..' in target.split('/'):
                return 'escape', f'link leaves the Vault: {target}'
            return self._lookup(posixpath.normpath(target))
        found = sorted(set(self.names.get(fold(target), [])) | set(self.stems.get(fold(target), [])))
        if len(found) == 1:
            return 'ok', found[0]
        return ('ambiguous', found) if found else ('missing', None)

    def _lookup(self, rel):
        for candidate in (rel, rel + '.md'):
            if fold(candidate) in self.escapes:
                return 'escape', f'link target is a symlink outside the Vault: {candidate}'
            found = self.files.get(fold(candidate), [])
            if len(found) == 1:
                return 'ok', found[0]
            if found:
                return 'ambiguous', sorted(found)
        return 'missing', None

    def anchors(self, rel):
        if rel not in self._anchors:
            try:
                lines = decode_markdown(on_disk(self.root, rel).read_bytes()).split('\n')
            except (OSError, UnicodeDecodeError):
                lines = []
            if lines and lines[0].rstrip() == '---':
                for index in range(1, len(lines)):
                    if lines[index].rstrip() == '---':
                        lines = [''] * (index + 1) + lines[index + 1:]
                        break
            masked = mask_markdown('\n'.join(lines))
            headings, uncertain = heading_outline(masked.split('\n'))
            self._anchors[rel] = (headings, set(BLOCK_ID_RE.findall(masked)), uncertain)
        return self._anchors[rel]


def claim_units(lines):
    """Split masked body lines into paragraphs, list items and table rows (one claim unit each)."""
    units, current, previous_blank = [], None, True
    for index, line in enumerate(lines):
        if not line.strip() or HEADING_RE.match(line) or FOOTNOTE_DEF_RE.match(line):
            current, previous_blank = None, True
            continue
        if current is None or LIST_ITEM_RE.match(line) or line.lstrip().startswith('|'):
            current = {'start': index, 'end': index, 'indent': len(line) - len(line.lstrip()),
                       'text': [line], 'joined': not previous_blank}
            units.append(current)
        else:
            current['text'].append(line)
            current['end'] = index
        previous_blank = False
    return units


def heading_outline(lines):
    """Return ([(level, normalized text)] in document order, has_undecidable_setext).

    ATX and single-line setext headings are supported. A setext underline below a
    multi-line paragraph is reported as undecidable instead of guessed.
    """
    headings, uncertain, paragraph = [], False, []

    def is_paragraph(line):
        stripped = line.lstrip()
        return (line.strip() and not HEADING_RE.match(line) and not LIST_ITEM_RE.match(line)
                and not stripped.startswith(('|', '>')))

    for line in lines:
        atx = HEADING_RE.match(line)
        setext = SETEXT_RE.fullmatch(line)
        if atx:
            headings.append((len(line.lstrip().split(None, 1)[0]), norm_heading(atx.group(1))))
            paragraph = []
        elif setext and paragraph:
            if len(paragraph) == 1:
                headings.append((1 if setext.group(1)[0] == '=' else 2, norm_heading(paragraph[0])))
            else:
                uncertain = True
            paragraph = []
        elif is_paragraph(line):
            paragraph.append(line)
        else:
            paragraph = []
    return headings, uncertain


def heading_chain_exists(headings, parts, matches):
    """True when parts[0] > parts[1] > ... are nested sections, like Obsidian's #A#B."""
    def search(start, end, remaining):
        for index in range(start, end):
            level, text = headings[index]
            if not matches(text, remaining[0]):
                continue
            if len(remaining) == 1:
                return True
            stop = next((j for j in range(index + 1, end) if headings[j][0] <= level), end)
            if search(index + 1, stop, remaining[1:]):
                return True
        return False
    return search(0, len(headings), parts)


# ---------------------------------------------------------------- lint

class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, code, path, line, message):
        self.errors.append({'code': code, 'path': path, 'line': line, 'message': message})

    def warn(self, code, path, line, message):
        self.warnings.append({'code': code, 'path': path, 'line': line, 'message': message})

    def sorted(self, items):
        return sorted(items, key=lambda f: (f['path'], f['line'] or 0, f['code'], f['message']))


class Linter:
    def __init__(self, root, today):
        self.root = root
        self.today = today
        self.config, self.config_source = load_config(root)
        wiki = root / self.config['wiki']
        if wiki.exists() and not wiki.is_dir():
            raise UsageError(f'wiki path is not a directory: {self.config["wiki"]}')
        self.vault = Vault(root)
        self.report = Report()
        self.pages = {}          # rel -> page record
        self.edges = set()       # (source, target) explicit wikilinks
        self.links_checked = 0
        self.raw_files = []      # Vault paths of files under raw (manifest excluded)

    # -- entry point
    def run(self):
        cfg = self.config
        admin = {cfg['index'], f'{cfg["wiki"]}/overview.md', f'{cfg["wiki"]}/log.md'}
        prefix = fold(cfg['wiki'] + '/')
        for key, rel in sorted(self.vault.escapes.items()):
            if key.startswith(prefix) and key.endswith('.md'):
                self.report.error('PATH_ESCAPE', rel, None, 'wiki page is a symlink outside the Vault')
        wiki_files = sorted(rel for rels in self.vault.files.values() for rel in rels
                            if fold(rel).startswith(prefix) and rel.lower().endswith('.md'))
        for rel in wiki_files:
            self.check_page(rel, rel in admin)
        self.legacy_index_edges()
        self.check_duplicates()
        self.check_orphans_and_stale()
        self.check_index_coverage()
        raw_stats = self.check_raw()
        pages = [p for p in self.pages.values() if not p['admin']]
        stats = {
            'pages': sum(1 for p in pages if not p['sample']),
            'sample_pages': sum(1 for p in pages if p['sample']),
            'admin_pages': sum(1 for p in self.pages.values() if p['admin']),
            'links_checked': self.links_checked,
            **raw_stats,
            'config': self.config_source,
            'deployment_status': cfg['deployment_status'],
            'scope': {
                'strict_schema': cfg['wiki'],
                'schema_exempt_admin': sorted(admin),
                'link_target_lookup': {'root': '.', 'excluded': ['folders whose name starts with "."', '.DS_Store']},
                'legacy_sources_allowed_roots': cfg['legacy_roots'],
                'legacy_link_sources_for_orphans_and_graph': [LEGACY_INDEX],
                'raw_immutable': cfg['raw'],
            },
        }
        return {'errors': self.report.sorted(self.report.errors),
                'warnings': self.report.sorted(self.report.warnings),
                'stats': stats}

    # -- pages
    def check_page(self, rel, admin):
        report = self.report
        page = {'path': rel, 'admin': admin, 'fm': {}, 'sample': False, 'status': None,
                'type': None, 'updated': None, 'review_after': None, 'valid': False}
        self.pages[rel] = page
        try:
            lines = decode_markdown(on_disk(self.root, rel).read_bytes()).split('\n')
        except UnicodeDecodeError:
            report.error('INVALID_FRONTMATTER', rel, None, 'file is not valid UTF-8')
            return
        body_index = None
        try:
            fm_lines, body_index = split_frontmatter(lines)
            if fm_lines is None:
                if not admin:
                    report.error('INVALID_FRONTMATTER', rel, 1, 'wiki page must start with a --- frontmatter block')
            elif not admin:
                fm, line_of = parse_frontmatter(fm_lines)
                page['valid'] = True
                self.check_fields(page, fm, line_of)
        except FrontmatterError as problem:
            report.error(problem.code, rel, problem.line, str(problem))
        if body_index is None:
            return
        body = lines[body_index:]
        masked = mask_markdown('\n'.join(body))
        if not admin:
            meaningful = [l for l in masked.split('\n') if l.strip() and not HEADING_RE.match(l)]
            if not meaningful:
                report.error('EMPTY_PAGE', rel, None, 'page has no content besides headings/comments')
        self.check_links(rel, masked, body_index + 1)
        if not admin and page['valid']:
            self.check_numbers(page, masked, body_index + 1)

    def check_fields(self, page, fm, line_of):
        rel, report, cfg = page['path'], self.report, self.config
        for key in fm:
            if key not in FIELD_KINDS:
                report.warn('UNKNOWN_FIELD', rel, line_of[key], f'undefined frontmatter field: {key}')
        for key in REQUIRED:
            if key not in fm:
                report.error('MISSING_FIELD', rel, 1, f'missing required field: {key}')
        valid = {}
        for key, value in fm.items():
            kind = FIELD_KINDS.get(key)
            if kind is None:
                continue
            ok = {'str': isinstance(value, str), 'date': isinstance(value, dt.date),
                  'bool': isinstance(value, bool)}.get(kind, isinstance(value, list) and all(isinstance(v, str) for v in value))
            if ok:
                valid[key] = value
            else:
                report.error('INVALID_FIELD', rel, line_of[key], f'{key} must be {KIND_TEXT[kind]}')
        page['fm'] = valid
        page['sample'] = valid.get('sample') is True
        for key, choices in (('type', TYPES), ('status', STATUSES), ('confidence', CONFIDENCES)):
            if key in valid and valid[key] not in choices:
                report.error('INVALID_FIELD', rel, line_of[key], f'{key} must be one of {", ".join(choices)}')
                del valid[key]
        page['status'], page['type'], page['updated'] = valid.get('status'), valid.get('type'), valid.get('updated')
        if 'title' in valid and not valid['title'].strip():
            report.error('INVALID_FIELD', rel, line_of['title'], 'title must not be empty')
        if 'created' in valid and 'updated' in valid and valid['updated'] < valid['created']:
            report.error('INVALID_FIELD', rel, line_of['updated'], 'updated must not be earlier than created')
        if 'created' in valid and 'review_after' in valid and valid['review_after'] < valid['created']:
            report.error('INVALID_FIELD', rel, line_of['review_after'], 'review_after must not be earlier than created')
        page['review_after'] = valid.get('review_after')
        if valid.get('confidence') in ('medium', 'high') and not valid.get('confidence_reason', '').strip():
            report.error('MISSING_FIELD', rel, line_of['confidence'], 'confidence medium/high requires confidence_reason')
        for key in ('sources', 'legacy_sources', 'courses', 'projects', 'supersedes'):
            for value in valid.get(key, []):
                self.check_meta_path(rel, key, value, line_of[key])
        evidence = len(valid.get('sources', [])) + len(valid.get('legacy_sources', []))
        if evidence == 0 and 'sources' in valid:
            if valid.get('status') == 'verified':
                report.error('VERIFIED_WITHOUT_SOURCES', rel, line_of['status'],
                             'verified requires at least one entry in sources or legacy_sources')
            else:
                report.warn('NO_SOURCES', rel, line_of['sources'],
                            'no traceable sources yet; add evidence before raising status/confidence')
            if valid.get('confidence') in ('medium', 'high'):
                report.warn('WEAK_CONFIDENCE', rel, line_of['confidence'], 'medium/high confidence without any source')

    def check_meta_path(self, rel, key, value, line):
        report, cfg = self.report, self.config
        if URL_RE.match(value) or value.lower().startswith('mailto:'):
            report.error('INVALID_FIELD', rel, line, f'{key}: URLs are not direct sources; record them in a raw note: {value}')
            return
        try:
            target = split_rel(value)
        except PathProblem as problem:
            report.error('PATH_ESCAPE' if problem.escape else 'INVALID_FIELD', rel, line, f'{key}: {problem}')
            return
        if key == 'sources':
            if target == cfg['manifest']:
                report.error('INVALID_FIELD', rel, line, 'sources: the manifest is not a source')
                return
            if not target.startswith(cfg['raw'] + '/'):
                report.error('INVALID_FIELD', rel, line, f'sources must be inside {cfg["raw"]}/: {value}')
                return
        if key == 'legacy_sources' and not any(within(target, base) for base in cfg['legacy_roots']):
            report.error('INVALID_FIELD', rel, line, f'legacy_sources must be inside legacy_roots: {value}')
            return
        state = self.vault.exact(target)
        if state == 'escape':
            report.error('PATH_ESCAPE', rel, line, f'{key}: symlink leaves the Vault: {value}')
        elif state == 'missing':
            code = 'MISSING_SOURCE' if key in ('sources', 'legacy_sources') else 'BROKEN_LINK'
            report.error(code, rel, line, f'{key}: file not found: {value}')

    # -- links
    def check_links(self, rel, masked, first_line):
        for match in WIKILINK_RE.finditer(masked):
            line = first_line + masked.count('\n', 0, match.start())
            path, fragment = parse_wikilink(match.group(2))
            target = self.check_target(rel, path, fragment, line, 'wikilink')
            if target and target != rel:
                self.edges.add((rel, target))
        plain = WIKILINK_RE.sub(lambda m: ' ' * len(m.group(0)), masked)
        for match in MDLINK_RE.finditer(plain):
            line = first_line + masked.count('\n', 0, match.start())
            dest = match.group(3)
            if dest.startswith('<'):
                dest = dest[1:-1]
            scheme = SCHEME_RE.match(dest)
            if scheme:
                if scheme.group(1).lower() == 'file':
                    self.report.warn('NON_PORTABLE_LINK', rel, line, f'file:// link depends on this machine: {dest}')
                continue
            if not dest:
                self.report.warn('UNSUPPORTED_LINK', rel, line, 'empty markdown link destination')
                continue
            path, sep, fragment = dest.partition('#')
            self.check_target(rel, unquote(path), unquote(fragment) if sep else None, line, 'markdown')

    def check_target(self, rel, path, fragment, line, syntax):
        report = self.report
        self.links_checked += 1
        if not path and not fragment:
            report.warn('UNSUPPORTED_LINK', rel, line, 'empty link')
            return None
        if path.startswith('^') or (fragment or '').startswith(('#', '^^')):
            report.warn('UNSUPPORTED_LINK', rel, line, 'Obsidian search-style link is not checked')
            return None
        if path:
            state, value = self.vault.resolve(path, rel)
            if state == 'missing':
                report.error('BROKEN_LINK', rel, line, f'link target not found: {path}')
                return None
            if state == 'ambiguous':
                report.error('AMBIGUOUS_LINK', rel, line, f'{path} matches {len(value)} files: {", ".join(value)}; use a Vault path')
                return None
            if state == 'escape':
                report.error('PATH_ESCAPE', rel, line, value)
                return None
            target = value
        else:
            target = rel
        if fragment:
            if not target.lower().endswith('.md'):
                report.warn('UNSUPPORTED_LINK', rel, line, f'fragment on non-Markdown target is not checked: {target}#{fragment}')
                return target
            headings, blocks, uncertain = self.vault.anchors(target)
            parts = [part.strip() for part in fragment.split('#')]
            if '' in parts or (len(parts) > 1 and any(part.startswith('^') for part in parts)):
                report.warn('UNSUPPORTED_LINK', rel, line, f'fragment form is not checked: #{fragment}')
            elif parts[0].startswith('^'):
                if parts[0][1:] not in blocks:
                    report.error('BROKEN_LINK', rel, line, f'block {parts[0]} not found in {target}')
            else:
                def matches(text, wanted):
                    return text == norm_heading(wanted) or (
                        syntax == 'markdown' and text.replace(' ', '-') == wanted.casefold())
                if not heading_chain_exists(headings, parts, matches):
                    label = ' > '.join(parts)
                    if uncertain:
                        report.warn('UNSUPPORTED_LINK', rel, line, f'heading "{label}" not decided: '
                                    f'{target} has a multi-line setext heading')
                    else:
                        report.error('BROKEN_LINK', rel, line, f'heading "{label}" not found in {target}')
        return target

    def legacy_index_edges(self):
        rel = LEGACY_INDEX
        if self.vault.exact(rel) != 'ok':
            return
        try:
            text = mask_markdown(decode_markdown(on_disk(self.root, rel).read_bytes()))
        except (OSError, UnicodeDecodeError):
            return
        for match in WIKILINK_RE.finditer(text):
            path, _ = parse_wikilink(match.group(2))
            if path:
                state, target = self.vault.resolve(path, rel)
                if state == 'ok' and target in self.pages:
                    self.edges.add((rel, target))

    # -- grounding heuristic
    def has_source_ref(self, page, text, footnotes):
        fm = page['fm']
        sources = fm.get('sources', []) + fm.get('legacy_sources', [])
        if any(source in text for source in sources):
            return True
        if any(footnotes.get(ref) for ref in FOOTNOTE_REF_RE.findall(text)):
            return True
        links = [parse_wikilink(m.group(2))[0] for m in WIKILINK_RE.finditer(text)]
        links += [unquote(m.group(3).strip('<>').partition('#')[0]) for m in MDLINK_RE.finditer(text)]
        for link in links:
            if not link or SCHEME_RE.match(link):
                continue
            state, target = self.vault.resolve(link, page['path'])
            if state == 'ok' and (target.startswith(self.config['raw'] + '/') or target in sources):
                return True
        return False

    def check_numbers(self, page, masked, first_line):
        lines = masked.split('\n')
        footnotes = {}
        for line in lines:
            definition = FOOTNOTE_DEF_RE.match(line)
            if definition:
                footnotes[definition.group(1)] = self.has_source_ref(page, definition.group(2), {})
        units = claim_units(lines)
        fm = page['fm']
        literal_paths = fm.get('sources', []) + fm.get('legacy_sources', [])
        for position, unit in enumerate(units):
            text = '\n'.join(unit['text'])
            scrubbed = WIKILINK_RE.sub(' ', text)
            scrubbed = MDLINK_RE.sub(' ', scrubbed)
            scrubbed = URL_RE.sub(' ', scrubbed)
            for path in literal_paths:
                scrubbed = scrubbed.replace(path, ' ')
            scrubbed = FOOTNOTE_REF_RE.sub(' ', scrubbed)
            scrubbed = BLOCK_ID_RE.sub(' ', scrubbed)
            scrubbed = LIST_ITEM_RE.sub(' ', scrubbed)
            scrubbed = TABLE_RULE_RE.sub(' ', scrubbed)
            if OPEN_ITEM_RE.match(unit['text'][0]):
                continue  # open items state what is not known yet; they are not factual claims
            if not NUMBER_RE.search(scrubbed) or self.has_source_ref(page, text, footnotes):
                continue
            following = units[position + 1] if position + 1 < len(units) else None
            if (following and following['joined'] and following['start'] == unit['end'] + 1
                    and following['indent'] > unit['indent']
                    and self.has_source_ref(page, '\n'.join(following['text']), footnotes)):
                continue
            self.report.warn('UNSOURCED_NUMBER', page['path'], first_line + unit['start'],
                             'number/date without a nearby source reference (heuristic)')

    # -- page-level warnings
    def regular_pages(self):
        return [p for p in self.pages.values() if not p['admin'] and p['valid']]

    def check_duplicates(self):
        owners = defaultdict(set)
        for page in self.regular_pages():
            names = [page['fm'].get('title', '')] + page['fm'].get('aliases', [])
            for name in {' '.join(fold(n).split()) for n in names if n.strip()}:
                owners[name].add(page['path'])
        for name, paths in sorted(owners.items()):
            if len(paths) > 1:
                for path in sorted(paths):
                    others = ', '.join(sorted(paths - {path}))
                    self.report.warn('DUPLICATE_TITLE', path, None,
                                     f'title/alias "{name}" also used by {others}; review before merging')

    def check_orphans_and_stale(self):
        incoming = defaultdict(set)
        for source, target in self.edges:
            if source != target:
                incoming[target].add(source)
        for page in sorted(self.regular_pages(), key=lambda p: p['path']):
            page['orphan'] = not incoming[page['path']]
            if page['orphan']:
                self.report.warn('ORPHAN_PAGE', page['path'], None, 'no incoming explicit wikilink from wiki pages or 04 Maps/Index')
            updated = page['updated']
            page['stale'] = updated is not None and (self.today - updated).days > STALE_DAYS
            if page['stale']:
                self.report.warn('STALE_PAGE', page['path'], None,
                                 f'updated {updated.isoformat()} is more than {STALE_DAYS} days ago; status is not changed automatically')
            review = page['review_after']
            if review is not None and not page['sample'] and review <= self.today:
                self.report.warn('REVIEW_DUE', page['path'], None,
                                 f'review_after {review.isoformat()} has come; re-check time-sensitive facts '
                                 '(status is not changed automatically)')

    # -- index coverage
    def link_targets(self, rel):
        """Vault paths a Markdown file links to (wikilinks and relative markdown links that resolve)."""
        try:
            text = mask_markdown(decode_markdown(on_disk(self.root, rel).read_bytes()))
        except (OSError, UnicodeDecodeError):
            return set()
        paths = [parse_wikilink(m.group(2))[0] for m in WIKILINK_RE.finditer(text)]
        plain = WIKILINK_RE.sub(lambda m: ' ' * len(m.group(0)), text)
        for match in MDLINK_RE.finditer(plain):
            dest = match.group(3)
            if dest.startswith('<'):
                dest = dest[1:-1]
            if dest and not SCHEME_RE.match(dest):
                paths.append(unquote(dest.partition('#')[0]))
        found = set()
        for path in paths:
            if path:
                state, target = self.vault.resolve(path, rel)
                if state == 'ok':
                    found.add(target)
        return found

    def check_index_coverage(self):
        cfg = self.config
        if self.vault.exact(cfg['index']) != 'ok':
            return
        listed = self.link_targets(cfg['index'])
        for page in sorted(self.regular_pages(), key=lambda p: p['path']):
            if not page['sample'] and page['path'] not in listed:
                self.report.warn('INDEX_MISSING', page['path'], None,
                                 f'not linked from {cfg["index"]}; add a link with a one-line description')
        if self.vault.exact(LEGACY_INDEX) == 'ok':
            listed |= self.link_targets(LEGACY_INDEX)
        roots = [r for r in cfg['legacy_roots'] if r not in LEGACY_INDEX_SKIP]
        notes = sorted(rel for rels in self.vault.files.values() for rel in rels
                       if rel.lower().endswith('.md') and rel != LEGACY_INDEX
                       and any(within(rel, root) for root in roots))
        for rel in notes:
            if rel not in listed:
                self.report.warn('LEGACY_INDEX_MISSING', rel, None,
                                 f'existing-area note is not linked from {LEGACY_INDEX} or {cfg["index"]}')

    # -- raw immutability
    def check_raw(self):
        cfg, report = self.config, self.report
        stats = {'raw_files': 0, 'raw_registered': 0, 'manifest': 'absent'}
        raw_dir = self.root / cfg['raw']
        if not raw_dir.is_dir():
            return stats
        current = {}
        for folder, dirnames, filenames in os.walk(raw_dir):
            base = nfc(Path(folder).relative_to(self.root).as_posix())
            for name in list(dirnames):
                rel = f'{base}/{nfc(name)}'
                if os.path.islink(os.path.join(folder, name)):
                    if resolves_inside(self.root, rel):
                        report.warn('RAW_SYMLINK_DIR', rel, None, 'symlinked folder inside raw is not scanned')
                    else:
                        report.error('PATH_ESCAPE', rel, None, 'raw folder symlink leaves the Vault')
            for name in filenames:
                rel = f'{base}/{nfc(name)}'
                if name == '.DS_Store' or rel == cfg['manifest']:
                    continue
                if os.path.islink(os.path.join(folder, name)) and not resolves_inside(self.root, rel):
                    report.error('PATH_ESCAPE', rel, None, 'raw file symlink leaves the Vault')
                    continue
                current[rel] = Path(folder) / name
        stats['raw_files'] = len(current)
        self.raw_files = sorted(current)
        try:
            manifest = load_manifest(self.root, cfg)
        except DataError as problem:
            report.error('MANIFEST_INVALID', cfg['manifest'], None, f'{problem}; raw comparison skipped')
            stats['manifest'] = 'invalid'
            return stats
        if manifest is None:
            manifest = {}
        else:
            stats['manifest'] = 'present'
        for rel, entry in sorted(manifest.items()):
            path = current.get(rel)
            if path is None:
                report.error('RAW_MISSING', rel, None, 'registered raw file is missing (or is no longer a regular file)')
            elif sha256_file(path) != entry['sha256']:
                report.error('RAW_MODIFIED', rel, None, f'bytes differ from the baseline registered {entry["registered"]}')
            else:
                stats['raw_registered'] += 1
        for rel in sorted(set(current) - set(manifest)):
            report.warn('RAW_UNREGISTERED', rel, None, 'raw file has no baseline; run register-raw after review')
        return stats


# ---------------------------------------------------------------- manifest

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(root, cfg):
    """Return {rel: entry}, None when absent, or raise DataError when corrupt."""
    path = root / cfg['manifest']
    if path.is_symlink():
        raise DataError('manifest must not be a symlink')
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_bytes().decode('utf-8'), object_pairs_hook=no_duplicate_keys)
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise DataError(f'manifest is not valid JSON: {error}')
    if not isinstance(data, dict) or set(data) != {'schema_version', 'files'}:
        raise DataError('manifest must have exactly schema_version and files')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise DataError('manifest schema_version must be the integer 1')
    if not isinstance(data['files'], dict):
        raise DataError('manifest files must be an object')
    files = {}
    for key, entry in data['files'].items():
        try:
            rel = split_rel(key)
        except PathProblem as problem:
            raise DataError(f'manifest path {key!r}: {problem}')
        if not rel.startswith(cfg['raw'] + '/') or rel == cfg['manifest'] or rel in files:
            raise DataError(f'manifest path is outside raw, the manifest itself or duplicated: {key}')
        if not isinstance(entry, dict) or set(entry) != {'sha256', 'registered'}:
            raise DataError(f'manifest entry {key} must have exactly sha256 and registered')
        if not isinstance(entry['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
            raise DataError(f'manifest entry {key}: sha256 must be 64 lowercase hex characters')
        registered = entry['registered']
        try:
            if not isinstance(registered, str) or not DATE_RE.fullmatch(registered):
                raise ValueError
            dt.date.fromisoformat(registered)
        except ValueError:
            raise DataError(f'manifest entry {key}: registered must be a YYYY-MM-DD date')
        files[rel] = entry
    return files


def exists_with_exact_names(root, rel):
    """Reject case/normalization aliases that a case-insensitive filesystem would accept."""
    folder = root
    for part in rel.split('/'):
        try:
            names = {nfc(name): name for name in os.listdir(folder)}
        except OSError:
            return False
        if part not in names:
            return False
        folder = folder / names[part]
    return True


def register_raw(root, target, today):
    cfg, _ = load_config(root)
    try:
        rel = split_rel(target)
    except PathProblem as problem:
        raise UsageError(f'register-raw: {problem}')
    if rel == cfg['manifest']:
        raise UsageError('register-raw: the manifest cannot register itself')
    if not rel.startswith(cfg['raw'] + '/'):
        raise UsageError(f'register-raw: path must be inside {cfg["raw"]}/')
    if posixpath.basename(rel) == '.DS_Store':
        raise UsageError('register-raw: .DS_Store is excluded')
    path = on_disk(root, rel)
    if not resolves_inside(root, rel):
        raise UsageError('register-raw: path resolves outside the Vault')
    if not path.is_file() or not exists_with_exact_names(root, rel):
        raise UsageError(f'register-raw: not an existing regular file with this exact path: {rel}')
    lock_dir = root / LOCK_DIR
    for folder in (root / '.cache', lock_dir):
        if folder.is_symlink():
            raise UsageError(f'register-raw: {folder.relative_to(root)} must not be a symlink')
    lock_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = root / cfg['manifest']
    with open(lock_dir / 'raw-manifest.lock', 'a') as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        files = load_manifest(root, cfg) or {}
        digest = sha256_file(path)
        if rel in files:
            if files[rel]['sha256'] == digest:
                return 'unchanged'
            raise DataError(f'{rel} is already registered with a different hash; baseline kept. '
                            'Register a corrected copy as a new file instead.')
        files[rel] = {'sha256': digest, 'registered': today.isoformat()}
        payload = json.dumps({'schema_version': 1, 'files': dict(sorted(files.items()))},
                             ensure_ascii=False, indent=2) + '\n'
        write_atomic(manifest_path, payload.encode('utf-8'), lock_dir)
    return 'registered'


def write_atomic(path, data, temp_dir):
    handle, temp = tempfile.mkstemp(prefix='manifest-', suffix='.tmp', dir=temp_dir)
    try:
        with os.fdopen(handle, 'wb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


# ---------------------------------------------------------------- evidence (string grounding)

EV_QUOTE_RES = (re.compile(r'「([^」\n]{4,})」'), re.compile(r'“([^”\n]{4,})”'))
EV_ASCII_QUOTE_RE = re.compile(r'"([^"\n]{4,})"')
EV_NUMBER_RE = re.compile(r'(?<![\d.])(\d{4}-\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日|\d+\.\d+|\d{3,})(?![\d.])')
EV_DATE_RE = re.compile(r'(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?')
EV_AI_MARK_RE = re.compile(r'AI ?(?:해석|계산|보충|추정|解釈|計算|補足|推定)|조정자(?:의)? ?(?:해석|계산|기록)')
EV_APPROX_RE = re.compile(r'(?:(?<![가-힣])약|約|およそ)\s*$')
EV_PAREN_RE = re.compile(r'[（(]([^）)]*)[）)]')
EV_SPLIT_RE = re.compile(r'…|\.\.\.|⋯|\s/\s|\s—\s|·')   # pieces of a partial or joined quote
HANGUL_RE = re.compile(r'[가-힣]')


def ev_norm(text):
    """Compare form: NFKC (full-width to half-width), no thousands commas, no whitespace."""
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'(?<=\d),(?=\d{3})', '', text)
    return re.sub(r'\s+', '', text)


def ev_number_forms(token):
    match = EV_DATE_RE.fullmatch(token)
    if not match:
        return [token]
    y, m, d = match.group(1), int(match.group(2)), int(match.group(3))
    return sorted({f'{y}-{m:02d}-{d:02d}', f'{y}-{m}-{d}', f'{y}年{m}月{d}日', f'{y}年{m:02d}月{d:02d}日',
                   f'{y}/{m}/{d}', f'{y}/{m:02d}/{d:02d}', f'{y}.{m}.{d}', f'{y}.{m:02d}.{d:02d}'})


def ev_quote_forms(quote):
    """Each form is a list of pieces that must all occur (ellipses split a partial quote)."""
    variants = {quote, EV_PAREN_RE.sub(lambda m: '' if HANGUL_RE.search(m.group(1)) else m.group(0), quote)}
    forms = []
    for variant in sorted(variants):
        pieces = [ev_norm(p) for p in EV_SPLIT_RE.split(variant)]
        pieces = [p for p in pieces if len(p) >= 4]
        if pieces:
            forms.append(pieces)
    return forms


class EvidenceChecker:
    """Read-only: do dates, numbers and direct quotes in wiki pages occur in their linked raw files?

    String matching only. A match does not prove the source supports the claim, and a miss may be a
    unit conversion or translation. Never edits pages, raw files or the manifest.
    """

    def __init__(self, linter):
        self.linter = linter
        self.root = linter.root
        self.config = linter.config
        self.vault = linter.vault
        self.findings = []
        self.stats = defaultdict(int)
        self._corpus = {}

    def corpus(self, rel):
        if rel not in self._corpus:
            try:
                self._corpus[rel] = ev_norm(decode_markdown(on_disk(self.root, rel).read_bytes()))
            except (OSError, UnicodeDecodeError):
                self._corpus[rel] = ''
        return self._corpus[rel]

    def unit_links(self, text, page_rel):
        """(raw files, outputs files, other evidence files outside wiki/raw/outputs) linked from a unit."""
        cfg = self.config
        paths = [parse_wikilink(m.group(2))[0] for m in WIKILINK_RE.finditer(text)]
        plain = WIKILINK_RE.sub(lambda m: ' ' * len(m.group(0)), text)
        for match in MDLINK_RE.finditer(plain):
            dest = match.group(3).strip('<>')
            if dest and not SCHEME_RE.match(dest):
                paths.append(unquote(dest.partition('#')[0]))
        raw, outs, other = [], [], []
        for path in paths:
            if not path:
                continue
            state, target = self.vault.resolve(path, page_rel)
            if state != 'ok' or target == page_rel:
                continue
            if within(target, cfg['raw']):
                raw.append(target)
            elif within(target, cfg['outputs']):
                outs.append(target)
            elif not within(target, cfg['wiki']):
                other.append(target)
        return list(dict.fromkeys(raw)), list(dict.fromkeys(outs)), other

    def run(self, only=None):
        cfg = self.config
        pages = sorted(self.linter.regular_pages(), key=lambda p: p['path'])
        if only:
            wanted = {nfc(p) for p in only}
            unknown = wanted - {p['path'] for p in pages}
            if unknown:
                raise UsageError(f'not a valid wiki page: {", ".join(sorted(unknown))}')
            pages = [p for p in pages if p['path'] in wanted]
        else:
            invalid = sum(1 for p in self.linter.pages.values() if not p['admin'] and not p['valid'])
            if invalid:
                self.stats['skipped_invalid_pages'] = invalid  # broken frontmatter: see lint errors
        for page in pages:
            if page['sample']:
                continue
            self.stats['pages'] += 1
            self.check_page(page)
        return {'findings': self.findings, 'stats': dict(sorted(self.stats.items()))}

    def check_page(self, page):
        rel, fm = page['path'], page['fm']
        lines = decode_markdown(on_disk(self.root, rel).read_bytes()).split('\n')
        _, body_index = split_frontmatter(lines)
        masked = mask_markdown('\n'.join(lines[body_index:])).split('\n')
        first_line = body_index + 1
        sources = [s for s in fm.get('sources', []) if within(s, self.config['raw'])]
        literal_paths = fm.get('sources', []) + fm.get('legacy_sources', [])
        units = claim_units(masked)
        for position, unit in enumerate(units):
            text = '\n'.join(unit['text'])
            if OPEN_ITEM_RE.match(unit['text'][0]):
                self.stats['skipped_open_items'] += 1
                continue
            if EV_AI_MARK_RE.search(text):
                self.stats['skipped_ai_marked_units'] += 1
                continue
            linked, outs, other = self.unit_links(text, rel)
            following = units[position + 1] if position + 1 < len(units) else None
            if (not linked and following and following['joined'] and following['start'] == unit['end'] + 1
                    and following['indent'] > unit['indent']):
                linked, child_outs, child_other = self.unit_links('\n'.join(following['text']), rel)
                outs = outs or child_outs
                other = other or child_other
            if linked or outs:
                targets = linked  # a unit citing only outputs is checked there, not in frontmatter sources
            elif other:
                self.stats['unchecked_units_non_raw_evidence'] += 1
                continue
            else:
                targets = sources
            if not targets and not outs:
                self.stats['unchecked_units_no_raw_source'] += 1
                continue
            pool = list(dict.fromkeys(sources + linked))
            for offset, line in enumerate(unit['text']):
                self.check_line(rel, first_line + unit['start'] + offset, line, literal_paths, targets, outs, pool)

    def check_line(self, rel, line_no, line, literal_paths, targets, outs, pool):
        blank = lambda m: ' ' * len(m.group(0))
        clean = WIKILINK_RE.sub(blank, line)
        clean = MDLINK_RE.sub(blank, clean)
        clean = URL_RE.sub(blank, clean)
        for path in literal_paths:
            clean = clean.replace(path, ' ' * len(path))
        clean = FOOTNOTE_REF_RE.sub(blank, clean)
        spans = [m for regex in EV_QUOTE_RES for m in regex.finditer(clean)]
        if clean.count('"') % 2 == 0:
            spans += list(EV_ASCII_QUOTE_RE.finditer(clean))
        quotes = [m.group(1) for m in spans]
        for match in spans:  # numbers inside a quote are checked as part of that quote
            clean = clean[:match.start()] + ' ' * (match.end() - match.start()) + clean[match.end():]
        for quote in quotes:
            if HANGUL_RE.search(EV_PAREN_RE.sub('', quote)):
                self.stats['skipped_hangul_quotes'] += 1
                continue
            forms = ev_quote_forms(quote)
            if forms:
                self.judge(rel, line_no, 'quote', quote, forms, targets, outs, pool)
        numeric = re.sub(r'(?<=\d),(?=\d{3})', '', unicodedata.normalize('NFKC', clean))
        for match in EV_NUMBER_RE.finditer(numeric):
            if EV_APPROX_RE.search(numeric[max(0, match.start() - 12):match.start()]):
                self.stats['skipped_approx_numbers'] += 1
                continue
            token = match.group(1)
            kind = 'date' if EV_DATE_RE.fullmatch(token) else 'number'
            forms = [[ev_norm(f)] for f in ev_number_forms(token)]
            self.judge(rel, line_no, kind, token, forms, targets, outs, pool)

    def judge(self, rel, line_no, kind, value, forms, targets, outs, pool):
        def holds(source):
            text = self.corpus(source)
            return any(all(piece in text for piece in pieces) for pieces in forms)
        if any(holds(t) for t in targets):
            self.stats[f'{kind}_found'] += 1
            return
        if any(holds(o) for o in outs):  # e.g. a sentence from an output: counted apart from raw matches
            self.stats[f'{kind}_found_in_outputs'] += 1
            return
        elsewhere = [s for s in pool if s not in targets and holds(s)]
        code = 'EVIDENCE_OTHER_SOURCE' if elsewhere else 'EVIDENCE_NOT_FOUND'
        self.stats[f'{kind}_{"other_source" if elsewhere else "not_found"}'] += 1
        self.findings.append({'code': code, 'path': rel, 'line': line_no, 'kind': kind, 'value': value,
                              'checked': targets + outs, 'found_in': elsewhere})


# ---------------------------------------------------------------- health and graph

def build_health(result, linter):
    pages = linter.regular_pages()
    counted = [p for p in pages if not p['sample']]
    by_status = {status: 0 for status in STATUSES}
    for page in counted:
        key = page['status'] or 'invalid'
        by_status[key] = by_status.get(key, 0) + 1
    invalid_pages = sum(1 for p in linter.pages.values() if not p['admin'] and not p['valid'])
    if invalid_pages:
        by_status['invalid'] = by_status.get('invalid', 0) + invalid_pages

    def by_code(items):
        counts = defaultdict(int)
        for item in items:
            counts[item['code']] += 1
        return {'total': len(items), 'by_code': dict(sorted(counts.items()))}

    codes = [f['code'] for f in result['errors']]
    return {
        'pages': len(counted) + invalid_pages,
        'by_status': by_status,
        'sample_pages': len(pages) - len(counted),
        'broken_links': codes.count('BROKEN_LINK') + codes.count('AMBIGUOUS_LINK'),
        'missing_sources': codes.count('MISSING_SOURCE'),
        'orphan_pages': sorted(p['path'] for p in counted if p.get('orphan')),
        'stale_pages': sorted(p['path'] for p in counted if p.get('stale')),
        'errors': by_code(result['errors']),
        'warnings': by_code(result['warnings']),
        'brief': build_brief(linter, counted),
    }


def build_brief(linter, pages):
    """Session-start view: inbox, recent pages, open items, uncited raw and evidence totals (read-only)."""
    root, cfg = linter.root, linter.config
    inbox = root / cfg['inbox']
    pending = 0
    if inbox.is_dir():
        pending = sum(1 for path in inbox.rglob('*') if path.is_file() and path.name != '.DS_Store'
                      and not any(part.startswith('.') for part in path.relative_to(inbox).parts))
    recent = sorted((p for p in pages if p['updated']), key=lambda p: (-p['updated'].toordinal(), p['path']))
    open_counts = {}
    for page in pages:
        try:
            text = mask_markdown(decode_markdown(on_disk(root, page['path']).read_bytes()))
        except (OSError, UnicodeDecodeError):
            continue
        count = sum(1 for line in text.split('\n') if OPEN_ITEM_RE.match(line))
        if count:
            open_counts[page['path']] = count
    top = sorted(open_counts.items(), key=lambda item: (-item[1], item[0]))[:BRIEF_LIMIT]
    dated = sorted((p['review_after'], p['path']) for p in pages if p['review_after'])
    soon_until = linter.today + dt.timedelta(days=REVIEW_SOON_DAYS)
    review_row = lambda day, path: {'path': path, 'review_after': day.isoformat()}
    evidence = EvidenceChecker(linter).run()['stats']
    total = lambda suffix: sum(v for k, v in evidence.items() if k.endswith(suffix))
    paths = {p['path'] for p in pages}
    cited = {s for p in pages for s in p['fm'].get('sources', [])} | {t for s, t in linter.edges if s in paths}
    return {
        'inbox_pending': pending,
        'recently_updated': [{'path': p['path'], 'updated': p['updated'].isoformat()} for p in recent[:BRIEF_LIMIT]],
        'open_items': {'total': sum(open_counts.values()),
                       'top_pages': [{'path': path, 'count': count} for path, count in top]},
        'raw_not_linked_from_wiki': sum(1 for rel in linter.raw_files if rel not in cited),
        'review_due': [review_row(day, path) for day, path in dated if day <= linter.today],
        'review_soon': [review_row(day, path) for day, path in dated if linter.today < day <= soon_until],
        'evidence': {'not_found': total('_not_found'), 'other_source': total('_other_source'),
                     'found_in_outputs': total('_found_in_outputs')},
    }


def build_graph(linter):
    cfg = linter.config
    nodes = {}
    for rel, page in linter.pages.items():
        nodes[rel] = 'admin' if page['admin'] else (page['type'] or 'unknown')
    for source, target in linter.edges:
        for rel in (source, target):
            if rel not in nodes:
                if within(rel, cfg['raw']):
                    nodes[rel] = 'raw'
                elif rel == LEGACY_INDEX or any(within(rel, base) for base in cfg['legacy_roots']):
                    nodes[rel] = 'legacy'
                else:
                    nodes[rel] = 'other'
    backlinks = defaultdict(list)
    for source, target in sorted(linter.edges):
        backlinks[target].append(source)
    return {
        'nodes': [{'id': rel, 'kind': kind} for rel, kind in sorted(nodes.items())],
        'edges': [{'source': s, 'target': t, 'kind': 'explicit'} for s, t in sorted(linter.edges)],
        'backlinks': dict(sorted(backlinks.items())),
    }


def graph_output_path(root, cfg, output):
    path = Path(output).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        parent = path.parent.resolve(strict=True)
    except OSError:
        raise UsageError(f'graph output folder does not exist: {path.parent}')
    final = parent / path.name
    if final.exists() or final.is_symlink():
        raise UsageError(f'graph output already exists; choose a new path: {final}')
    try:
        rel = final.relative_to(root).as_posix()
    except ValueError:
        return final
    allowed = ('.cache', f'{cfg["outputs"]}/.generated')
    if not any(rel.startswith(base + '/') for base in allowed):
        raise UsageError('inside the Vault, graph output may only go under .cache/ or '
                         f'{cfg["outputs"]}/.generated/ (never raw, wiki, sources or rules)')
    return final


# ---------------------------------------------------------------- CLI

def print_findings(result):
    for label, items in (('ERROR', result['errors']), ('WARNING', result['warnings'])):
        for item in items:
            where = item['path'] + (f':{item["line"]}' if item['line'] else '')
            print(f'{label} {item["code"]} {where} {item["message"]}')
    stats = result['stats']
    print(f'{len(result["errors"])} errors, {len(result["warnings"])} warnings; '
          f'{stats["pages"]} pages (+{stats["sample_pages"]} sample), {stats["raw_files"]} raw files '
          f'({stats["raw_registered"]} verified against manifest). Structural checks only; not fact verification.')


def command_lint(root, args):
    linter = Linter(root, args.today)
    result = linter.run()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_findings(result)
    return 1 if result['errors'] else 0


def command_health(root, args):
    linter = Linter(root, args.today)
    result = linter.run()
    health = build_health(result, linter)
    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2))
    else:
        for key, value in health.items():
            print(f'{key}: {json.dumps(value, ensure_ascii=False)}')
    return 1 if result['errors'] else 0


def command_graph(root, args):
    linter = Linter(root, args.today)
    output = graph_output_path(root, linter.config, args.output) if args.output else None
    result = linter.run()
    payload = json.dumps(build_graph(linter), ensure_ascii=False, indent=2) + '\n'
    if output is None:
        sys.stdout.write(payload)
    else:
        try:
            with open(output, 'x', encoding='utf-8') as handle:
                handle.write(payload)
        except FileExistsError:
            raise UsageError(f'graph output already exists; choose a new path: {output}')
        print(f'Wrote graph to {output}')
    if result['errors']:
        print(f'graph built, but lint found {len(result["errors"])} errors; unresolved links are not edges',
              file=sys.stderr)
        return 1
    return 0


def command_evidence(root, args):
    linter = Linter(root, args.today)
    linter.run()
    result = EvidenceChecker(linter).run(args.page)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    for item in result['findings']:
        where = f'{item["path"]}:{item["line"]}'
        detail = (f'found in {", ".join(item["found_in"])}; line links {", ".join(item["checked"])}'
                  if item['found_in'] else f'checked {", ".join(item["checked"])}')
        print(f'{item["code"]} {where} {item["kind"]} {item["value"]} ({detail})')
    stats = result['stats']
    missing = sum(v for k, v in stats.items() if k.endswith('_not_found'))
    elsewhere = sum(v for k, v in stats.items() if k.endswith('_other_source'))
    found = sum(v for k, v in stats.items() if k.endswith('_found') and not k.endswith('_not_found'))
    in_outputs = sum(v for k, v in stats.items() if k.endswith('_found_in_outputs'))
    print(f'{missing} not found, {elsewhere} only in another source of the page, {found} found, '
          f'{in_outputs} found only in linked outputs; {stats.get("pages", 0)} pages. '
          'String matching only; not fact verification.')
    return 0


def command_register(root, args):
    outcome = register_raw(root, args.path, args.today)
    if outcome == 'unchanged':
        print(f'Already registered with the same SHA-256; manifest unchanged: {args.path}')
    else:
        print(f'Registered baseline: {args.path}')
    return 0


def parse_day(value):
    if not DATE_RE.fullmatch(value):
        raise argparse.ArgumentTypeError('use YYYY-MM-DD')
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError('not a real date')


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    specs = (
        ('lint', command_lint, 'check wiki schema, links, sources and raw baselines (read-only)'),
        ('health', command_health, 'summarize page counts, errors and warnings (read-only)'),
        ('graph', command_graph, 'export explicit wikilinks and backlinks as JSON'),
        ('evidence', command_evidence, 'report dates, numbers and quotes not found in linked raw files (read-only)'),
        ('register-raw', command_register, 'add one new raw file baseline to the manifest'),
    )
    for name, handler, help_text in specs:
        sub = commands.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        sub.add_argument('--root', type=Path, default=Path('.'), help='Vault root (default: current folder)')
        sub.add_argument('--today', type=parse_day, default=dt.date.today(),
                         help='override today (YYYY-MM-DD) for reproducible checks')
        if name in ('lint', 'health', 'evidence'):
            sub.add_argument('--json', action='store_true', help='machine-readable output')
        if name == 'evidence':
            sub.add_argument('--page', action='append', help='Vault-relative wiki page to check (repeatable)')
        if name == 'graph':
            sub.add_argument('--output', help='new file to create (default: stdout)')
        if name == 'register-raw':
            sub.add_argument('path', help='Vault-relative path inside raw/')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        if not root.is_dir():
            raise UsageError(f'root is not a directory: {args.root}')
        return args.handler(root, args)
    except (UsageError, DataError, OSError) as problem:
        code = 1 if isinstance(problem, DataError) else 2
        if getattr(args, 'json', False):
            kind = 'DATA_ERROR' if code == 1 else 'USAGE_ERROR'
            print(json.dumps({'fatal': {'code': kind, 'message': str(problem)}}, ensure_ascii=False))
        print(f'{args.command} failed: {problem}', file=sys.stderr)
        return code


if __name__ == '__main__':
    raise SystemExit(main())
