import * as fs from 'fs/promises';
import * as os from 'os';
import * as path from 'path';

import { AtomicFileService } from './core/atomic-file.service';
import { BackupService } from './core/backup.service';
import { EnvFileService } from './core/env-file.service';
import { FileLockService } from './core/file-lock.service';
import { PathService } from './core/path.service';
import { YamlDocService } from './core/yaml-doc.service';
import { HermesConfigService } from './hermes-config/hermes-config.service';
import { InstallationService } from './installation/installation.service';
import { SkillsService } from './skills/skills.service';
import { CatalogService } from './catalog/catalog.service';
import { McpService } from './mcp/mcp.service';
import { ModuleInstallerService } from './catalog/module-installer.service';

const CONFIG = `model:
  default: gpt-5
telegram:
  reactions: true
skills:
  external_dirs: []
  disabled:
    - apple-notes
mcp_servers:
  music:
    command: D:/old/python.exe
    args:
      - D:/old/server.py
    tools:
      include:
        - music_get_playlists
        - music_search_tracks
`;

const MANIFEST = `manifest_version: 1
name: weather
description: Weather tools.
transport:
  type: stdio
  command: python
  args:
    - "\${INSTALL_DIR}/server.py"
auth:
  type: api_key
  env:
    - name: WEATHER_API_KEY
      prompt: API key
      required: true
      secret: true
    - name: WEATHER_UNITS
      prompt: Units
      default: metric
      required: false
      secret: false
tools:
  default_enabled:
    - get_forecast
    - get_current
`;

const BUNDLE_MANIFEST = `manifest_version: 1
name: bundlemod
description: Bundle test module.
transport:
  type: stdio
  command: python
  args:
    - "\${INSTALL_DIR}/server.py"
auth:
  type: none
  env: []
tools:
  default_enabled:
    - t1
provides:
  server_env:
    DATA_DIR: "\${HERMES_ROOT}/bundlemod-data"
  skills:
    - src: bundle/skills/test-skill
      dest: skills/memory/test-skill
  hooks:
    - src: bundle/hooks/cap
      dest: hooks/cap
      template: true
  cron:
    - id: bundlemod-job
      name: "Bundlemod job"
      schedule: "*/15 * * * *"
      no_agent: true
      python: "\${VENV_PYTHON}"
      script: "\${INSTALL_DIR}/job.py"
      workdir: "\${INSTALL_DIR}"
      env:
        DATA_DIR: "\${HERMES_ROOT}/bundlemod-data"
`;

