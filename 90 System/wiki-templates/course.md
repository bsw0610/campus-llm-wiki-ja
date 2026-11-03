---
title: "{{title}}"
type: "course"
status: "draft"
sources: []
created: {{date:YYYY-MM-DD}}
updated: {{date:YYYY-MM-DD}}
confidence: "low"
aliases: []
legacy_sources: []
---

# {{title}}

%%
course = 一つの科目で学んだ知識を、学期が終わった後も再利用するためのハブ。
- 05 Courses に科目ホームがある間は、同じ科目のページを新しく作らない。学期が終わって科目をアーカイブへ移すとき、または本人が求めたときに作り、既存の科目ホームを legacy_sources にリンクする。
- 授業・課題・試験の日程の原本は Google Calendar。日付をここにコピーしない。
- 講義資料の原本(PPT/PDF)は Google Drive、論文は Zotero に置く。ここには場所へのリンクだけを置く。
- 教員の説明、講義資料の内容、AI補足を区別して書く。
- 概念の説明はこのページに長く書かず、wiki/concepts のページを再利用してリンクする。
- legacy_sources には legacy_roots(例: 05 Courses)内の実在するファイルのパスだけを入れる。
- sources が [] の draft は NO_SOURCES 警告が正常。
- この案内コメントは書き終えたら消してよい。
%%

## 科目情報

- 学期:
- 既存の科目ホーム: (legacy の科目ホームへのリンク。なければ空のまま)
- 講義資料の場所: (Google Drive のリンク、または「未連携」)
- 日程: Google Calendar で調べる。このページに日付をコピーしない。
- 評価方法: — 根拠: (シラバスの raw のパスと位置)

## 学習目標

- (シラバス・初回授業に基づく) — 根拠: (raw のパスと位置)

## 主要概念

- (concept ページへのリンク) — この科目でどんな文脈で出てくるかを1文で

## 回ごとの要約

- 第N回 (テーマ) — 教員の説明: / 講義資料: / AI補足: — 根拠: (raw のパスとページ・スライド番号)

## 課題・試験の状態

- [ ] (課題名) — 状態だけを書く。締切は Calendar。

## 試験対策

- 出題範囲の根拠: (raw のパスと位置)
- 弱い部分:
- Anki カード候補: (wiki の内容から作った候補のみ。Anki は source ではない)

## 未解決の質問

- 要確認:
