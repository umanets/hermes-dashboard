import { parseManifest } from './manifest.parser';

const APPLE_TIDAL = `manifest_version: 1
name: music
description: Apple Music / TIDAL integration.
source: https://example.com
transport:
  type: stdio
  command: python
  args:
    - "\${INSTALL_DIR}/server.py"
install:
  type: git
  url: https://example.com/repo.git
  ref: main
  bootstrap:
    - python -m venv .venv
auth:
  type: api_key
  env:
    - name: TIDAL_CLIENT_ID
      prompt: TIDAL client id
      required: false
      secret: false
    - name: TIDAL_CLIENT_SECRET
      prompt: TIDAL client secret
      required: false
      secret: true
tools:
  default_enabled:
    - music_get_playlists
    - music_search_tracks
`;

describe('parseManifest', () => {
  it('parses a valid stdio/api_key manifest', () => {
    const r = parseManifest(APPLE_TIDAL);
    expect(r.valid).toBe(true);
    expect(r.manifest?.name).toBe('music');
    expect(r.manifest?.transport.type).toBe('stdio');
    expect(r.manifest?.transport.args).toEqual(['${INSTALL_DIR}/server.py']);
    expect(r.manifest?.auth.env.find((e) => e.name === 'TIDAL_CLIENT_SECRET')?.secret).toBe(true);
    expect(r.manifest?.tools.default_enabled).toContain('music_get_playlists');
  });

  it('flags stdio manifest missing a command', () => {
    const r = parseManifest('manifest_version: 1\nname: x\ntransport:\n  type: stdio\n');
    expect(r.valid).toBe(false);
    expect(r.errors.join(' ')).toMatch(/command/);
  });

  it('flags invalid YAML', () => {
    const r = parseManifest(':\n  - [unbalanced');
    expect(r.valid).toBe(false);
  });
});
