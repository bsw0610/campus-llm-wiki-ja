import datetime as dt
import hashlib
import io
import json
import unicodedata
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import wiki_lint

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'wiki_lint.py'
TODAY = dt.date(2026, 9, 17)


def page(title='개념', status='draft', confidence='low', sources=('raw/web/source.md',), body='본문 설명.',
         extra='', created='2026-09-01', updated='2026-09-10'):
    source_lines = ''.join(f'  - "{s}"\n' for s in sources)
    sources_block = f'sources:\n{source_lines}' if sources else 'sources: []\n'
    return (f'---\ntitle: "{title}"\ntype: "concept"\nstatus: "{status}"\n{sources_block}'
            f'created: {created}\nupdated: {updated}\nconfidence: "{confidence}"\n{extra}---\n\n{body}\n')


class VaultCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / 'wiki-config.json').write_text(json.dumps(wiki_lint.DEFAULT_CONFIG), 'utf-8')
        self.write('raw/web/source.md', 'Original source text 2026.\n')
        self.write('wiki/index.md', '# Wiki\n')

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8') if isinstance(text, str) else text)
        return path

    def lint(self, today=TODAY):
        linter = wiki_lint.Linter(self.root, today)
        return linter.run(), linter

    def codes(self, result, kind='errors'):
        return [item['code'] for item in result[kind]]

    def cli(self, *args):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = wiki_lint.main([*args, '--root', str(self.root), '--today', TODAY.isoformat()])
        return code, out.getvalue(), err.getvalue()

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.root.rglob('*')) if p.is_file()}


class FrontmatterTests(VaultCase):
    def test_valid_page_with_korean_japanese_paths_crlf_and_bom(self):
        self.write('raw/university/社会統計学/第1回 資料.md', '# 回帰分析\n\n説明 ^def-1\n')
        self.write('05 Courses/2026-秋/社会統計学/社会統計学.md', '# 社会統計学\n\n## 評価方法\n')
        body = ('# 회귀분석\n\n## 정의\n\n![[raw/university/社会統計学/第1回 資料#^def-1]] 참조.\n\n'
                '[[社会統計学#評価方法|과목 홈]]과 [[#정의]]를 연결한다. '
                '원문 [자료](raw/university/社会統計学/第1回%20資料.md#回帰分析), [외부](https://example.com).\n')
        text = page(title='회귀분석', sources=('raw/university/社会統計学/第1回 資料.md',), body=body,
                    extra='aliases:\n  - "回帰分析"\ncourses:\n  - "05 Courses/2026-秋/社会統計学/社会統計学.md"\n')
        self.write('wiki/concepts/회귀분석.md', b'\xef\xbb\xbf' + text.replace('\n', '\r\n').encode())
        self.write('wiki/index.md', '# Wiki\n\n- [[wiki/concepts/회귀분석|회귀분석]]\n')
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])
        self.assertNotIn('ORPHAN_PAGE', self.codes(result, 'warnings'))

    def test_missing_required_key(self):
        self.write('wiki/concepts/a.md', page().replace('confidence: "low"\n', ''))
        result, _ = self.lint()
        self.assertIn('MISSING_FIELD', self.codes(result))

    def test_unsupported_yaml_is_explicit_error(self):
        cases = {
            'duplicate key': 'title: "again"\n',
            'single quote': "aliases:\n  - 'x'\n",
            'bare string': 'confidence_reason: plain text\n',
            'nested map': 'aliases:\n  nested: "x"\n',
            'multiline': 'confidence_reason: |\n',
            'inline list': 'aliases: ["x"]\n',
            'tab indent': 'aliases:\n\t- "x"\n',
            'empty block': 'aliases:\n',
            'three-space item': 'aliases:\n   - "x"\n',
        }
        for name, extra in cases.items():
            with self.subTest(name):
                self.write('wiki/concepts/a.md', page(extra=extra))
                result, _ = self.lint()
                self.assertIn('INVALID_FRONTMATTER', self.codes(result))

    def test_invalid_values(self):
        cases = {
            'enum': (page(status='done'), 'INVALID_FIELD'),
            'calendar date': (page(created='2026-02-30'), 'INVALID_FIELD'),
            'updated before created': (page(created='2026-09-10', updated='2026-09-01'), 'INVALID_FIELD'),
            'confidence reason': (page(confidence='medium'), 'MISSING_FIELD'),
            'empty title': (page(title=''), 'INVALID_FIELD'),
            'wrong type': (page(extra='sample: "yes"\n'), 'INVALID_FIELD'),
            'unclosed': (page().replace('---\n\n', '\n', 1), 'INVALID_FRONTMATTER'),
            'no frontmatter': ('# only body\n', 'INVALID_FRONTMATTER'),
        }
        for name, (text, code) in cases.items():
            with self.subTest(name):
                self.write('wiki/concepts/a.md', text)
                result, _ = self.lint()
                self.assertIn(code, self.codes(result))

    def test_unknown_field_warns(self):
        self.write('wiki/concepts/a.md', page(extra='rating: "5"\n', body='설명 [[wiki/index]]'))
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])
        self.assertIn('UNKNOWN_FIELD', self.codes(result, 'warnings'))

    def test_verified_requires_sources_draft_only_warns(self):
        self.write('wiki/concepts/a.md', page(status='verified', sources=()))
        result, _ = self.lint()
        self.assertIn('VERIFIED_WITHOUT_SOURCES', self.codes(result))
        self.write('wiki/concepts/a.md', page(status='draft', sources=()))
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])
        self.assertIn('NO_SOURCES', self.codes(result, 'warnings'))
        self.write('05 Courses/x/note.md', 'legacy text')
        self.write('wiki/concepts/a.md', page(status='verified', confidence='medium', sources=(),
                                              extra='legacy_sources:\n  - "05 Courses/x/note.md"\n'
                                                    'confidence_reason: "직접 확인"\n'))
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])

    def test_source_paths(self):
        cases = {
            'missing': ('raw/web/none.md', 'MISSING_SOURCE'),
            'url': ('https://example.com/a', 'INVALID_FIELD'),
            'escape': ('raw/../../etc/passwd', 'PATH_ESCAPE'),
            'absolute': ('/etc/passwd', 'PATH_ESCAPE'),
            'outside raw': ('wiki/index.md', 'INVALID_FIELD'),
            'manifest': ('raw/manifest.json', 'INVALID_FIELD'),
        }
        for name, (source, code) in cases.items():
            with self.subTest(name):
                self.write('wiki/concepts/a.md', page(sources=(source,)))
                result, _ = self.lint()
                self.assertIn(code, self.codes(result))
        self.write('wiki/concepts/a.md', page(extra='legacy_sources:\n  - "99 Archive/x.md"\n'))
        self.write('99 Archive/x.md', 'x')
        result, _ = self.lint()
        self.assertIn('INVALID_FIELD', self.codes(result))

    def test_broken_source_is_detected_and_restore_clears_it(self):
        self.write('wiki/concepts/a.md', page(body='설명 [[wiki/index]]'))
        source = self.root / 'raw/web/source.md'
        moved = self.root / 'moved.md'
        source.rename(moved)
        result, _ = self.lint()
        self.assertIn('MISSING_SOURCE', self.codes(result))
        moved.rename(source)
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])

    def test_empty_page(self):
        self.write('wiki/concepts/a.md', page(body='# 제목만\n\n<!-- TODO -->'))
        result, _ = self.lint()
        self.assertIn('EMPTY_PAGE', self.codes(result))


class LinkTests(VaultCase):
    def lint_body(self, body):
        self.write('wiki/concepts/a.md', page(body=body))
        result, _ = self.lint()
        return result

    def test_broken_links_headings_and_blocks(self):
        self.write('02 Knowledge/target.md', '# Heading One\n\ntext ^blk-1\n')
        ok = self.lint_body('[[target#heading one]] [[target#^blk-1]] ![[target]] [[target|표시]]')
        self.assertEqual(ok['errors'], [])
        for body in ('[[nothing]]', '[[target#No Such]]', '[[target#^missing]]', '[[#없는 제목]]',
                     '[text](02%20Knowledge/none.md)'):
            with self.subTest(body):
                self.assertIn('BROKEN_LINK', self.codes(self.lint_body(body)))

    def test_same_basename_is_ambiguous_until_path_is_used(self):
        self.write('02 Knowledge/Note.md', 'a')
        self.write('03 Sources/Articles/Note.md', 'b')
        self.assertIn('AMBIGUOUS_LINK', self.codes(self.lint_body('[[Note]]')))
        self.assertEqual(self.lint_body('[[02 Knowledge/Note]] [[03 Sources/Articles/Note.md|B]]')['errors'], [])

    def test_code_and_comments_are_not_links(self):
        body = ('```md\n[[fenced example]]\n```\n\n~~~~\n[[tilde]]\n~~~~\n\n'
                '`[[inline example]]` <!-- [[comment]] --> %% [[obsidian comment]] %%\n\n실제 문장.')
        result = self.lint_body(body)
        self.assertEqual(result['errors'], [])

    def test_path_escapes(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        Path(outside.name, 'secret.md').write_text('# secret')
        (self.root / '02 Knowledge').mkdir()
        (self.root / '02 Knowledge/link.md').symlink_to(Path(outside.name, 'secret.md'))
        self.assertIn('PATH_ESCAPE', self.codes(self.lint_body('[[02 Knowledge/link]]')))
        self.assertIn('PATH_ESCAPE', self.codes(self.lint_body('[x](../../../outside.md)')))
        self.assertIn('PATH_ESCAPE', self.codes(self.lint_body('[[../../../outside]]')))
        (self.root / 'wiki/concepts/escaped.md').symlink_to(Path(outside.name, 'secret.md'))
        result = self.lint_body('설명')
        self.assertTrue(any(e['code'] == 'PATH_ESCAPE' and e['path'] == 'wiki/concepts/escaped.md'
                            for e in result['errors']))

    def test_nfd_file_names_match_nfc_links_and_sources(self):
        name = unicodedata.normalize('NFD', 'ブログ分析.md')
        self.write('raw/web/' + name, 'データ')
        self.write('02 Knowledge/' + name, '# ガイド')
        self.write('wiki/concepts/a.md', page(sources=('raw/web/ブログ分析.md',), body='[[02 Knowledge/ブログ分析#ガイド]]'))
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])
        self.assertEqual(wiki_lint.register_raw(self.root, 'raw/web/ブログ分析.md', TODAY), 'registered')
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])

    def link_findings(self, target_text, link):
        self.write('tasks/target.md', target_text)
        self.write('wiki/index.md', f'# Wiki\n\n{link}\n')
        result, _ = self.lint()
        return self.codes(result), self.codes(result, 'warnings')

    def test_hierarchical_heading_links_check_every_level(self):
        target = '# 실제 제목\n\n## B\n\n# 다른 상위\n\n## C\n'
        cases = {
            '[[tasks/target#없는상위제목#B]]': (['BROKEN_LINK'], []),
            '[[tasks/target#다른 상위#B]]': (['BROKEN_LINK'], []),
            '[[tasks/target#실제 제목#C]]': (['BROKEN_LINK'], []),
            '[[tasks/target#실제 제목#B]]': ([], []),
            '[[tasks/target#다른 상위#C]]': ([], []),
            '[[tasks/target#B]]': ([], []),
            '[[tasks/target#실제 제목#^blk]]': ([], ['UNSUPPORTED_LINK']),
        }
        for link, expected in cases.items():
            with self.subTest(link):
                errors, warnings = self.link_findings(target, link)
                self.assertEqual((errors, [w for w in warnings if w == 'UNSUPPORTED_LINK']), expected)

    def test_setext_headings(self):
        target = '실제 제목\n===\n\n하위 제목\n---\n\n본문\n\n# ATX 제목\n'
        cases = {
            '[[tasks/target#실제 제목]]': ([], []),
            '[[tasks/target#실제 제목#하위 제목]]': ([], []),
            '[[tasks/target#ATX 제목]]': ([], []),
            '[[tasks/target#없는 ATX 제목]]': (['BROKEN_LINK'], []),
            '[[tasks/target#본문]]': (['BROKEN_LINK'], []),
        }
        for link, expected in cases.items():
            with self.subTest(link):
                errors, warnings = self.link_findings(target, link)
                self.assertEqual((errors, [w for w in warnings if w == 'UNSUPPORTED_LINK']), expected)

    def test_multiline_setext_is_reported_as_undecidable(self):
        errors, warnings = self.link_findings('첫 줄\n둘째 줄\n===\n', '[[tasks/target#둘째 줄]]')
        self.assertEqual(errors, [])
        self.assertIn('UNSUPPORTED_LINK', warnings)
        errors, _ = self.link_findings('# 확실한 제목\n', '[[tasks/target#없음]]')
        self.assertEqual(errors, ['BROKEN_LINK'])

    def test_relative_link_from_document_folder(self):
        self.write('wiki/concepts/b.md', page(title='b'))
        self.assertEqual(self.lint_body('[[./b]] [b](./b.md)')['errors'], [])

    def test_external_and_unsupported_links(self):
        result = self.lint_body('[a](https://x.jp) [b](mailto:a@b.c) [c](zotero://select/1) '
                                '[d](obsidian://open?x) [e](file:///Users/x/y.pdf) [[##search]]')
        self.assertEqual(result['errors'], [])
        warnings = self.codes(result, 'warnings')
        self.assertIn('NON_PORTABLE_LINK', warnings)
        self.assertIn('UNSUPPORTED_LINK', warnings)

    def test_admin_pages_skip_schema_but_check_links(self):
        self.write('wiki/overview.md', '# Overview without frontmatter\n\n[[missing page]]\n')
        self.write('wiki/log.md', '')
        result, _ = self.lint()
        self.assertEqual(self.codes(result), ['BROKEN_LINK'])
        self.assertEqual(result['errors'][0]['path'], 'wiki/overview.md')

    def test_legacy_notes_are_not_schema_or_link_checked(self):
        self.write('02 Knowledge/old.md', 'no frontmatter [[broken legacy link]]\n')
        self.write('03 Sources/Articles/raw-ish.md', '---\ntags: [a, b]\n---\n[[another broken]]\n')
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])


class ConfigTests(VaultCase):
    def run_with_config(self, config):
        (self.root / 'wiki-config.json').write_text(json.dumps(config) if not isinstance(config, str) else config)
        return self.cli('lint', '--json')

    def test_invalid_configs_exit_2(self):
        base = dict(wiki_lint.DEFAULT_CONFIG)
        cases = {
            'unknown key': {**base, 'extra': 1},
            'missing key': {k: v for k, v in base.items() if k != 'raw'},
            'bool version': {**base, 'schema_version': True},
            'status': {**base, 'deployment_status': 'on'},
            'absolute': {**base, 'wiki': '/tmp/wiki'},
            'dotdot': {**base, 'raw': '../raw'},
            'manifest outside raw': {**base, 'manifest': 'wiki/manifest.json'},
            'index outside wiki': {**base, 'index': 'raw/index.md'},
            'overlap': {**base, 'outputs': 'wiki/outputs'},
            'legacy overlap': {**base, 'legacy_roots': ['wiki']},
            'broken json': '{"schema_version": 1',
            'duplicate key': '{"schema_version": 1, "schema_version": 1}',
        }
        for name, config in cases.items():
            with self.subTest(name):
                code, out, _ = self.run_with_config(config)
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out)['fatal']['code'], 'USAGE_ERROR')

    def test_external_symlink_config_path_rejected(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.root / 'outputs').symlink_to(outside.name, target_is_directory=True)
        code, _, _ = self.cli('lint')
        self.assertEqual(code, 2)

    def test_scope_separates_link_lookup_from_legacy_sources(self):
        code, out, _ = self.cli('lint', '--json')
        scope = json.loads(out)['stats']['scope']
        self.assertEqual(code, 0)
        self.assertNotIn('link_lookup_only', scope)
        self.assertEqual(scope['link_target_lookup'],
                         {'root': '.', 'excluded': ['folders whose name starts with "."', '.DS_Store']})
        self.assertEqual(scope['legacy_sources_allowed_roots'], wiki_lint.DEFAULT_CONFIG['legacy_roots'])
        self.assertEqual(scope['legacy_link_sources_for_orphans_and_graph'], ['04 Maps/Index.md'])
        self.assertEqual(scope['strict_schema'], 'wiki')
        self.assertEqual(scope['raw_immutable'], 'raw')

    def test_templates_may_live_inside_a_legacy_root_but_knowledge_dirs_may_not(self):
        base = dict(wiki_lint.DEFAULT_CONFIG)
        code, _, _ = self.run_with_config({**base, 'templates': '90 System/wiki-templates'})
        self.assertEqual(code, 0)
        code, _, _ = self.run_with_config({**base, 'outputs': '90 System/outputs'})
        self.assertEqual(code, 2)

    def test_missing_config_uses_defaults(self):
        (self.root / 'wiki-config.json').unlink()
        code, out, _ = self.cli('lint', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['stats']['config'], 'default')


class RawTests(VaultCase):
    def manifest_bytes(self):
        path = self.root / 'raw/manifest.json'
        return path.read_bytes() if path.exists() else None

    def test_register_then_detect_modify_delete_and_new_file(self):
        self.assertEqual(self.cli('register-raw', 'raw/web/source.md')[0], 0)
        data = json.loads(self.manifest_bytes())
        entry = data['files']['raw/web/source.md']
        self.assertEqual(entry['sha256'], hashlib.sha256(b'Original source text 2026.\n').hexdigest())
        self.assertEqual(entry['registered'], TODAY.isoformat())
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])
        self.write('raw/web/new.md', 'new')
        result, _ = self.lint()
        self.assertEqual(self.codes(result, 'warnings'), ['RAW_UNREGISTERED'])
        self.write('raw/web/source.md', 'Original source text 2027.\n')
        result, _ = self.lint()
        self.assertEqual(self.codes(result), ['RAW_MODIFIED'])
        (self.root / 'raw/web/source.md').unlink()
        result, _ = self.lint()
        self.assertEqual(self.codes(result), ['RAW_MISSING'])
        self.assertEqual(json.loads(self.manifest_bytes()), data)

    def test_same_hash_is_noop_and_different_hash_fails(self):
        self.cli('register-raw', 'raw/web/source.md')
        before = self.manifest_bytes()
        stat = (self.root / 'raw/manifest.json').stat().st_mtime_ns
        self.assertEqual(self.cli('register-raw', 'raw/web/source.md')[0], 0)
        self.assertEqual(self.manifest_bytes(), before)
        self.assertEqual((self.root / 'raw/manifest.json').stat().st_mtime_ns, stat)
        self.write('raw/web/source.md', 'changed')
        code, _, err = self.cli('register-raw', 'raw/web/source.md')
        self.assertEqual(code, 1)
        self.assertIn('baseline kept', err)
        self.assertEqual(self.manifest_bytes(), before)

    def test_register_rejects_bad_targets_without_writing(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        Path(outside.name, 'x.md').write_text('x')
        (self.root / 'raw/web/link.md').symlink_to(Path(outside.name, 'x.md'))
        self.write('wiki/concepts/a.md', 'x')
        self.write('raw/.DS_Store', 'x')
        for target in ('../x.md', 'raw/../wiki/concepts/a.md', str(self.root / 'raw/web/source.md'),
                       'wiki/concepts/a.md', 'raw/manifest.json', 'raw/web/none.md', 'raw/web/link.md',
                       'raw/web', 'raw/.DS_Store', 'RAW/web/source.md'):
            with self.subTest(target):
                self.assertEqual(self.cli('register-raw', target)[0], 2)
                self.assertIsNone(self.manifest_bytes())

    def test_corrupt_manifest_stops_register_and_is_lint_error(self):
        valid_hash = 'a' * 64
        cases = {
            'empty file': '',
            'bad json': '{',
            'extra key': json.dumps({'schema_version': 1, 'files': {}, 'x': 1}),
            'bad hash': json.dumps({'schema_version': 1, 'files': {'raw/web/a.md': {'sha256': 'ABC', 'registered': '2026-09-17'}}}),
            'bad date': json.dumps({'schema_version': 1, 'files': {'raw/web/a.md': {'sha256': valid_hash, 'registered': '2026-13-01'}}}),
            'outside raw': json.dumps({'schema_version': 1, 'files': {'wiki/a.md': {'sha256': valid_hash, 'registered': '2026-09-17'}}}),
            'duplicate key': '{"schema_version": 1, "files": {}, "files": {}}',
        }
        for name, text in cases.items():
            with self.subTest(name):
                self.write('raw/manifest.json', text)
                self.assertEqual(self.cli('register-raw', 'raw/web/source.md')[0], 1)
                self.assertEqual(self.manifest_bytes(), text.encode())
                result, _ = self.lint()
                self.assertIn('MANIFEST_INVALID', self.codes(result))

    def test_empty_manifest_is_valid(self):
        self.write('raw/manifest.json', json.dumps({'schema_version': 1, 'files': {}}))
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.codes(result, 'warnings'), ['RAW_UNREGISTERED'])

    def test_write_failure_keeps_raw_and_manifest(self):
        self.cli('register-raw', 'raw/web/source.md')
        self.write('raw/web/second.md', 'second')
        before = self.snapshot()
        with mock.patch.object(wiki_lint.os, 'replace', side_effect=OSError('disk full')):
            self.assertEqual(self.cli('register-raw', 'raw/web/second.md')[0], 2)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list((self.root / '.cache/wiki_lint').glob('manifest-*')), [])

    def test_concurrent_registration_keeps_every_entry(self):
        names = [f'raw/web/file{i}.md' for i in range(12)]
        for i, name in enumerate(names):
            self.write(name, f'content {i}')
        errors = []

        def register(name):
            try:
                wiki_lint.register_raw(self.root, name, TODAY)
            except Exception as error:  # pragma: no cover - surfaced below
                errors.append(error)

        threads = [threading.Thread(target=register, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(set(json.loads(self.manifest_bytes())['files']), set(names))

    def test_ds_store_ignored_other_hidden_files_reported(self):
        self.write('raw/web/.DS_Store', 'x')
        self.write('raw/web/.hidden.md', 'x')
        result, _ = self.lint()
        paths = [w['path'] for w in result['warnings'] if w['code'] == 'RAW_UNREGISTERED']
        self.assertEqual(sorted(paths), ['raw/web/.hidden.md', 'raw/web/source.md'])

    def test_unicode_raw_path_registers(self):
        self.write('raw/career/企業A 説明会.md', '発言記録')
        self.assertEqual(self.cli('register-raw', 'raw/career/企業A 説明会.md')[0], 0)
        result, _ = self.lint()
        self.assertEqual(result['errors'], [])


class HealthGraphTests(VaultCase):
    def build(self):
        self.write('05 Courses/2026-秋/社会統計学/社会統計学.md', '# 社会統計学')
        self.write('wiki/courses/stat.md', page(title='社会統計学 wiki', body='[[wiki/concepts/regression|회귀]] [[wiki/concepts/regression]]').replace('"concept"', '"course"'))
        self.write('wiki/concepts/regression.md', page(title='회귀분석', status='verified', confidence='medium',
                                                       extra='confidence_reason: "원문 직접 확인"\n',
                                                       body='[[wiki/courses/stat]] 와 [[regression]]'))
        self.write('wiki/concepts/orphan.md', page(title='고립', created='2026-01-01', updated='2026-03-21'))
        self.write('wiki/concepts/stale.md', page(title='오래됨', created='2026-01-01', updated='2026-03-20', body='[[wiki/concepts/orphan]]'))
        self.write('wiki/concepts/sample.md', page(title='샘플', extra='sample: true\n', created='2025-01-01', updated='2025-01-01'))
        self.write('04 Maps/Index.md', '- [[wiki/concepts/stale|old]]\n- [[wiki/courses/stat]]\n')

    def test_health_counts_sample_orphans_and_180_day_boundary(self):
        self.build()
        code, out, _ = self.cli('health', '--json')
        health = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(health['pages'], 4)
        self.assertEqual(health['sample_pages'], 1)
        self.assertEqual(health['by_status'], {'draft': 3, 'verified': 1, 'conflicted': 0, 'outdated': 0})
        # orphan.md updated 180 days before TODAY (not stale); stale.md 181 days (stale)
        self.assertEqual((TODAY - dt.date(2026, 3, 21)).days, 180)
        self.assertEqual(health['stale_pages'], ['wiki/concepts/stale.md'])
        self.assertEqual(health['orphan_pages'], [])
        self.assertEqual(health['errors']['total'], 0)
        for key in ('broken_links', 'missing_sources', 'warnings'):
            self.assertIn(key, health)

    def test_orphan_self_link_and_source_metadata_do_not_count(self):
        self.write('wiki/concepts/lonely.md', page(title='lonely', body='[[wiki/concepts/lonely]] [[raw/web/source.md]]'))
        result, _ = self.lint()
        orphans = [w['path'] for w in result['warnings'] if w['code'] == 'ORPHAN_PAGE']
        self.assertEqual(orphans, ['wiki/concepts/lonely.md'])

    def test_health_reports_errors_with_exit_1(self):
        self.write('wiki/concepts/a.md', page(sources=('raw/web/none.md',), body='[[nowhere]]'))
        code, out, _ = self.cli('health', '--json')
        health = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(health['missing_sources'], 1)
        self.assertEqual(health['broken_links'], 1)

    def test_health_brief_lists_inbox_recent_open_items_and_unlinked_raw(self):
        self.write('00 Inbox/Clips/new.md', '# clip\n')
        self.write('raw/web/unused.md', 'not cited\n')
        self.write('wiki/concepts/a.md', page(title='a', updated='2026-10-01',
                                              body='본문.\n\n- 확인 필요: 마감일\n- 확인 필요: 인원\n'))
        self.write('wiki/concepts/b.md', page(title='b', updated='2026-10-03', sources=(),
                                              body='[[raw/web/source]] 참고 9999건.'))
        self.write('wiki/concepts/s.md', page(title='s', extra='sample: true\n', updated='2026-10-09',
                                              body='- 확인 필요: 샘플\n'))
        before = self.snapshot()
        code, out, _ = self.cli('health', '--json')
        brief = json.loads(out)['brief']
        self.assertEqual(brief['inbox_pending'], 1)
        self.assertEqual(brief['recently_updated'], [{'path': 'wiki/concepts/b.md', 'updated': '2026-10-03'},
                                                     {'path': 'wiki/concepts/a.md', 'updated': '2026-10-01'}])
        self.assertEqual(brief['open_items'], {'total': 2, 'top_pages': [{'path': 'wiki/concepts/a.md', 'count': 2}]})
        self.assertEqual(brief['raw_not_linked_from_wiki'], 1)
        self.assertEqual(brief['evidence'], {'not_found': 1, 'other_source': 0, 'found_in_outputs': 0})
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_title_and_alias_warn(self):
        self.write('wiki/concepts/a.md', page(title='Regression'))
        self.write('wiki/methods/b.md', page(title='회귀', extra='aliases:\n  - "regression"\n'))
        result, _ = self.lint()
        duplicates = sorted(w['path'] for w in result['warnings'] if w['code'] == 'DUPLICATE_TITLE')
        self.assertEqual(duplicates, ['wiki/concepts/a.md', 'wiki/methods/b.md'])

    def test_unsourced_number_heuristic(self):
        cases = {
            '응답자의 42%가 동의했다.': True,
            '2026年に導入された。': True,
            '응답자의 42%가 동의했다 [[raw/web/source.md]].': False,
            '응답자의 42%가 동의했다 (raw/web/source.md, p.3).': False,
            '응답자의 42%가 동의했다.[^1]\n\n[^1]: [[raw/web/source.md]] p.3': False,
            '- 응답자의 42%\n  - 근거: [원문](raw/web/source.md)': False,
            '1. 첫 단계\n2. 둘째 단계': False,
            '자세한 내용은 https://example.com/2026/report 참조.': False,
            '```\nx = 2026\n```': False,
            '[[2026-09-17 第1回 概要]] 참조.': False,
            # Open items say what is NOT known yet; they are not factual claims.
            '- 확인 필요: 28졸 募集要項와 2030中期経営計画의 중점.': False,
            '- 쓰기 전에 할 일: 2026년 3월기 결산 자료를 읽는다.': False,
            '- 역질문 방향(문안은 사용자가 만든다): 2030中期経営計画의 비중 변화.': False,
            '- 찾지 못한 것: 2026년 실적.': False,
            '- 매출은 8,147억 엔이다. 확인 필요: 증감.': True,
            '확인 필요 없이 42%가 동의했다.': True,
            # Japanese open-item labels (template vault)
            '- 要確認: 2026年度の募集人数。': False,
            '- 見つからなかったこと: 2026年の実績。': False,
            '- 未解決の質問: 回答者の42%は誰か。': False,
            '- 売上は8,147億円である。要確認: 増減。': True,
        }
        self.write('2026-09-17 第1回 概要.md', '# x')
        for body, expected in cases.items():
            with self.subTest(body):
                self.write('wiki/concepts/a.md', page(body=body))
                result, _ = self.lint()
                self.assertEqual('UNSOURCED_NUMBER' in self.codes(result, 'warnings'), expected)

    def test_graph_explicit_edges_backlinks_and_dedupe(self):
        self.build()
        target = Path(tempfile.mkdtemp()) / 'graph.json'
        self.addCleanup(lambda: target.exists() and target.unlink())
        code, _, _ = self.cli('graph', '--output', str(target))
        self.assertEqual(code, 0)
        graph = json.loads(target.read_text('utf-8'))
        edges = {(e['source'], e['target']) for e in graph['edges']}
        self.assertEqual(len(edges), len(graph['edges']))
        self.assertIn(('wiki/courses/stat.md', 'wiki/concepts/regression.md'), edges)
        self.assertIn(('wiki/concepts/regression.md', 'wiki/courses/stat.md'), edges)
        self.assertIn(('04 Maps/Index.md', 'wiki/concepts/stale.md'), edges)
        self.assertNotIn(('wiki/concepts/regression.md', 'wiki/concepts/regression.md'), edges)
        self.assertFalse(any(t.startswith('raw/') for _, t in edges))  # sources metadata is not an edge
        self.assertTrue(all(e['kind'] == 'explicit' for e in graph['edges']))
        self.assertEqual(graph['backlinks']['wiki/concepts/regression.md'], ['wiki/courses/stat.md'])
        kinds = {n['id']: n['kind'] for n in graph['nodes']}
        self.assertEqual(kinds['wiki/courses/stat.md'], 'course')
        self.assertEqual(kinds['04 Maps/Index.md'], 'legacy')
        self.assertEqual(self.cli('graph', '--output', str(target))[0], 2)

    def test_graph_output_cannot_overwrite_vault_content(self):
        for rel in ('wiki/graph.json', 'raw/graph.json', 'AGENTS.md', '03 Sources/graph.json', 'wiki/index.md'):
            with self.subTest(rel):
                (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
                self.assertEqual(self.cli('graph', '--output', str(self.root / rel))[0], 2)
        (self.root / 'outputs/.generated').mkdir(parents=True)
        self.assertEqual(self.cli('graph', '--output', str(self.root / 'outputs/.generated/g.json'))[0], 0)

    def test_read_only_commands_do_not_change_bytes(self):
        self.build()
        before = self.snapshot()
        self.cli('lint', '--json')
        self.cli('health', '--json')
        self.cli('graph')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / '.cache').exists())
        self.assertFalse((self.root / 'raw/manifest.json').exists())


class CliContractTests(VaultCase):
    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args, '--root', str(self.root)],
                              capture_output=True, text=True)

    def test_json_shape_and_exit_codes(self):
        proc = self.run_script('lint', '--json')
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(set(data), {'errors', 'warnings', 'stats'})
        for item in data['warnings']:
            self.assertEqual(set(item), {'code', 'path', 'line', 'message'})
        self.write('wiki/concepts/a.md', page(body='[[nowhere]]'))
        proc = self.run_script('lint', '--json')
        self.assertEqual(proc.returncode, 1)
        error = json.loads(proc.stdout)['errors'][0]
        self.assertEqual((error['code'], error['path'], error['line']), ('BROKEN_LINK', 'wiki/concepts/a.md', 12))
        self.assertEqual(self.run_script('lint', '--bogus').returncode, 2)
        missing = subprocess.run([sys.executable, str(SCRIPT), 'lint', '--root', str(self.root / 'nope')],
                                 capture_output=True, text=True)
        self.assertEqual(missing.returncode, 2)


class IndexCoverageTests(VaultCase):
    def warned(self, code):
        result, _ = self.lint()
        return [w['path'] for w in result['warnings'] if w['code'] == code]

    def test_wiki_page_missing_from_index(self):
        self.write('wiki/concepts/a.md', page(title='a'))
        self.write('wiki/concepts/b.md', page(title='b'))
        self.write('wiki/concepts/s.md', page(title='s', extra='sample: true\n'))
        self.write('wiki/index.md', '# Wiki\n\n- [[wiki/concepts/a|가]] — 설명\n')
        self.assertEqual(self.warned('INDEX_MISSING'), ['wiki/concepts/b.md'])

    def test_markdown_link_and_bare_name_count_as_listed(self):
        self.write('wiki/concepts/a.md', page(title='a'))
        self.write('wiki/concepts/b c.md', page(title='b'))
        self.write('wiki/index.md', '# Wiki\n\n- [[a]]\n- [b](wiki/concepts/b%20c.md)\n')
        self.assertEqual(self.warned('INDEX_MISSING'), [])

    def test_link_inside_code_or_comment_does_not_count(self):
        self.write('wiki/concepts/a.md', page(title='a'))
        self.write('wiki/index.md', '# Wiki\n\n`[[wiki/concepts/a]]` <!-- [[wiki/concepts/a]] -->\n')
        self.assertEqual(self.warned('INDEX_MISSING'), ['wiki/concepts/a.md'])

    def test_legacy_note_missing_from_both_maps(self):
        self.write('04 Maps/Index.md', '# Index\n\n- [[05 Courses/2026-秋/科目/科目]]\n')
        self.write('05 Courses/2026-秋/科目/科目.md', '# 科目\n')
        self.write('05 Courses/2026-秋/科目/2026-10-01 第1回 導入.md', '# 第1回\n')
        self.write('99 Legacy/02 Knowledge/x.md', '# x\n')
        self.write('wiki/index.md', '# Wiki\n\n- [[99 Legacy/02 Knowledge/x]]\n')
        self.write('90 System/tasks/todo.md', '# todo\n')
        self.assertEqual(self.warned('LEGACY_INDEX_MISSING'), ['05 Courses/2026-秋/科目/2026-10-01 第1回 導入.md'])

    def test_no_index_file_means_no_coverage_warning(self):
        (self.root / 'wiki/index.md').unlink()
        self.write('wiki/concepts/a.md', page(title='a'))
        self.assertEqual(self.warned('INDEX_MISSING'), [])