describe('dashboard integration (temp fixture)', () => {
  let root: string;
  let modulesDir: string;
  let installation: InstallationService;
  let config: HermesConfigService;
  let skills: SkillsService;
  let mcp: McpService;
  let installer: ModuleInstallerService;
  const prevEnvDir = process.env.CATALOG_MODULES_DIR;

  beforeAll(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), 'hermes-install-'));
    modulesDir = await fs.mkdtemp(path.join(os.tmpdir(), 'hermes-modules-'));
    process.env.CATALOG_MODULES_DIR = modulesDir;

    await fs.writeFile(path.join(root, 'config.yaml'), CONFIG, 'utf8');
    await fs.writeFile(path.join(root, '.env'), 'EXISTING=keep\n', 'utf8');
    await mkSkill(root, 'media', 'music-management', 'music-management', 'Music tools.');
    await mkSkill(root, 'apple', 'apple-notes', 'apple-notes', 'Apple Notes.');

    // a catalog module with a fake venv python to exercise venv resolution
    const modDir = path.join(modulesDir, 'weather');
    await fs.mkdir(path.join(modDir, '.venv', 'Scripts'), { recursive: true });
    await fs.writeFile(path.join(modDir, 'hermes-manifest.yaml'), MANIFEST, 'utf8');
    await fs.writeFile(path.join(modDir, '.venv', 'Scripts', 'python.exe'), '', 'utf8');

    // a BUNDLE module exercising provides{server_env, skills, hooks(template), cron}
    const bundleDir = path.join(modulesDir, 'bundlemod');
    await fs.mkdir(path.join(bundleDir, '.venv', 'Scripts'), { recursive: true });
    await fs.writeFile(path.join(bundleDir, '.venv', 'Scripts', 'python.exe'), '', 'utf8');
    await fs.writeFile(path.join(bundleDir, 'hermes-manifest.yaml'), BUNDLE_MANIFEST, 'utf8');
    const skillSrc = path.join(bundleDir, 'bundle', 'skills', 'test-skill');
    await fs.mkdir(skillSrc, { recursive: true });
    await fs.writeFile(path.join(skillSrc, 'SKILL.md'), '---\nname: test-skill\ndescription: bundle skill.\n---\nbody\n', 'utf8');
    const hookSrc = path.join(bundleDir, 'bundle', 'hooks', 'cap');
    await fs.mkdir(hookSrc, { recursive: true });
    await fs.writeFile(path.join(hookSrc, 'HOOK.yaml'), 'name: cap\nevents:\n  - agent:end\n', 'utf8');
    await fs.writeFile(path.join(hookSrc, 'handler.py'), 'PY = r"${VENV_PYTHON}"\nDATA = r"${HERMES_ROOT}/bundlemod-data"\n', 'utf8');

    const paths = new PathService();
    const yaml = new YamlDocService();
    const lock = new FileLockService();
    const backup = new BackupService();
    const atomic = new AtomicFileService();
    const env = new EnvFileService(lock, backup, atomic);
    config = new HermesConfigService(paths, yaml, lock, backup, atomic);
    installation = new InstallationService(paths);
    skills = new SkillsService(installation, config, paths);
    const catalog = new CatalogService(paths);
    mcp = new McpService(installation, config, catalog);
    installer = new ModuleInstallerService(installation, catalog, env, config, paths);

    await installation.connect(root);
  });

  afterAll(async () => {
    process.env.CATALOG_MODULES_DIR = prevEnvDir;
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(modulesDir, { recursive: true, force: true });
  });

  it('connects and validates the install', () => {
    const active = installation.getActiveOrThrow();
    expect(active.valid).toBe(true);
  });

  it('derives skill enabled state from skills.disabled', async () => {
    const list = await skills.list();
    const music = list.find((s) => s.name === 'music-management');
    const notes = list.find((s) => s.name === 'apple-notes');
    expect(music?.enabled).toBe(true);
    expect(notes?.enabled).toBe(false);
    expect(music?.category).toBe('media');
  });

  it('toggles a skill by mutating skills.disabled (and preserves unmanaged sections)', async () => {
    await skills.setEnabled('apple-notes', true);
    await skills.setEnabled('music-management', false);
    const text = await fs.readFile(path.join(root, 'config.yaml'), 'utf8');
    expect(text).toContain('- music-management');
    expect(text).not.toContain('- apple-notes');
    // unmanaged sections survive
    expect(text).toContain('default: gpt-5');
    expect(text).toContain('reactions: true');
    // a backup was created
    const backups = (await fs.readdir(root)).filter((f) => f.startsWith('config.yaml.bak.'));
    expect(backups.length).toBeGreaterThan(0);
  });

  it('lists mcp servers with derived state and toggles enabled', async () => {
    const before = await mcp.list();
    expect(before.find((s) => s.name === 'music')?.transport).toBe('stdio');
    await mcp.setServerEnabled('music', false);
    const after = await mcp.get('music');
    expect(after.enabled).toBe(false);
  });

  it('toggles a tool within an include list', async () => {
    await mcp.setToolEnabled('music', 'music_get_playlists', false);
    const view = await mcp.get('music');
    expect(view.tools.map((t) => t.name)).not.toContain('music_get_playlists');
    expect(view.tools.map((t) => t.name)).toContain('music_search_tracks');
  });

  it('renders the credential form with .env presence', async () => {
    const form = await installer.credentialForm('weather');
    expect(form.fields.map((f) => f.name)).toEqual(['WEATHER_API_KEY', 'WEATHER_UNITS']);
    expect(form.fields.find((f) => f.name === 'WEATHER_API_KEY')?.present).toBe(false);
  });

  it('installs a module: writes .env + mcp_servers block with venv python + tools.include', async () => {
    const result = await installer.install('weather', { WEATHER_API_KEY: 'abc123', WEATHER_UNITS: 'imperial' });
    expect(result.serverName).toBe('weather');
    expect(result.appliedEnvKeys).toEqual(expect.arrayContaining(['WEATHER_API_KEY', 'WEATHER_UNITS']));

    const envText = await fs.readFile(path.join(root, '.env'), 'utf8');
    expect(envText).toContain('WEATHER_API_KEY=abc123');
    expect(envText).toContain('EXISTING=keep'); // preserved

    const cfgText = await fs.readFile(path.join(root, 'config.yaml'), 'utf8');
    expect(cfgText).toContain('weather:');
    expect(cfgText).toContain('.venv/Scripts/python.exe'); // venv resolved, ${INSTALL_DIR} gone
    expect(cfgText).not.toContain('${INSTALL_DIR}');
    expect(cfgText).toContain('- get_forecast');

    expect(result.server.command).toContain('.venv/Scripts/python.exe');
    expect(result.notice).toMatch(/[Rr]estart/);
  });

  it('rejects install when a required credential is missing', async () => {
    await fs.writeFile(path.join(root, '.env'), 'EXISTING=keep\n', 'utf8'); // clear weather key
    await expect(installer.install('weather', { WEATHER_UNITS: 'metric' })).rejects.toThrow(/Required credential/);
  });

  it('installs a BUNDLE: server env, skill + templated hook copied, cron spec written', async () => {
    const fwdRoot = root.replace(/\\/g, '/');
    const result = await installer.install('bundlemod', {});
    expect(result.serverName).toBe('bundlemod');

    // server_env resolved onto the block (HERMES_ROOT is canonical forward-slash)
    expect(result.server.env?.DATA_DIR).toBe(`${fwdRoot}/bundlemod-data`);
    const cfg = await fs.readFile(path.join(root, 'config.yaml'), 'utf8');
    expect(cfg).toContain('DATA_DIR:');

    // skill copied into the install
    const skill = await fs.readFile(path.join(root, 'skills', 'memory', 'test-skill', 'SKILL.md'), 'utf8');
    expect(skill).toContain('name: test-skill');
    expect(result.placedSkills.length).toBe(1);

    // hook copied AND templated (placeholder resolved to the venv python path)
    const handler = await fs.readFile(path.join(root, 'hooks', 'cap', 'handler.py'), 'utf8');
    expect(handler).not.toContain('${VENV_PYTHON}');
    expect(handler).toContain('.venv/Scripts/python.exe');
    expect(handler).toContain(`${fwdRoot}/bundlemod-data`);
    expect(result.placedHooks.length).toBe(1);

    // cron spec file written (not jobs.json) + a register command surfaced
    const spec = JSON.parse(await fs.readFile(path.join(root, 'cron', 'bundlemod-job.job.json'), 'utf8'));
    expect(spec.schedule.value).toBe('*/15 * * * *');
    expect(spec.script).toContain('/job.py');
    expect(spec.env.DATA_DIR).toBe(`${fwdRoot}/bundlemod-data`);
    expect(result.cron[0].command).toContain('hermes cron create');
  });

  it('bulk toggles all skills, then re-enables one category', async () => {
    const total = (await skills.list()).length;
    const off = await skills.setBulkEnabled(false);
    expect(off.changed).toBe(total);
    expect((await skills.list()).every((s) => !s.enabled)).toBe(true);

    const onMedia = await skills.setBulkEnabled(true, 'media');
    expect(onMedia.changed).toBe(1);
    const list = await skills.list();
    expect(list.find((s) => s.category === 'media')?.enabled).toBe(true);
    expect(list.find((s) => s.category === 'apple')?.enabled).toBe(false);
  });
});

async function mkSkill(root: string, category: string, dir: string, name: string, description: string) {
  const skillDir = path.join(root, 'skills', category, dir);
  await fs.mkdir(skillDir, { recursive: true });
  await fs.writeFile(
    path.join(skillDir, 'SKILL.md'),
    `---\nname: ${name}\ndescription: ${description}\n---\n# ${name}\n`,
    'utf8',
  );
}
