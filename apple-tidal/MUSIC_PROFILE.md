# Music profile

This file guides the recommendation engine. **Edit it freely** — the parser
reads the first fenced `yaml` block below. Per-request instructions (passed to
`music_recommend_tracks`) are merged on top of this baseline, so you can steer a
single request without changing your standing preferences.

Fields:
- `preferences.vocals` — vocal qualities you gravitate toward.
- `preferences.sound` — production / sonic qualities you like.
- `preferences.avoid` — things to steer away from.
- `reference_artists` — anchor artists the engine uses to seed discovery.

```yaml
preferences:
  vocals:
    - unusual
    - soulful
    - emotionally intense
    - dramatic
    - deep female vocals
    - expressive high male vocals

  sound:
    - punchy bass
    - wide soundstage
    - cinematic atmosphere
    - strong vocal presence

  avoid:
    - generic pop vocals
    - emotionally flat performances
    - repetitive mainstream recommendations

reference_artists:
  - Adam Naas
  - Josephine Oniyama
  - Benjamin Clementine
  - London Grammar
  - Michael Kiwanuka
  - Anastasia Minster
  - Black Pumas

# Optional per-artist mood tags. The agent uses these (plus an auto-built SQLite
# cache) to pick mood-appropriate seed artists, e.g. for an "energy" request.
# You can edit these freely; missing artists get tagged automatically over time.
artist_moods:
  Black Pumas: [soulful, groovy, energy]
  London Grammar: [mellow, cinematic, emotional]
  Benjamin Clementine: [dramatic, theatrical, emotional]
  Michael Kiwanuka: [soulful, warm, mellow]
  Adam Naas: [soulful, intimate]
  Anastasia Minster: [dramatic, emotional]
```
