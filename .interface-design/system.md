# Factorioprints Monitor - Design System

A single-user tool for a Factorio blueprint publisher to **answer comments** and **track stats over time**. The product is a *comment inbox*, not a dashboard.

Reference mockups: `mockups/inbox.html`, `mockups/blueprints.html`, `mockups/blueprint.html`, `mockups/settings.html` (self-contained, open in a browser; nav links between them, theme/switcher/done toggles are interactive).

## Intent

- **Who:** a person who publishes blueprints on factorioprints.com and wants to (1) know when someone comments so they can reply, (2) see stats over time. 99.9% single-user.
- **Job:** triage new comments → reply on factorioprints → mark done. Everything else is secondary.
- **Feel:** Factorio's in-game GUI - warm industrial panels, high-contrast text, a single orange accent that *only* ever means "needs you." Functional and dense over moody.

## Themes

Two themes, both shipped, toggled by a single sun/moon icon button (no two-button control). Same hue family; only lightness shifts between surfaces.

**Dark** (Factorio panel):
```
--canvas #26241f  --panel #34312b  --panel-2 #3d3a33  --slot #1d1b17
--border rgba(255,250,235,0.11)  --border-soft .06  --border-strong .22
--text-1 #f4f1ea  --text-2 #c2bcae  --text-3 #8f897b
--accent #ff9d2e  --accent-text #ffb35a  --accent-soft rgba(255,157,46,0.14)  --on-accent #221f17
--good #97c84d  --good-text #b4dd6f  --good-soft rgba(151,200,77,0.15)
--grid rgba(255,250,235,0.07)
```
**Light** (engineering paper - crisp white panels, NOT muddy beige):
```
--canvas #edece7  --panel #ffffff  --panel-2 #f6f4ef  --slot #ecebe4
--border rgba(38,32,22,0.13)  --border-soft .07  --border-strong .26
--text-1 #23201a  --text-2 #5b554a  --text-3 #8f897b
--accent #e07d12  --accent-text #b3610a  --accent-soft rgba(224,125,18,0.12)  --on-accent #fff
--good #5a8a26  --good-text #436b1a  --good-soft rgba(90,138,38,0.13)
--grid rgba(38,32,22,0.09)
```

Rule: **orange = "needs you"** (needs-reply dot, awaiting badge, snapshot button) and nothing else. Green (`--good`) = handled/done. Gray builds all structure.

## Foundations

- **Depth:** borders-first, low-opacity. Surfaces shift ~4% lightness (canvas → panel → panel-2). Sidebar same family as canvas, separated by a 1px border (no different-colored "sidebar world").
- **Spacing:** 8px base.
- **Radius:** 6–8px controls/rows, 10–12px cards/wrappers.
- **Type:** system sans for UI/prose; **monospace tabular** (`.mono`) for every number, timestamp, count, and axis label - the in-game production-panel read.
- **Text hierarchy:** use all of text-1/2/3 (primary / supporting / metadata).
- **Icons:** thin line icons (stroke ~2), one set. Standalone icons get a slot/`--slot` container.

## Layout

App shell = **fixed left sidebar (224px) + main column**.

- **Sidebar:** brand mark (orange tile) + "FP Monitor / comment watch"; nav `Inbox · Blueprints · -sep- · Settings · About`; spacer; user switcher (see below) + a single sun/moon **theme icon button** at its right edge (one button that toggles, not a two-button control).
- **Top bar (global, 56px):** left = search (320px, `flex:none`); right = scan status + orange "Take snapshot" button. Global only - no page title here.
- **Content:** its own heading lives *in the content area* (e.g. `<h2>Last comments</h2>` + mono sub-count), then a toolbar (filters / date-range), then the list/cards.
- **Breadcrumb:** app is shallow - breadcrumb appears ONLY on drill-in (Blueprint detail: `Blueprints › <name>`). No trails elsewhere.

## Key components

- **Comment row:** avatar square (left) · blueprint name bold (anchor) or author bold · time top-right (hybrid: `2h ago` for fresh, `Jun 18` for old, exact UTC in `title`) · 2-line clamped text (expands on hover, `@media (hover:hover)` only). Actions: `Reply ↗` + a **Done toggle** (fixed `min-width:102px` so the timestamp never shifts; empty box "Mark done" ↔ green check "Done"; done rows dim and drop the orange dot).
- **Needs-reply dot:** small orange dot before the title; only on un-done rows.
- **Filters:** segmented pills (`All` default+first · `Needs reply` · `Done`).
- **Pagination:** `Showing 1–10 of N` + per-page `<select>` (10/25/50, default 10) + page numbers. Same component on inbox and table.
- **Stat card:** mono number + small green delta + uppercase tertiary label.
- **Chart:** line+area, one point per snapshot. **Plot by real date** so scan gaps show as spacing; the no-scan stretch is a **dashed segment** with a note that the slope is interpolated. Wide viewBox (e.g. 1000×220) + `preserveAspectRatio="xMidYMid meet"` + `height:auto` - never stretch a fixed-height SVG (distorts points/labels). Compact band so content below stays near the fold.
- **Table (Blueprints):** sortable headers, mono right-aligned numbers, orange `await` badge for >0 / quiet `-` for 0, clickable rows → detail. Sheds lower-priority columns (Comments, Last comment) under 820px.
- **User switcher:** the sidebar user box IS a button (hover highlight + up/down chevron - must read as clickable). Click → popover anchored above it (below it under 820px): "Monitored users" list with the current one checked, a divider, then **+ Add user** which expands an inline "paste factorioprints user URL → Go" field. Closes on outside click.
- **Settings form:** identity is a one-line subtitle, not a section. A compact card of label-left / control-right rows (label col ~200px, collapses to stacked under 820px). Read-only fields for data we already have (the user URL - it *defines* the user, so it's shown not asked; display name comes from the scrape). A **toggle switch** (accent track + knob) for booleans; conditional fields dim (`.off`) when their toggle is off. Don't add controls for non-decisions (no alert-frequency select - one daily snapshot, few comments).
- **Code block:** copy button lives in a header bar (`label` left, `Copy` right) above the `<pre>` - never overlapping the code. Reference/help content (e.g. Windows auto-scan setup) goes in a collapsed `<details>`, not an always-open block.

## Responsive (desktop-first, mobile-second)

Breakpoint **820px**. Below it: sidebar → wrapping top bar (icon-only nav, current label kept, theme icon inline); top bar wraps and search goes full-width; row actions stack full-width; pager stacks; table drops non-essential columns. Inputs/search use `box-sizing:border-box` + `min-width:0` so nothing overflows.

## Data-honesty rules

- "Done" is **manual** (a `handled` flag) - reliable with no scraping change. Auto-detecting the user's own replies needs their Disqus name + captured comment parent/thread data; that's a later upgrade, not the foundation.
- Never imply a trend across dates with no snapshots - show the gap.

## Screen map

`Inbox` (last-comments feed, the daily driver) · `Blueprints` (sortable list) · `Blueprint detail` (stats + favourites-over-time + that build's comments) · `Settings` (Disqus name, email alerts, Windows auto-scan setup) · `About` (credit to Nir Adar + GitHub link) · opening screen (choose user - minimal, single-user 99.9% of the time).

Killed from the old app: the standalone comments-between page (absorbed into inbox date-range) and the snapshots-management page.
