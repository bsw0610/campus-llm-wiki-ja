"""T10: isolated end-to-end checks of the ingest workflow's structural guarantees.

`compile_sample` and `apply_conflict` are scripted stand-ins for what an agent does in the
Compile pass. They prove that the layout, raw registration, lint, health and graph fit
together; they do not prove that an LLM ingest is automated or semantically correct.
Everything lives in a TemporaryDirectory and every knowledge-like page is `sample: true`.
"""
import datetime as dt
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts import wiki_lint
from scripts.init_layout import initialize

TODAY = dt.date(2026, 9, 17)
RAW = 'raw/university/sample-course/sample-lecture-01.md'
RAW_NEW = 'raw/university/sample-course/sample-lecture-02.md'
COURSE = 'wiki/courses/sample-course.md'
CONCEPT = 'wiki/concepts/sample-sampling-error.md'

RAW_TEXT = """# SAMPLE lecture 01 (test fixture, not real course material)

## 1. 標本誤差
2026-04-10 の講義。標本誤差は標本から母集団を推定するときに生じる誤差である。

## 2. 信頼区間
標本サイズが 400 のとき、95% 信頼区間の幅は約 ±5 ポイントと説明された。

## 3. 無作為抽出
無作為抽出は各個体が等しい確率で選ばれる抽出方法である。
"""
RAW_NEW_TEXT = """# SAMPLE lecture 02 (test fixture, not real course material)

## 1. 信頼区間の訂正
標本サイズが 400 のとき、95% 信頼区間の幅は約 ±4 ポイントと説明された。
"""

ORIGINAL_CLAIM = ('- SAMPLE: 標本サイズ 400 のとき 95% 信頼区間の幅は約 ±5 ポイント '
                  f'— 근거: [[{RAW[:-3]}|sample lecture 01]] §2')
NEW_CLAIM = ('- SAMPLE: 標本サイズ 400 のとき 95% 信頼区間の幅は約 ±4 ポイント '
             f'— 근거: [[{RAW_NEW[:-3]}|sample lecture 02]] §1')


def frontmatter(title, kind, sources, status='draft', extra=''):
    lines = ''.join(f'  - "{s}"\n' for s in sources)
    return (f'---\ntitle: "{title}"\ntype: "{kind}"\nstatus: "{status}"\nsources:\n{lines}'
            f'created: 2026-09-17\nupdated: 2026-09-17\nconfidence: "low"\nsample: true\n{extra}---\n\n')


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        initialize(self.root)
        self.write('wiki-config.json', json.dumps(wiki_lint.DEFAULT_CONFIG))
        self.write('wiki/index.md', '# SAMPLE wiki index\n')
        self.write('wiki/log.md', '# SAMPLE log\n')
        self.write(RAW, RAW_TEXT)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))

    def read(self, rel):
        return (self.root / rel).read_text('utf-8')

    def cli(self, *args):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            code = wiki_lint.main([*args, '--root', str(self.root), '--today', TODAY.isoformat()])
        return code, out.getvalue()

    def lint(self):
        code, out = self.cli('lint', '--json')
        return code, json.loads(out)

    def codes(self, result, kind='errors'):
        return [item['code'] for item in result[kind]]

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(self.root.rglob('*')) if p.is_file() and '.cache' not in p.parts}

    def append_once(self, rel, line):
        if line not in self.read(rel).splitlines():
            self.write(rel, self.read(rel).rstrip('\n') + '\n' + line + '\n')

    def compile_sample(self):
        """Register the raw file, then create one course and one concept page. Re-runnable."""
        code, _ = self.cli('register-raw', RAW)
        self.assertEqual(code, 0)
        if not (self.root / CONCEPT).exists():
            self.write(CONCEPT, frontmatter('SAMPLE 標本誤差', 'concept', [RAW], extra='aliases:\n  - "sampling error"\n')
                       + '# SAMPLE 標本誤差\n\n## 정의\n\n'
                       f'- SAMPLE: 標本から母集団を推定するときに生じる誤差 — 근거: [[{RAW[:-3]}|sample lecture 01]] §1\n\n'
                       '## 핵심 포인트\n\n' + ORIGINAL_CLAIM + '\n')
        if not (self.root / COURSE).exists():
            self.write(COURSE, frontmatter('SAMPLE course', 'course', [RAW])
                       + '# SAMPLE course\n\n## 회차\n\n'
                       f'- SAMPLE: 2026-04-10 講義 — 근거: [[{RAW[:-3]}|sample lecture 01]] §1\n\n'
                       '## 개념\n\n'
                       f'- [[{CONCEPT[:-3]}|SAMPLE 標本誤差]] — 이 회차에서 처음 정의한 개념이라 연결한다.\n')
        self.append_once('wiki/index.md', f'- [[{COURSE[:-3]}|SAMPLE course]] — sample fixture')
        self.append_once('wiki/log.md', '- 2026-09-17 SAMPLE: sample lecture 01 ingest (course 1, concept 1)')

    def apply_conflict(self):
        """Second source disagrees on a number: keep both claims and mark the page conflicted."""
        self.write(RAW_NEW, RAW_NEW_TEXT)
        self.assertEqual(self.cli('register-raw', RAW_NEW)[0], 0)
        text = self.read(CONCEPT)
        text = text.replace('status: "draft"', 'status: "conflicted"').replace(
            f'sources:\n  - "{RAW}"\n', f'sources:\n  - "{RAW}"\n  - "{RAW_NEW}"\n')
        text += ('\n## 충돌\n\n' + NEW_CLAIM + '\n'
                 '- 미해결 질문: 두 회차의 조건(모비율 가정)이 같은지 확인 필요.\n')
        self.write(CONCEPT, text)

    # A/B/C
    def test_ingest_registers_raw_and_links_claims_without_lint_errors(self):
        self.compile_sample()
        code, result = self.lint()
        self.assertEqual((code, result['errors']), (0, []))
        self.assertNotIn('UNSOURCED_NUMBER', self.codes(result, 'warnings'))
        self.assertNotIn('RAW_UNREGISTERED', self.codes(result, 'warnings'))
        self.assertEqual((result['stats']['raw_files'], result['stats']['raw_registered']), (1, 1))
        # One input does not force a source-summary or a concept per heading.
        self.assertEqual(list((self.root / 'wiki/sources').iterdir()), [])
        pages = sorted(p.relative_to(self.root).as_posix() for p in (self.root / 'wiki').rglob('*.md')
                       if p.name not in ('index.md', 'log.md'))
        self.assertEqual(pages, [CONCEPT, COURSE])
        for rel in pages:
            self.assertIn('sample: true', self.read(rel))
            self.assertIn('status: "draft"', self.read(rel))
            self.assertIn('confidence: "low"', self.read(rel))
        health = json.loads(self.cli('health', '--json')[1])
        self.assertEqual((health['pages'], health['sample_pages']), (0, 2))

    # D
    def test_broken_source_is_detected_and_restoring_it_clears_the_error(self):
        self.compile_sample()
        good = self.read(CONCEPT)
        self.write(CONCEPT, good.replace(f'  - "{RAW}"', '  - "raw/university/sample-course/missing.md"'))
        code, result = self.lint()
        self.assertEqual(code, 1)
        self.assertIn('MISSING_SOURCE', self.codes(result))
        self.write(CONCEPT, good)
        self.assertEqual(self.lint()[1]['errors'], [])

    # E
    def test_course_and_concept_are_navigable_both_ways_without_forced_backlink_text(self):
        self.compile_sample()
        code, out = self.cli('graph')
        graph = json.loads(out)
        self.assertEqual(code, 0)
        self.assertIn({'source': COURSE, 'target': CONCEPT, 'kind': 'explicit'}, graph['edges'])
        self.assertIn(COURSE, graph['backlinks'][CONCEPT])
        self.assertNotIn('sample-course|', self.read(CONCEPT))
        self.assertNotIn({'source': CONCEPT, 'target': COURSE, 'kind': 'explicit'}, graph['edges'])

    def test_rerunning_the_same_input_creates_no_duplicates(self):
        self.compile_sample()
        before = self.snapshot()
        self.compile_sample()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.read('wiki/log.md').count('sample lecture 01 ingest'), 1)

    def test_conflicting_source_keeps_the_original_claim_and_marks_conflicted(self):
        self.compile_sample()
        raw_before = (self.root / RAW).read_bytes()
        self.apply_conflict()
        text = self.read(CONCEPT)
        self.assertIn(ORIGINAL_CLAIM, text)
        self.assertIn(NEW_CLAIM, text)
        self.assertIn('status: "conflicted"', text)
        self.assertEqual((self.root / RAW).read_bytes(), raw_before)
        code, result = self.lint()
        self.assertEqual((code, result['errors']), (0, []))
        health = json.loads(self.cli('health', '--json')[1])
        self.assertEqual(health['sample_pages'], 2)

    def test_raw_change_after_registration_is_detected_and_not_rebaselined(self):
        self.compile_sample()
        manifest = (self.root / 'raw/manifest.json').read_bytes()
        self.write(RAW, RAW_TEXT + '\n수정된 줄\n')
        code, result = self.lint()
        self.assertEqual(code, 1)
        self.assertIn('RAW_MODIFIED', self.codes(result))
        self.assertEqual(self.cli('register-raw', RAW)[0], 1)
        self.assertEqual((self.root / 'raw/manifest.json').read_bytes(), manifest)

    def test_read_only_commands_leave_the_sample_vault_unchanged(self):
        self.compile_sample()
        before = self.snapshot()
        for command in (('lint', '--json'), ('health', '--json'), ('graph',)):
            self.cli(*command)
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
