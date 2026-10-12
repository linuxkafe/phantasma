---
schema: google-labs-code/design.md/v1
name: pHantasma Design System
version: 0.1.0
colors:
  background:
    dark: "#0a0a0a"
    light: "#ffffff"
  surface:
    dark: "#171717"
    light: "#f5f5f5"
  border:
    dark: "#262626"
    light: "#e5e5e5"
  text:
    primary:
      dark: "#fafafa"
      light: "#171717"
    muted:
      dark: "#737373"
      light: "#737373"
  accent:
    dark: "#22c55e"
    light: "#22c55e"
  destructive:
    dark: "#ef4444"
    light: "#ef4444"
typography:
  fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
  fontMono: "SF Mono, 'Fira Code', monospace"
  scale:
    display: { size: "32px", weight: 700 }
    h1: { size: "24px", weight: 600 }
    h2: { size: "20px", weight: 600 }
    body: { size: "14px", weight: 400 }
    small: { size: "12px", weight: 400 }
    mono: { size: "14px", weight: 400 }
spacing:
  baseUnit: "4px"
  scale: [4, 8, 16, 24, 32, 48, 64]
borderRadius:
  sm: "4px"
  md: "8px"
  lg: "12px"
  full: "9999px"
shadows:
  sm: "0 1px 2px 0 rgb(0 0 0 / 0.05)"
  md: "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)"
  lg: "0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)"
components:
  button:
    variants: [primary, secondary, destructive, ghost]
    sizes: [sm, md, lg]
  input:
    variants: [default, error]
  card:
    variants: [default, elevated]
  dialog:
    sizes: [sm, md, lg, xl]
icons:
  library: "lucide"
  note: "No emojis in source code. Use Lucide icons or inline SVG."
---

# pHantasma Design System

## Visual Theme

pHantasma uses a professional, minimal aesthetic with high contrast and purposeful design. The design system is optimized for CLI/API/voice-first interfaces. The single interface is the responsive page served at `/`, which is also the phone interface — there is no second client to keep in step.

### Dark Mode (Default)
- Background: `#0a0a0a`
- Surface: `#171717`
- Border: `#262626`
- Text Primary: `#fafafa`
- Text Muted: `#737373`
- Accent: `#22c55e` (green)
- Destructive: `#ef4444` (red)

### Light Mode
- Background: `#ffffff`
- Surface: `#f5f5f5`
- Border: `#e5e5e5`
- Text Primary: `#171717`
- Text Muted: `#737373`
- Accent: `#22c55e`
- Destructive: `#ef4444`

## Typography

Font stack: `Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`
Monospace: `SF Mono, "Fira Code", monospace`

| Element | Size | Weight | Use Case |
|---------|------|--------|----------|
| Display | 32px | 700 | Hero, landing |
| H1 | 24px | 600 | Page titles |
| H2 | 20px | 600 | Section headers |
| Body | 14px | 400 | Default text |
| Small | 12px | 400 | Captions, metadata |
| Mono | 14px | 400 | Code, logs, CLI output |

## Spacing

4px base unit: 4, 8, 16, 24, 32, 48, 64px

## Components

### Button
Variants: primary (accent), secondary (surface), destructive, ghost
Sizes: sm (32px h), md (40px h), lg (48px h)

### Input
Variants: default, error (destructive border)
States: focus (accent ring), disabled (muted)

### Card
Variants: default (surface + border), elevated (surface + shadow md)

### Dialog
Sizes: sm (320px), md (480px), lg (640px), xl (800px)

## Iconography

Library: Lucide (lucide.dev)
- No emojis in source code (.py, .tsx, .vue, .dart, etc.)
- Use Lucide icons, Heroicons, inline SVG, or text labels
- Emojis allowed only in comments, markdown docs, README

## The Ghost Avatar

The chat ghost is a `<ghost-avatar>` custom element with a shadow root, defined
once in `skills/skill_ui.py`. One `<template>` holds the whole drawing; every
instance clones it, so the markup exists once in the document rather than being
inlined per message, and page CSS cannot reach the face.

**Ten expressions**, switched by the `data-expression` attribute:
`normal`, `wink`, `happy`, `thinking`, `surprised`, `confused`, `sleepy`,
`excited`, `error`, `loading`.

The rules the component holds to:

- **The silhouette is frozen.** The body path is the brand ghost's own `d`,
  identical in all ten expressions. Only the face varies. A test compares the
  component's path against `GHOST_SVG` so "the ghost in the chat" cannot quietly
  become a lookalike.
