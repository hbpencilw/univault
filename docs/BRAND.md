# UniVault brand

The mark is a **graduation cap inside a vault ring**: the ring is the vault /
academic seal, the cap is the university, and the blue tassel is the single
accent. It is drawn on an 8pt grid, uses only the system palette below, and
stays legible at 24 px.

## Files

| File | Use |
|---|---|
| `assets/brand/logo.svg` | App-icon style mark on a rounded graphite tile (512×512) |
| `assets/brand/logo-mark-light.svg` | Transparent white mark for dark backgrounds |
| `assets/brand/logo-mark-dark.svg` | Transparent black mark for light backgrounds |
| `assets/brand/lockup.svg` | Horizontal mark + "UniVault" + tagline, for dark backgrounds |
| `assets/brand/lockup-dark.svg` | Same lockup in black, for light backgrounds |

## README artwork

| File | Use |
|---|---|
| `assets/readme/hero.svg` | English hero |
| `assets/readme/hero.zh-CN.svg` | Simplified Chinese hero |
| `assets/readme/hero.ja.svg` | Japanese hero |
| `assets/readme/hero.ko.svg` | Korean hero |
| `assets/readme/workflow.svg` | English workflow diagram |
| `assets/readme/workflow.zh-CN.svg` | Simplified Chinese workflow diagram |
| `assets/readme/workflow.ja.svg` | Japanese workflow diagram |
| `assets/readme/workflow.ko.svg` | Korean workflow diagram |

Localized artwork keeps the same 1200-wide canvas, graphites and system palette;
only the copy changes. Keep product names (`Ed`, `Moodle`, `Lessons`,
`univault.jsonl`) untranslated.

## Palette

| Token | Value | Use |
|---|---|---|
| Ink | `#000000` | Canvas |
| Graphite | `#1C1C1E` | Surfaces / icon tile base |
| Blue | `#0A84FF` | Dial handle, primary accent |
| Cyan | `#64D2FF` | Dial gradient start |
| White | `#F2F2F7` | Shelves, wordmark on dark |
| Grey | `#8E8E93` | Taglines, secondary text |

## Usage

- **Clear space**: keep at least the width of one shelf (≈ 1/4 of the mark)
  free on all sides.
- **Minimum size**: 24 px for the mark; 120 px wide for the lockup.
- **Backgrounds**: use `logo-mark-light.svg` on dark surfaces and
  `logo-mark-dark.svg` on light surfaces. Never place the light mark on white.
- **Do not** rotate, stretch, outline, add shadows, or recolor the mark outside
  the palette above. Do not set the wordmark in a different typeface.
- **Wordmark**: SF Pro Display Semibold with −1.2 tracking; tagline uses
  uppercase SF Pro Text with +2.2 tracking.

## Icon construction

- 512 × 512 tile, 112 px corner radius (≈ 22%).
- Vault ring: centre 256,252; radius 140; 22 px stroke; cyan-to-blue gradient.
- Cap diamond: 200 × 92, corners at 256,180 / 356,226 / 256,272 / 156,226.
- Cap band: 100 wide × 54 high, 14 px bottom radius, top edge at y=258.
- Tassel: 8 px round-cap stroke from the right diamond corner down 72 px,
  ending in an 8 px dot.
- All measurements are multiples of 2 and aligned to the 8pt rhythm.

The mark is original work for UniVault (MIT). README header icons use Lucide
(ISC) and are credited in `docs/THIRD_PARTY.md`.
