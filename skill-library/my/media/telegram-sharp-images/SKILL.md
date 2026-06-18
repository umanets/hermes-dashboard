---
name: telegram-sharp-images
description: Use when delivering images on Telegram to ensure sharp native photos
instead of blurry webpage previews. Works around Telegram's low-res OG thumbnail
caching by using MEDIA:/path instead of Markdown ![]() links.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [telegram, images, media, delivery, preview]
    related_skills: []
---

# Telegram Sharp Image Delivery

## Overview

When Hermes sends an image on Telegram via Markdown `![alt](url)`, Telegram fetches
the URL as a **webpage preview** — it looks up Open Graph metadata (`og:image`) and
caches a low-resolution thumbnail (often 200×200 or 320×320). Stretching that
thumbnail in-chat produces the pixelated/blurry result.

The fix: use `MEDIA:/absolute/path/to/file` in the response. This instructs the
Telegram gateway to call `send_photo` — Telegram stores and serves the image natively,
producing a sharp inline photo.

## When to Use

- **Any time `image_generate` returns a remote URL (FAL, etc.):** download locally → `MEDIA:`
- **Any time you want the image to appear sharp on Telegram** and not as a webpage preview
- **Screenshots, diagrams, generated art** — anything where visual quality matters

## When NOT to Use

- Plain text messages (no image involved)
- Platforms other than Telegram (MEDIA: is Hermes-generic but the sharpness problem is Telegram-specific)
- When the URL is already a direct image link and the user is on a non-Telegram platform

## Workflow

### 1. Generate or obtain the image URL

```
image_generate(prompt="...") → returns {"image": "https://..."}
```

### 2. Download the image locally

```bash
curl -sL -o /tmp/my_image.png "https://v3b.fal.media/files/..."
```

Use a unique filename under `/tmp/` to avoid collisions. Check the file exists and
has reasonable size.

### 3. Deliver via MEDIA:

```
MEDIA:/tmp/my_image.png
```

Include this directive in your response. The Telegram gateway picks it up and sends
it as `send_photo`.

### 4. (Optional) Clean up

Temporary files under `/tmp/` are auto-cleaned by the OS, but you can `rm` them
after delivery if you prefer.

## Side-by-Side

| Method | Telegram API call | Quality | When |
|--------|------------------|---------|------|
| `![alt](url)` | `send_message` (webpage preview) | Blurry/pixelated thumbnail | Non-critical, thumbnails only |
| `MEDIA:/path` | `send_photo` | Sharp, native resolution | Always for generated images |

## Common Pitfalls

1. **Using the remote URL directly with MEDIA:** `MEDIA:https://...` won't work —
   MEDIA: requires a local absolute file path. Download first.

2. **Downloading to a directory without write permissions:** `/tmp/` is always safe.
   Avoid `~/` if the home directory might be read-only in some backends.

3. **Forgetting to download at all:** If `image_generate` returned a local path
   (some backends do), you can use it directly. But FAL and most backends return
   URLs — always check.

4. **Re-using the same filename:** If you do multiple images in one session, use
   distinct filenames (`/tmp/cat_1.png`, `/tmp/cat_2.png`) to avoid overwrites.

5. **Assuming the user sees the image immediately:** On slow connections, `send_photo`
   uploads the full image — it takes longer than a webpage preview. Still worth it
   for quality.

## Verification Checklist

- [ ] Image downloaded with `curl` and confirmed non-zero size (`ls -lh`)
- [ ] Response includes `MEDIA:/absolute/path/to/file` (not a URL)
- [ ] File path is absolute (starts with `/`)
- [ ] User confirms the image is sharp, not pixelated
```