- **The fallback is CSS, not JavaScript.** The ten rules only ever *hide*
  `normal`, so an unknown, empty or missing expression leaves the default
  standing. A malformed attribute can never blank the avatar, and it can never
  show two faces, because exactly one rule pair can ever apply.
- **No idle animation, and no accumulating motion.** No blinking, no float on a
  delivered message. `.ia-avatar` used to carry `floatGhost ... infinite`, which
  left one permanent animation per message — the browser's census reads exactly
  one infinite animation per avatar, linear in conversation length, never cleaned
  up. It was removed on owner instruction 2026-10-07.

  The performance benefit is **unverified**: freezing it moved the p95 frame gap
  by −0.1 ms at n=2 on every Chromium available, so the Android symptom that
  prompted the instruction could not be reproduced and may have another cause.
  What the change does guarantee is bounded motion.

  The one exception is the `thinking` face, which breathes while a reply is being
  composed (owner instruction 2026-10-01). That row is created on demand and
  removed the moment the reply lands, so it cannot accumulate — which is exactly
  why it is the only place motion is allowed to remain.
- **Nine `--avatar-*` variables** — `--avatar-size`, `--avatar-body`,
  `--avatar-fill`, `--avatar-face`, `--avatar-highlight`,
  `--avatar-background`, `--avatar-shadow`, `--avatar-opacity`,
  `--avatar-stroke-width` — so a future light theme does not touch the geometry.
  `--avatar-face` and `--avatar-body` need 3:1 against `--avatar-background`,
  and the face against the body fill.
- **No visible text in the SVG.** A per-message ghost is `aria-hidden`; the
  typing ghost is labelled, because in that row it is the only thing saying
  "I am working".

`addToChatLog(text, sender, expression)` takes the expression rather than
looking it up: normal answers pass `normal`, the typing row passes `thinking`,
and a failed command or a network error passes `error`. The eight remaining
faces are built and tested but not yet reachable from a real state.

Rendering tests live in `tests/test_ghost_avatar_browser.py`. They assert painted
geometry, not string presence, because the failure mode is silent: an expression
matching no rule leaves every face hidden and the chat shows a blank space.

## The bar ghost

The ghost in the top bar is the same `<ghost-avatar>` component, and it carries
two independent readings (owner instruction 2026-10-07):

| what | comes from | how |
| --- | --- | --- |
| **face** | the last thing pHantasma said | `expression` attribute on the component |
| **tint** | the weather | `.ghost-sun` / `.ghost-rain` / `.ghost-storm` on the host |

They are kept apart on purpose. The weather tint is a CSS `filter` and a CSS
`animation` on the **host element**, and both apply to shadow content — so the
sky still colours the bar ghost exactly as it always did, with no second copy of
the tint rules. `--avatar-body: currentColor` is what carries the old inline
`stroke="currentColor"` across, and `--avatar-size: 1em` keeps the component's
22px default from shrinking a bar ghost that has always been 1em of a 2rem font.

The face comes from `faceForText()`, an ordered first-match table of Portuguese
patterns, and it is deliberately small. A wrong face is a cosmetic miss, so the
rule is "default to `normal` rather than guess".

Two rules about what it may not decide:

- **`error` comes from the caller, never from the text.** A failed request
  arrives as `expression === 'error'` because words cannot carry that fact
  reliably: "Não foi possível" is a phrase, not a guarantee. If the classifier
  decided, a cheerful sentence about a broken lamp could put a smile on the face
  of a command that never happened.
- **A user message never moves it.** Only pHantasma's own words do. The brand
  reacting to being typed *at* would be a ghost answering itself.

The bar ghost is also the page's only long-lived `<ghost-avatar>`, which is what
makes it affordable: it costs one cloned stylesheet for the whole session
instead of one per reply.

Note the trap this code sits in: the page is a Python string literal, so a
single-backslash word boundary is decoded by Python into U+0008 before the
browser ever sees it, and the boundary vanishes silently. The rules double every
backslash, and `tests/test_bar_ghost_expression.py` asserts the compiled
patterns still carry boundaries rather than only checking that phrases work.

## Do's

- Use semantic HTML in web views
- Use icons from Lucide library
- Maintain 4px spacing grid
- Use design tokens (never hardcode colors)
- Ensure WCAG AA contrast in both themes

## Don'ts

- Use emoji in source code
- Use arbitrary colors not in palette
- Break 4px spacing grid
- Override design tokens in component code
- Use emoji as UI indicators

## CLI / Terminal

For terminal output (logs, CLI):
- Use ANSI colors matching palette where supported
- Structured logging: `%(asctime)s %(levelname)s %(name)s %(message)s`
- No emoji in log output
- Progress indicators: simple spinner or percentage