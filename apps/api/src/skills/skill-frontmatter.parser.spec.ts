import { parseFrontmatter } from './skill-frontmatter.parser';

describe('parseFrontmatter', () => {
  it('parses valid frontmatter', () => {
    const r = parseFrontmatter(
      '---\nname: apple-notes\ndescription: Manage Apple Notes.\nplatforms: [macos]\n---\n# body\n',
    );
    expect(r.valid).toBe(true);
    expect(r.data?.name).toBe('apple-notes');
    expect(r.data?.platforms).toEqual(['macos']);
  });

  it('flags missing required fields but still returns best-effort data', () => {
    const r = parseFrontmatter('---\nname: x\n---\nbody');
    expect(r.valid).toBe(false);
    expect(r.errors.join(' ')).toMatch(/description/);
  });

  it('returns invalid when no frontmatter block exists', () => {
    const r = parseFrontmatter('# just markdown\n');
    expect(r.valid).toBe(false);
    expect(r.data).toBeNull();
  });
});