class EvidenceTests(VaultCase):
    RAW = ('# 공식 페이지\n\n従業員数（連結） ２,４６７名。売上収益 23,469億円。\n'
           '応募締切：2026年9月14日（月）23:59\n'
           '「AIを取り入れるべき範囲を見極める」という説明。\n')

    def setUp(self):
        super().setUp()
        self.write('raw/web/official.md', self.RAW)
        self.write('raw/web/other.md', '# 다른 기록\n\n月給：413,000円（基本給324,000 円）＋賞与\n')

    def evidence(self, body, sources=('raw/web/official.md', 'raw/web/other.md'), only=None):
        self.write('wiki/entities/x.md', page(title='x', sources=sources, body=body))
        linter = wiki_lint.Linter(self.root, TODAY)
        linter.run()
        return wiki_lint.EvidenceChecker(linter).run(only)

    def found(self, result):
        return [(f['code'], f['kind'], f['value']) for f in result['findings']]

    def test_normalized_numbers_dates_and_quotes_are_found(self):
        body = ('- 従業員数 2467名, 売上収益 23469億円 — 근거: [[raw/web/official|공식]]\n'
                '- 마감 2026-09-14 — 근거: [[raw/web/official|공식]]\n'
                '- 설명 「AIを取り入れるべき範囲を見極める」 — 근거: [[raw/web/official|공식]]\n')
        result = self.evidence(body)
        self.assertEqual(result['findings'], [])
        self.assertEqual((result['stats']['number_found'], result['stats']['date_found'],
                          result['stats']['quote_found']), (2, 1, 1))

    def test_missing_value_is_reported_with_line(self):
        result = self.evidence('- 従業員数 2500名 — 근거: [[raw/web/official|공식]]\n')
        self.assertEqual(self.found(result), [('EVIDENCE_NOT_FOUND', 'number', '2500')])
        self.assertEqual(result['findings'][0]['line'], 13)
        self.assertEqual(result['findings'][0]['checked'], ['raw/web/official.md'])

    def test_value_in_another_page_source_is_other_source(self):
        result = self.evidence('- 月給 「月給：413,000円（基本給324,000 円）＋賞与」 — 근거: [[raw/web/official|공식]]\n')
        self.assertEqual(self.found(result), [('EVIDENCE_OTHER_SOURCE', 'quote', '月給：413,000円（基本給324,000 円）＋賞与')])
        self.assertEqual(result['findings'][0]['found_in'], ['raw/web/other.md'])

    def test_unit_without_link_uses_frontmatter_sources(self):
        result = self.evidence('従業員数は 2467名이다.\n', sources=('raw/web/official.md',))
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['stats']['number_found'], 1)

    def test_child_item_link_covers_parent(self):
        body = '- 売上収益 23469億円\n  - 근거: [[raw/web/official|공식]]\n'
        result = self.evidence(body, sources=('raw/web/other.md',))
        self.assertEqual(result['findings'], [])

    def test_skips_ai_marked_units_approx_numbers_hangul_quotes_and_open_items(self):
        body = ('- AI 계산: 증가율 12.4%\n'
                '- 시트는 2026-09-23에 지웠다 — 조정자 기록 — [[raw/web/official|공식]]\n'
                '- 매출은 약 9999억 엔, 계약 8888건 — 근거: [[raw/web/official|공식]]\n'
                '- 「일본과의 연결」 절을 본다 — 근거: [[raw/web/official|공식]]\n'
                '- 확인 필요: 2031년 계획 7777\n')
        result = self.evidence(body)
        self.assertEqual(self.found(result), [('EVIDENCE_NOT_FOUND', 'number', '8888')])
        stats = result['stats']
        self.assertEqual((stats['skipped_ai_marked_units'], stats['skipped_approx_numbers'],
                          stats['skipped_hangul_quotes'], stats['skipped_open_items']), (2, 1, 1, 1))

    def test_skips_japanese_ai_marked_units_and_open_items(self):
        body = ('- AI計算: 増加率 12.4%\n'
                '- AI解釈: 2030年までに 5555社に広がる\n'
                '- 契約は 8888件 — 根拠: [[raw/web/official|公式]]\n'
                '- 要確認: 2031年の計画 7777\n')
        result = self.evidence(body)
        self.assertEqual(self.found(result), [('EVIDENCE_NOT_FOUND', 'number', '8888')])
        stats = result['stats']
        self.assertEqual((stats['skipped_ai_marked_units'], stats['skipped_open_items']), (2, 1))

    def test_ellipsis_quote_pieces_and_japanese_brackets_kept(self):
        body = ('- 「従業員数（連結）…23,469億円」 — 근거: [[raw/web/official|공식]]\n'
                '- 「2026年9月14日（月）23:59」 — 근거: [[raw/web/official|공식]]\n'
                '- 「従業員数（連結） / 売上収益 23,469億円」 — 근거: [[raw/web/official|공식]]\n')
        result = self.evidence(body)
        self.assertEqual(result['findings'], [])

    def test_unbalanced_ascii_quotes_are_ignored(self):
        result = self.evidence('- 표기 "AIを取り入れ 그리고 "엉뚱한 구간 — 근거: [[raw/web/official|공식]]\n')
        self.assertNotIn('quote', [f['kind'] for f in result['findings']])

    def test_linked_outputs_are_checked_and_counted_apart(self):
        self.write('outputs/career/x/메모.md', '# 메모\n\n本人が整理した文の一部です。\n')
        body = ('- 「本人が整理した文の一部です」 — 근거: [[outputs/career/x/메모|메모]]\n'
                '- 「本人が整理した文の一部です」 — [[raw/web/official|공식]], [[outputs/career/x/메모|메모]]\n'
                '- 「整理していない別の文」 — 근거: [[outputs/career/x/메모|메모]]\n')
        result = self.evidence(body)
        self.assertEqual(self.found(result), [('EVIDENCE_NOT_FOUND', 'quote', '整理していない別の文')])
        self.assertEqual(result['findings'][0]['checked'], ['outputs/career/x/메모.md'])
        self.assertEqual(result['stats']['quote_found_in_outputs'], 2)
        self.assertNotIn('quote_found', result['stats'])

    def test_other_non_raw_evidence_is_unchecked(self):
        self.write('99 Legacy/x.md', '# legacy\n')
        result = self.evidence('- 「本人が整理した文の一部です」 — 근거: [[99 Legacy/x|legacy]]\n')
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['stats']['unchecked_units_non_raw_evidence'], 1)

    def test_numbers_inside_links_and_paths_are_ignored(self):
        body = '- 근거: [[raw/web/official|2026-10-03 기록 9876]] [링크](https://example.com/5555)\n'
        result = self.evidence(body)
        self.assertEqual(result['findings'], [])

    def test_invalid_page_is_counted_not_checked(self):
        self.write('wiki/entities/broken.md', '# no frontmatter 2500\n')
        result = self.evidence('- 従業員数 2467名 — 근거: [[raw/web/official|공식]]\n')
        self.assertEqual(result['stats']['skipped_invalid_pages'], 1)
        self.assertEqual(result['findings'], [])

    def test_read_only_page_filter_and_cli(self):
        self.write('wiki/index.md', '# Wiki\n\n- [[wiki/entities/x]]\n')
        self.evidence('- 従業員数 2500名 — 근거: [[raw/web/official|공식]]\n')
        before = self.snapshot()
        code, out, _ = self.cli('evidence', '--json')
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(set(data), {'findings', 'stats'})
        self.assertEqual(set(data['findings'][0]),
                         {'code', 'path', 'line', 'kind', 'value', 'checked', 'found_in'})
        code, out, _ = self.cli('evidence', '--page', 'wiki/entities/x.md')
        self.assertEqual(code, 0)
        self.assertIn('EVIDENCE_NOT_FOUND wiki/entities/x.md:13 number 2500', out)
        self.assertIn('not fact verification', out)
        self.assertEqual(self.cli('evidence', '--page', 'wiki/nope.md')[0], 2)
        self.assertEqual(self.snapshot(), before)


class ReviewAfterTests(VaultCase):
    def review_warnings(self, result):
        return sorted(w['path'] for w in result['warnings'] if w['code'] == 'REVIEW_DUE')

    def test_review_after_is_an_optional_date_not_before_created(self):
        self.write('wiki/concepts/early.md', page(title='early', extra='review_after: 2026-08-01\n'))
        self.write('wiki/concepts/text.md', page(title='text', extra='review_after: "soon"\n'))
        self.write('wiki/concepts/ok.md', page(title='ok', extra='review_after: 2026-10-01\n'))
        result, _ = self.lint()
        invalid = sorted(e['path'] for e in result['errors'] if e['code'] == 'INVALID_FIELD')
        self.assertEqual(invalid, ['wiki/concepts/early.md', 'wiki/concepts/text.md'])
        self.assertNotIn('UNKNOWN_FIELD', self.codes(result, 'warnings'))

    def test_review_due_warns_from_the_date_itself_and_skips_samples(self):
        self.write('wiki/concepts/past.md', page(title='past', extra='review_after: 2026-09-16\n'))
        self.write('wiki/concepts/today.md', page(title='today', extra='review_after: 2026-09-17\n'))
        self.write('wiki/concepts/future.md', page(title='future', extra='review_after: 2026-09-18\n'))
        self.write('wiki/concepts/sample.md', page(title='sample', extra='sample: true\nreview_after: 2026-09-10\n'))
        result, _ = self.lint()
        self.assertEqual(self.review_warnings(result), ['wiki/concepts/past.md', 'wiki/concepts/today.md'])
        self.assertEqual(self.review_warnings(self.lint(today=dt.date(2026, 9, 15))[0]), [])

    def test_health_brief_lists_review_due_and_soon(self):
        self.write('wiki/concepts/due.md', page(title='due', extra='review_after: 2026-09-10\n'))
        self.write('wiki/concepts/soon.md', page(title='soon', extra='review_after: 2026-10-01\n'))
        self.write('wiki/concepts/later.md', page(title='later', extra='review_after: 2026-10-02\n'))
        code, out, _ = self.cli('health', '--json')
        brief = json.loads(out)['brief']
        self.assertEqual(brief['review_due'], [{'path': 'wiki/concepts/due.md', 'review_after': '2026-09-10'}])
        self.assertEqual(brief['review_soon'], [{'path': 'wiki/concepts/soon.md', 'review_after': '2026-10-01'}])


if __name__ == '__main__':
    unittest.main()
