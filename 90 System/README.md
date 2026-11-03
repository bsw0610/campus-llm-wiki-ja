# 90 System — 使い方の詳細

エージェントのルールは [[AGENTS]]（ルート）にある。この文書は人間が読む使い方と、lint の読み方の説明である。

## 1. 目的と原則

この Vault は「AI に要約させて終わり」にしないための仕組みである。

- **原文は変えない。** 集めた資料は `raw/` に登録し、SHA-256 で変化を検出する。
- **主張には根拠を付ける。** Wiki の数字・日付・引用の横に、raw のどの位置かを書く。
- **事実・AI の解釈・未確認を分ける。** `AI解釈:`、`要確認:` と書き分ける。
- **知識は一か所に。** 同じ概念を科目ごとに作らず、`aliases` でまとめて再利用する。
- **成果物は分ける。** 就活の準備や発表の資料は `outputs/` に置き、Wiki と混ぜない。

しないこと: ベクトル DB やサーバーを足す、AI の確信だけで「確認済み」にする、raw を書き換える。

## 2. 全体の流れ

```
ブラウザ・メモ・文字起こし
        │ Web Clipper / 手で置く
        ▼
00 Inbox/Clips ──「新しい資料を整理して」──▶ raw/（原文、変えない、manifest に登録）
                                              │
                                              ▼
                                     wiki/（根拠つきの再利用知識）
                                              │
                         ┌────────────────────┼────────────────────┐
                         ▼                    ▼                    ▼
             05 Courses/（授業ノート）  outputs/（就活・発表）    Anki などのカード
```

## 3. フォルダ

| フォルダ | 中身 |
|---|---|
| `00 Inbox/Clips` | 未処理の資料 |
| `raw/university/<科目名>` | 授業のメモ・文字起こし・講義資料の識別記録 |
| `raw/papers`, `raw/books` | 論文・本の書誌情報、読んだ範囲、抜粋 |
| `raw/web` | 興味で集めた記事・動画 |
| `raw/career` | 就活の記事・説明会の記録・企業調査の記録・選考体験談の調査記録 |
| `raw/research`, `raw/projects/<名前>` | ゼミ・研究・個人開発の事実記録（コードの原本はリポジトリ） |
| `raw/personal` | 本人の経験の記録（本人の陳述） |
| `wiki/concepts`, `wiki/methods` | 科目をまたいで使う概念・方法 |
| `wiki/entities` | 企業・組織・人物・サービス |
| `wiki/syntheses` | 複数の資料をまとめた整理（例: 経験インベントリ） |
| `wiki/decisions`, `wiki/projects`, `wiki/courses`, `wiki/sources` | 決定、プロジェクトの入口、科目のハブ、必要なときだけの資料要約 |
| `05 Courses/<学期>/<科目名>` | 科目ホーム、授業ノート |
| `outputs/career/<会社>`, `outputs/career/_共通` | 企業ごとの準備メモ、応募状況 |
| `90 System` | ルール、テンプレート、ログ、スクリプト、テスト |

## 4. raw / wiki / outputs の違い

| | raw | wiki | outputs |
|---|---|---|---|
| 中身 | 原文・抽出・文字起こし | 根拠つきの知識 | 就活の準備・発表 |
| 書き換え | しない（訂正は新バージョン） | する（更新日を変える） | する |
| 根拠になるか | なる | ならない（外部事実の独立した証拠ではない） | ならない |

## 5. 最初の資料を取り込む

1. `90 System/PROFILE.md` を埋める（空欄があってもよい）。
2. 記事を Web Clipper で保存するか、Markdown を `00 Inbox/Clips` に置く。
3. エージェントに `新しい資料を整理して` と頼む。
4. エージェントが変更一覧を見せ、raw 登録・Wiki 更新・index 更新・lint まで行う。
5. `wiki/index.md` から新しいページを開いて確かめる。

### テンプレートでページを作る

Obsidian の「テンプレート」コアプラグインのフォルダは `90 System/wiki-templates` に設定済み。新しいノートで「テンプレートを挿入」から type を選ぶ。授業用の `Course Home`・`Lecture Note` は `90 System/Templates` にある。

## 6. エージェントへの命令の例

| 言い方 | 何が起きるか |
|---|---|
| `新しい資料を整理して` | Inbox を Analyze → Compile で処理 |
| `新しい資料を確認して` | Analyze だけ（ファイルは変えない） |
| `今日の授業をまとめて` | メモから授業ノートを作る |
| `試験対策して <科目>` | 授業ノートから重要概念・予想問題・弱点 |
| `query <質問>` | Vault の中だけで答える（根拠つき） |
| `research <質問>` | 足りなければウェブで調べ、記録を raw に残して答える |
| `review <ページ>` | 根拠・重複・矛盾を点検して報告（直さない） |
| `lint` | 構造の点検 |

