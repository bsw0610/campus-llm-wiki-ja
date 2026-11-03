---
title: "{{title}}"
type: "concept"
status: "draft"
sources: []
created: {{date:YYYY-MM-DD}}
updated: {{date:YYYY-MM-DD}}
confidence: "low"
aliases: []
courses: []
projects: []
---

# {{title}}

%%
書く前の確認
- wiki/concepts と wiki/methods で title と aliases から同じ概念を先に探す。あれば新規ページを作らず既存ページを更新する。
- 科目ごとに同じ概念を別々に作らない。別の科目で再び出てきたら courses に科目のパスを追加するだけにする。
- aliases には同じ概念の別表記を入れる。例:
    aliases:
      - "回帰分析"
      - "regression analysis"
- courses/projects/sources は Vault 相対パスのリスト。空なら [] のままにする。
- sources が [] の draft は lint で NO_SOURCES 警告が出るのが正常。警告を消すために根拠を作らない。
- confidence を medium/high に上げるときは confidence_reason: "..." を追加する。出典の数や AI の確信だけで上げない。
- 根拠表記の例: 主張の文末に `— 根拠: [[raw/university/科目/ファイル|表示名]] p.12` または `§2.1`、`00:14:30`。
- この案内コメントは書き終えたら消してよい。
%%

## 定義

- (1〜2文の定義) — 根拠: (raw のパスとページ/節/タイムスタンプ)

## 要点

- (出典が述べた内容) — 根拠: (raw のパスと位置)
- AI解釈: (原文からそのまま出ない整理・比較。根拠にした原文の位置も書く)

## 文脈と適用範囲

- どの学問・科目・分析場面でこの意味で使うか:
- 定義が変わる場合や、混同しやすい概念:

## 例

- (原文に出た例だけを書く。自分で作った例は「AI例」または「本人の例」と表示する)

## 関連ページ

- (関連ページへのリンク) — なぜ関連するかを1文で

## 未解決の質問

- 要確認:
