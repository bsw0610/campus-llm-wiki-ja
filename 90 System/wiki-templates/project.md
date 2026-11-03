---
title: "{{title}}"
type: "project"
status: "draft"
sources: []
created: {{date:YYYY-MM-DD}}
updated: {{date:YYYY-MM-DD}}
confidence: "low"
aliases: []
courses: []
legacy_sources: []
---

# {{title}}

%%
project = 終わりのある作業(ゼミ研究、個人開発、就職活動など)の入口ページ。
- プロジェクトごとにこのページを1つ。他のページはこのページをリンクし、プロジェクトの事実を再び書かない。
- コード・データ・作業ログはリポジトリに置き、コピーしない。作業ログやToDo一覧をそのまま写さない。
- sources には raw/projects/<名前>/ の事実記録を入れる。経緯や数字は最新の事実記録の節にリンクし、ここに再び書かない。
- 結果の数値は結果ファイルのパス・コードのバージョン(コミット)と併記する。文ごとに根拠の節を付ける。
- kind・phase・repo・pinned_commit・predecessor・career_use は frontmatter に入れない(lint UNKNOWN_FIELD、パス項目に URL を入れると INVALID_FIELD)。下の「プロジェクト情報」の表に置く。
- [USER](本人の陳述)・[INTERP](AI解釈)だけでは verified や confidence medium/high に上げない。
- 次の事実記録を受け取る日が決まっているときは、frontmatter に `review_after: YYYY-MM-DD` を入れる(その日から lint が REVIEW_DUE 警告を出す)。値がなければ行ごと入れない。
- sources が [] の draft は NO_SOURCES 警告が正常。
- この案内コメントは書き終えたら消してよい。
%%

## プロジェクト情報

| 項目 | 値 |
|---|---|
| kind | seminar / personal-dev |
| phase | active / paused / done |
| repo | ローカルパス: / 公開 URL: |
| pinned_commit | (最新の事実記録の基準コミット) |
| predecessor | (前学期・前バージョンのプロジェクトへのリンク。なければ「なし」) |
| career_use | 就活で使える / 面接でのみ / 使わない — 理由: |

## 一行説明

-

## 現在の状態と次にやること

%% 新しいセッションがこの節だけ読んで始められること。文ごとに根拠の節を付ける。 %%

- 状態:
- 次にやること:

## 経緯

- (最新の事実記録の経緯の節へのリンク) — 1〜2文の案内だけ。再び書かない。

## 数字と帰属

- (最新の事実記録の数字の表へのリンク) — 値・誰の判断か・比較の基準はその表に従う。

## AIがしたこと / 自分がしたこと

- AI:
- 本人:

## ここから生まれた再利用知識

- (wiki/methods、wiki/concepts へのリンク) — このプロジェクトからどう生まれたかを1文で

## 決定

- (wiki/decisions へのリンク) — 他のプロジェクトや就活に影響する決定のみ。なければ「なし」

## 成果物

- (outputs のパスまたはリポジトリへのリンク) — 就活の準備、発表。何か、どの版か

## 関連する企業

- (wiki/entities へのリンク) — その会社の選考で実際に使う場合のみ

## 弱点と使ってはいけない表現

- 先に言うべき弱点:
- 使ってはいけない表現:

## 未確認

- 要確認:
