Tastebud app icon (Noodle Bowl) - icon pack
===========================================

Folders
  svg/                    Vector masters (scale to any size)
    tastebud-maskable.svg   Full-bleed square, artwork in the circular safe zone (master)
    tastebud-square.svg     Full-bleed square, slightly larger artwork (iOS / stores)
    tastebud-rounded.svg    Rounded square with transparent corners
    tastebud-circle.svg     Circle
    favicon.svg             Browser tab icon (artwork enlarged for small sizes)
    android-adaptive-foreground.svg
  png/any/                Rounded square, transparent corners: 48 to 1024 px
  png/maskable/           Full-bleed, safe for circle/squircle masks: 192, 512, 1024 px
  png/apple/              apple-touch-icon 120, 152, 167, 180 px (iOS rounds the corners itself)
  png/favicon/            16, 32, 48, 64 px
  png/circle/             Pre-cropped circles: 512, 1024 px
  png/square/             Full-bleed squares: 1024, 2048 px (app stores, high-res uses)
  png/android-adaptive/   432 px foreground + background layers (for a future Android wrapper)
  favicon.ico             16 + 32 + 48 px

Background colour: #1f2937 (use as theme_color / background_color)

manifest.webmanifest icons
--------------------------
"icons": [
  { "src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any" },
  { "src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any" },
  { "src": "/icons/maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable" },
  { "src": "/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable" }
],
"theme_color": "#1f2937",
"background_color": "#1f2937"

HTML <head>
-----------
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="/apple-touch-icon-180.png">
<meta name="theme-color" content="#1f2937">