## 7. query と research の違い

- `query` は Vault の中の知識だけで答える。根拠がない部分は「Vault に根拠なし」と言う。
- `research` はその質問についての外部調査の依頼とみなし、調べた URL・アクセス日・読んだ範囲・短い抜粋を `raw/` に登録してから答える。既存の根拠で足りればウェブは使わない。

## 8. lint

標準ライブラリだけを使う `90 System/scripts/wiki_lint.py` で点検する。インストールするパッケージはない。Vault のルートで実行する。

```sh
python3 "90 System/scripts/wiki_lint.py" lint --root .
python3 "90 System/scripts/wiki_lint.py" health --root . --json
python3 "90 System/scripts/wiki_lint.py" evidence --root .
python3 "90 System/scripts/wiki_lint.py" graph --root .
python3 "90 System/scripts/wiki_lint.py" register-raw raw/web/example.md --root .
(cd "90 System" && python3 -m unittest discover -s tests)
```

どのサブコマンドも `--help` でオプションを見られる。

### 何を点検するか

- `wiki/` のページの frontmatter（必須フィールド、type/status/confidence の値、日付）、空のページ、壊れた・曖昧なリンク、`sources` のパスが存在するか
- `raw/` のファイルが登録時の SHA-256 と同じか、削除・未登録のファイルがないか
- 孤立したページ、タイトル・別名の重複候補、近くに根拠リンクがない数字・日付、180日以上更新されていないページ
- index の漏れ（`wiki/index.md` にない Wiki ページや科目フォルダのノート。`90 System` は見ない）

### 結果の読み方

- **終了コード:** `0` = エラーなし（警告はありうる）、`1` = データのエラーあり、`2` = 使い方・設定・実行のエラー。
- **直すべきエラー:** `BROKEN_LINK`, `AMBIGUOUS_LINK`（同名ファイルが複数 → Vault のパスでリンク）, `MISSING_SOURCE`, `MISSING_FIELD`, `INVALID_FIELD`, `INVALID_FRONTMATTER`, `EMPTY_PAGE`, `PATH_ESCAPE`, `VERIFIED_WITHOUT_SOURCES`, `RAW_MODIFIED`, `RAW_MISSING`, `MANIFEST_INVALID`。
- **確認すべき警告:** `NO_SOURCES`（根拠のない draft。途中の状態としては正常）, `UNSOURCED_NUMBER`（数字・日付の近くに根拠リンクがない。`- 要確認:`、`- 見つからなかったこと:`、`- 未解決の質問:` のような「まだ分からないこと」の行は対象外）, `ORPHAN_PAGE`, `DUPLICATE_TITLE`, `STALE_PAGE`, `RAW_UNREGISTERED`, `WEAK_CONFIDENCE`, `UNKNOWN_FIELD`, `REVIEW_DUE`（`review_after` の日が来た）, `INDEX_MISSING`, `LEGACY_INDEX_MISSING`。
- **`health`** は件数の要約と、セッションの始めに見る短い `brief`（Inbox の待ち件数、最近の更新、見直し日の近いページ、未解決の項目数など）を出す。
- **`graph`** は本文の明示的な wikilink だけを辺にする。出力先は Vault の外の新しいファイルか、Vault 内なら `.cache/` か `outputs/.generated/` の下だけ。

### evidence — 原文との文字列照合

`evidence` は Wiki ページの日付・数字・直接引用が、**その行がリンクした raw ファイルの中に文字列として実際にあるか**を探す読み取り専用の報告である。

- `EVIDENCE_NOT_FOUND`: リンクした raw にもページのほかの raw にもない。原文を開いて確かめる候補（換算・翻訳・要約の表記差のこともある）。
- `EVIDENCE_OTHER_SOURCE`: その行のリンク先にはなく、同じページの別の raw にある。リンク先の間違いが多い。
- `AI解釈:` などの行、`約`・`およそ` の付いた数字、「要確認」の行は照合しない。
- 見つかっても、原文がその主張を支えているとは限らない（同じ数字が別の文脈のこともある）。

### lint の通過 ≠ 事実の検証

