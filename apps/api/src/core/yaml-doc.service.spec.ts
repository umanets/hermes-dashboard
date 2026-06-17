import { YamlDocService } from './yaml-doc.service';

const SAMPLE = `model:
  default: gpt-5
# user comment that must survive
telegram:
  allowed_chats: ''
  reactions: true
skills:
  external_dirs: []
  disabled:
    - claude-code
    - opencode
mcp_servers:
  music:
    command: D:/work/hermes-tools/apple-tidal/.venv/Scripts/python.exe
    args:
      - D:/work/hermes-tools/apple-tidal/server.py
`;

describe('YamlDocService round-trip', () => {
  const svc = new YamlDocService();

  it('preserves unmanaged sections and comments when mutating one node', () => {
    const doc = svc.parse(SAMPLE);
    svc.set(doc, ['skills', 'disabled'], ['claude-code']);
    const out = svc.stringify(doc);

    expect(out).toContain('model:');
    expect(out).toContain('default: gpt-5');
    expect(out).toContain('# user comment that must survive');
    expect(out).toContain('reactions: true');
    expect(out).toContain('command: D:/work/hermes-tools/apple-tidal/.venv/Scripts/python.exe');
    // mutated node reflects the change
    expect(out).toContain('- claude-code');
    expect(out).not.toContain('- opencode');
  });

  it('adds a new mcp_servers entry without touching others', () => {
    const doc = svc.parse(SAMPLE);
    svc.set(doc, ['mcp_servers', 'apple-tidal'], { enabled: true, command: 'py', args: ['s.py'] });
    const out = svc.stringify(doc);
    expect(out).toContain('music:');
    expect(out).toContain('apple-tidal:');
    expect(out).toContain('enabled: true');
  });

  it('toJS exposes managed slices for reads', () => {
    const js = svc.toJS<{ skills: { disabled: string[] }; mcp_servers: Record<string, unknown> }>(
      svc.parse(SAMPLE),
    );
    expect(js.skills.disabled).toEqual(['claude-code', 'opencode']);
    expect(Object.keys(js.mcp_servers)).toContain('music');
  });
});
