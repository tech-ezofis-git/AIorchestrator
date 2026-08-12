# EZOFIS design tokens

Collected from two **public** EZOFIS properties (no login involved for
either) — the product app's sign-in screen and the marketing site. Both
are given below since they're built on different stacks and don't fully
agree on fonts, but they DO agree on the accent color, which is the
strongest signal for "the real EZOFIS brand color."

## The one thing that's consistent: brand cyan `#00bcd4`

- On the marketing site (`ezofis.com`), the primary CTA ("Request a
  Demo") renders exactly `rgb(0, 188, 212)` = `#00bcd4`.
- In the product app (`demoapp.ezofis.com`)'s own design tokens, that
  same value is `--secondary-9`.

Treat `#00bcd4` as the one true EZOFIS brand color if you only take one
thing from this doc.

## Product app (`demoapp.ezofis.com`) — custom-built, Radix-UI-style tokens

- **UI font:** `Inter`, variable weight 100–900 (the one actually
  loaded/used).
- **Also registered:** `Poppins` (100–900, normal+italic) — likely for
  marketing-flavored screens inside the app.
- **Full semantic palette + 12-step color scales:** see
  `theme-tokens.json` in this folder (already extracted).
- **Component detail:** buttons `border-radius: 5px`; the sign-in screen
  itself renders in a dark shell (`#121113` bg / `#b5b2bc` text) even
  though the main app's own tokens (`--surface: white`, `--bg-dashboard:
  #f6f3fb`) are light-themed — a dark auth screen in front of a light app,
  not one global mode.
- **Logo:** `assets/ezofis-logo-mark.png` (1080×1080 icon) +
  `assets/ezofis-logo-text.png` (1361×452 wordmark).

## Marketing site (`ezofis.com`) — WordPress/Elementor

- **Body font:** `Open Sans`.
- **Heading font:** `Lato`.
- (A long tail of other font-families are *registered* by WordPress
  plugins/icon fonts on this site — WooCommerce, Font Awesome, themify,
  etc. — but Open Sans/Lato are what's actually rendered in the content.)
- **CTA / brand color:** `#00bcd4` (see above).
- **Button radius:** `4px`.
- **Header background:** white.
- **Logo:** `assets/ezofis-marketing-logo.svg` (189×60 wordmark, white
  variant — meant for a dark/colored header, not a plain white
  background).

## Recommendation for a native app

Given the two sources disagree on fonts (`Inter`/`Poppins` vs. `Open
Sans`/`Lato`), lean on the **product app's** tokens as the primary
source — that's the actual software product your users will recognize,
not the marketing site — and use `#00bcd4` (confirmed on both) as the
anchor brand color:

- Font: **Inter** (body/UI), optionally **Poppins** for large marketing-
  style headings if the native app has any promotional screens.
- Primary accent: **`#00bcd4`** (brand cyan).
- Secondary accent: **`#7c5cff`** (the app's own internal purple/violet
  accent — `--primary-9` — used for buttons/focus states inside the
  product itself).
- Surfaces/text/borders/status colors: use the semantic table already in
  this file's previous revision / `theme-tokens.json`'s `semantic` block.

## What this does NOT cover

Still branding only. The real EZOFIS **API** (base URL
`https://demo.ezofis.com/v6api/api/...`, auth flow, endpoint shapes) is a
separate, still-open thread — see chat for where that stands.
