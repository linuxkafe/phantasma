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

pHantasma uses a professional, minimal aesthetic with high contrast and purposeful design. The design system is optimized for CLI/API/voice-first interfaces with optional Android companion app.

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

## Do's

- Use semantic HTML in Android Compose / web views
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

## Android Integration

The Android app (Kotlin + Jetpack Compose) consumes this design system via:
- `android/app/src/main/java/com/phantasma/app/ui/theme/`
- Color.kt, Typography.kt, Shapes.kt generated from this spec
- Components in `android/app/src/main/java/com/phantasma/app/ui/components/`

## CLI / Terminal

For terminal output (logs, CLI):
- Use ANSI colors matching palette where supported
- Structured logging: `%(asctime)s %(levelname)s %(name)s %(message)s`
- No emoji in log output
- Progress indicators: simple spinner or percentage