lint は**構造**だけを見る。リンク先があり、ファイルが変わっておらず、形式が正しいかを確かめるだけで、原文が主張を本当に支えているか、内容が最新かは判断できない。`status: "verified"` に上げるには、人が（または `review` で）主な主張と原文の位置を照らし合わせる。

### raw の登録

- `register-raw` は `raw/` の中のファイル1つを**新しく**登録するだけ。同じファイル・同じハッシュで再実行しても manifest は変わらない。
- 登録済みファイルの中身が変わっていれば登録は拒否され、元の基準値が残る。上書きするオプションはない。新しいバージョンのファイルとして登録する。
- manifest と Git で変更を追えるが、改ざんを防ぐセキュリティ装置ではない。

## 9. Wiki ページの書き方

1. **先に探し、なければ作る。** title と `aliases` で既存の concept/method を探す。日本語・英語の表記は `aliases` でまとめる。
2. **根拠は主張の横に。** 数字・日付・直接引用・研究/統計の結果・企業の事実・本人の測定値は、その文か表の行に原文へのリンクと位置（ページ、節、タイムスタンプ、結果ファイル、コミット）を書く。
3. **出典の種類を分ける。** 出典が述べた事実 / 本人の陳述 / AI解釈 / 要確認。説明会の発言は「そういう発言があった」という記録で、内容が事実である証拠ではない。
4. **推測で埋めない。** 読んだ範囲だけまとめ、欠けているものは欠けていると書く。
5. **status と confidence は根拠でだけ上げる**（[[AGENTS]]「ページのスキーマと確信度」）。
6. **矛盾は上書きしない。** 期間・対象・定義が同じかを先に確かめ、両方の主張と根拠を残して `conflicted` にする。
7. **リンクには理由を。** `[[ページ]] — なぜ関連するか一文`。同名ファイルがありうるので `wiki/concepts/回帰分析` のように Vault のパスでリンクしてもよい。
8. **ほどよい大きさで。** 細かく分けすぎず、長い自動要約をそのまま積まない。

### frontmatter の形式

点検ツールが読めるのは YAML 全体ではなく、次の部分集合である。

```yaml
---
title: "回帰分析"
type: "concept"
status: "draft"
sources:
  - "raw/university/社会統計学/2026-10-01 第2回 講義メモ.md"
created: 2026-10-01
updated: 2026-10-01
confidence: "low"
aliases:
  - "regression analysis"
courses: []
---
```

- 文字列とリストの項目は**ダブルクォート**、空のリストは `[]`、日付は `YYYY-MM-DD`、リストはちょうど2スペースの字下げ `  - "..."`。
- シングルクォート、クォートなしの文字列、`[a, b]` 形式のリスト、入れ子、複数行の値、タブはエラーになる。
- 必須: `title`, `type`, `status`, `sources`, `created`, `updated`, `confidence`。任意: `aliases`, `courses`, `projects`, `legacy_sources`, `supersedes`, `confidence_reason`, `sample`, `review_after`（締切・選考日程のようにすぐ古くなる事実があるページだけ）。
- `sources` には `raw/` に実在するファイルだけを入れる。URL は raw のノートの中に書く。
- `sample: true` は例のページの印で、`health` の件数から外れる。同梱の例のページは中身を理解したら消してよい。

## 10. ほかのツールとの分担

- **カレンダー:** 授業・課題・選考の日程の原本。Vault には書き写さない。
- **クラウドストレージ:** 講義資料の原本。Vault にはリンクだけ。
- **Zotero など:** 論文 PDF と書誌情報の原本。Vault には読んだ論文の要約ノート。
- **Anki など:** 授業ノートの「暗記カード候補」から本人が作る。Anki は出典ではなく出力先。
- **GitHub:** コードとデータの原本。Vault には事実記録と要約だけ（`raw/projects/<名前>/YYYY-MM-DD 事実記録.md` → `wiki/projects/<名前>.md`）。

## 11. Git と公開

- Vault 全体を Git で管理すると、raw の変更と Wiki の履歴を追える。
- **この Vault を自分用に使い始めたら、公開リポジトリには push しない。** 応募状況・本人の経験が入る。公開したいときは、新しいフォルダに仕組み（ルール・スクリプト・テンプレート）だけをコピーして、新しいリポジトリとして始める。
- 大きな PDF・動画は Vault に入れない（原本はクラウドに置く）。

## 12. バックアップ

- Git のリモート（**非公開**リポジトリ）か、クラウド同期のどちらかを使う。両方を同じフォルダに重ねると衝突しやすい。
- `raw/manifest.json` は raw の変化を検出する基準なので、raw と一緒にバックアップする。
