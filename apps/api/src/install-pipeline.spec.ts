import * as fs from 'fs/promises';
import { existsSync } from 'fs';
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
import { CatalogService } from './catalog/catalog.service';
import { ModuleInstallerService } from './catalog/module-installer.service';
import { InstallJobService } from './catalog/install-job.service';

const MANIFEST = `manifest_version: 1
name: vmem
description: pipeline test module.
transport:
  type: stdio
  command: python
  args:
    - "\${INSTALL_DIR}/server.py"
install:
  type: local
  bootstrap:
    - "python -m venv .venv"
  warmup: "print('warm')"
auth:
  type: none
  env: []
tools:
  default_enabled: [t1]
provides:
  server_env:
    DATA_DIR: "\${HERMES_ROOT}/vmem-data"
  skills:
    - src: bundle/skills/s
      dest: skills/memory/s
  hooks:
    - src: bundle/hooks/h
      dest: hooks/h
      template: true
  cron:
    - id: vmem-sum
      name: "vmem summary"
      schedule: "*/15 * * * *"
      no_agent: true
      python: "\${VENV_PYTHON}"
      script: "\${INSTALL_DIR}/summarize_job.py"
      env:
        DATA_DIR: "\${HERMES_ROOT}/vmem-data"
`;

// Fake shell: records calls, simulates `python -m venv` by creating the venv python.
class FakeShell {
  calls: { command: string; args: string[] }[] = [];
  async which(cmd: string): Promise<string | null> {
    return cmd === 'python' ? '/usr/bin/python' : cmd === 'hermes' ? '/usr/bin/hermes' : null;
  }
  async run(command: string, args: string[], opts: { cwd?: string } = {}) {
    this.calls.push({ command, args });
    if (args.includes('venv') && opts.cwd) {
      const dir = path.join(opts.cwd, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin');
      await fs.mkdir(dir, { recursive: true });
      await fs.writeFile(path.join(dir, process.platform === 'win32' ? 'python.exe' : 'python'), '');
    }
    return { code: 0, stdout: 'ok', stderr: '', ok: true };
  }
}

// Fake docker: records `exec` argv. When given the host volume + dataDir, simulates
// `uv venv <dataDir>/.../.venv` by creating the (Linux-layout) venv python on the host
// volume so the pipeline's reuse-check sees it.
class FakeDocker {
  calls: { name: string; argv: string[] }[] = [];
  constructor(private hostVol?: string, private dataDir = '/opt/data') {}
  async exec(name: string, argv: string[], _opts: { timeoutMs?: number } = {}) {
    this.calls.push({ name, argv });
    if (argv[0] === 'uv' && argv.includes('venv') && this.hostVol) {
      const venvArg = argv[argv.length - 1]; // /opt/data/modules/vmem/.venv
      const hostVenv = venvArg.replace(this.dataDir, this.hostVol);
      const binDir = path.join(hostVenv, 'bin');
      await fs.mkdir(binDir, { recursive: true });
      await fs.writeFile(path.join(binDir, 'python'), '');
    }
    return { code: 0, stdout: '', stderr: '', ok: true };
  }
  async execDetached() {
    return { code: 0, stdout: '', stderr: '', ok: true };
  }
}

describe('install pipeline (stubbed shell)', () => {
  let root: string;
  let modulesDir: string;
  let jobs: InstallJobService;
  let shell: FakeShell;
  const prev = process.env.CATALOG_MODULES_DIR;

  beforeAll(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), 'pipe-install-'));
    modulesDir = await fs.mkdtemp(path.join(os.tmpdir(), 'pipe-modules-'));
    process.env.CATALOG_MODULES_DIR = modulesDir;

    await fs.writeFile(path.join(root, 'config.yaml'), 'model:\n  default: x\nmcp_servers: {}\n', 'utf8');
    await fs.writeFile(path.join(root, '.env'), 'EXISTING=keep\n', 'utf8');
    await fs.mkdir(path.join(root, 'skills'), { recursive: true });

    const md = path.join(modulesDir, 'vmem');
    await fs.mkdir(path.join(md, 'bundle', 'skills', 's'), { recursive: true });
    await fs.mkdir(path.join(md, 'bundle', 'hooks', 'h'), { recursive: true });
    await fs.writeFile(path.join(md, 'hermes-manifest.yaml'), MANIFEST, 'utf8');
    await fs.writeFile(path.join(md, 'server.py'), '# server\n', 'utf8');
    await fs.writeFile(path.join(md, 'summarize_job.py'), '# job\n', 'utf8');
    await fs.writeFile(path.join(md, 'bundle', 'skills', 's', 'SKILL.md'), '---\nname: s\ndescription: d.\n---\n', 'utf8');
    await fs.writeFile(path.join(md, 'bundle', 'hooks', 'h', 'handler.py'), 'PY = r"${VENV_PYTHON}"\n', 'utf8');

    const paths = new PathService();
    const lock = new FileLockService();
    const backup = new BackupService();
    const atomic = new AtomicFileService();
    const env = new EnvFileService(lock, backup, atomic);
    const config = new HermesConfigService(paths, new YamlDocService(), lock, backup, atomic);
    const installation = new InstallationService(paths);
    const catalog = new CatalogService(paths);
    const installer = new ModuleInstallerService(installation, catalog, env, config, paths);
    shell = new FakeShell();
    jobs = new InstallJobService(installation, catalog, installer, shell as any, paths, new FakeDocker() as any);

    await installation.connect(root);
  });

  afterAll(async () => {
    process.env.CATALOG_MODULES_DIR = prev;
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(modulesDir, { recursive: true, force: true });
  });

  it('runs all steps: copy → venv → deps → configure → cron', async () => {
    const { jobId } = await jobs.start('vmem', {});
    let job = jobs.get(jobId);
    for (let i = 0; i < 200 && job.status === 'running'; i++) {
      await new Promise((r) => setTimeout(r, 25));
      job = jobs.get(jobId);
    }
    expect(job.status).toBe('completed');
    expect(job.steps.map((s) => s.status)).toEqual(['done', 'done', 'done', 'done', 'done', 'done']);

    // module copied to <root>/modules/vmem (без .venv из источника)
    const local = path.join(root, 'modules', 'vmem');
    expect(existsSync(path.join(local, 'server.py'))).toBe(true);
    const venvPy = path.join(local, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    expect(existsSync(venvPy)).toBe(true); // fake venv created it

    // shell did venv + pip + hermes cron create
    expect(shell.calls.some((c) => c.args.includes('venv'))).toBe(true);
    expect(shell.calls.some((c) => c.args.includes('pip'))).toBe(true);
    expect(shell.calls.some((c) => c.args.includes('-c') && c.args.join(' ').includes("print('warm')"))).toBe(true);
    expect(shell.calls.some((c) => c.command === '/usr/bin/hermes' && c.args.includes('create'))).toBe(true);

    // mcp block points at the LOCAL venv python; skill + hook copied
    const cfg = await fs.readFile(path.join(root, 'config.yaml'), 'utf8');
    expect(cfg).toContain('vmem:');
    expect(cfg).toContain('modules/vmem/.venv');
    expect(existsSync(path.join(root, 'skills', 'memory', 's', 'SKILL.md'))).toBe(true);
    const handler = await fs.readFile(path.join(root, 'hooks', 'h', 'handler.py'), 'utf8');
    expect(handler).not.toContain('${VENV_PYTHON}'); // templated

    // cron launcher written in <root>/scripts and re-execs the venv python
    const launcher = await fs.readFile(path.join(root, 'scripts', 'vmem-sum.py'), 'utf8');
    expect(launcher).toContain('subprocess.run');
    expect(launcher).toContain('modules/vmem/.venv');
    expect(launcher).toContain('summarize_job.py');
  });

  it('marks the running step failed when a shell step errors', async () => {
    const badShell = new FakeShell();
    badShell.run = async (command: string, args: string[]) => {
      badShell.calls.push({ command, args });
      if (args.includes('pip')) return { code: 1, stdout: '', stderr: 'boom', ok: false };
      if (args.includes('venv')) return { code: 0, stdout: '', stderr: '', ok: true };
      return { code: 0, stdout: '', stderr: '', ok: true };
    };
    const paths = new PathService();
    const lock = new FileLockService();
    const config = new HermesConfigService(paths, new YamlDocService(), lock, new BackupService(), new AtomicFileService());
    const installation = new InstallationService(paths);
    await installation.connect(root);
    const catalog = new CatalogService(paths);
    const installer = new ModuleInstallerService(
      installation,
      catalog,
      new EnvFileService(lock, new BackupService(), new AtomicFileService()),
      config,
      paths,
    );
    const j = new InstallJobService(installation, catalog, installer, badShell as any, paths, new FakeDocker() as any);
    // ensure a fresh module dir so venv isn't "reused" from the prior test (would skip deps)
    await fs.rm(path.join(root, 'modules', 'vmem'), { recursive: true, force: true });
    const { jobId } = await j.start('vmem', {});
    let job = j.get(jobId);
    for (let i = 0; i < 200 && job.status === 'running'; i++) {
      await new Promise((r) => setTimeout(r, 25));
      job = j.get(jobId);
    }
    expect(job.status).toBe('failed');
    expect(job.steps.find((s) => s.key === 'deps')?.status).toBe('failed');
  });
});

const HMEM_MANIFEST = `manifest_version: 1
name: hindsight
description: http MCP memory test module.
transport:
  type: http
  url: "http://hindsight:8888/mcp/\${BANK_ID}/"
auth:
  type: none
  env: []
tools:
  default_enabled: [retain, recall, reflect]
`;

const FCRED_MANIFEST = `manifest_version: 1
name: fcred
description: file-cred test module.
transport:
  type: http
  url: "http://localhost:9/mcp"
auth:
  type: api_key
  env:
    - name: APPLE_KEY
      prompt: "path to .p8"
      required: true
      secret: false
      file: true
tools:
  default_enabled: []
`;

describe('container-mode install pipeline (stubbed docker)', () => {
  let root: string;
  let modulesDir: string;
  let keyFile: string;
  let jobs: InstallJobService;
  let docker: FakeDocker;
  const prev = process.env.CATALOG_MODULES_DIR;

  beforeAll(async () => {
    root = await fs.mkdtemp(path.join(os.tmpdir(), 'cpipe-install-'));
    modulesDir = await fs.mkdtemp(path.join(os.tmpdir(), 'cpipe-modules-'));
    process.env.CATALOG_MODULES_DIR = modulesDir;

    await fs.writeFile(path.join(root, 'config.yaml'), 'model:\n  default: x\nmcp_servers: {}\n', 'utf8');
    await fs.writeFile(path.join(root, '.env'), '', 'utf8');
    await fs.mkdir(path.join(root, 'skills'), { recursive: true });

    // stdio module (vmem) for the venv/bundle/cron assertions
    const md = path.join(modulesDir, 'vmem');
    await fs.mkdir(path.join(md, 'bundle', 'skills', 's'), { recursive: true });
    await fs.mkdir(path.join(md, 'bundle', 'hooks', 'h'), { recursive: true });
    await fs.writeFile(path.join(md, 'hermes-manifest.yaml'), MANIFEST, 'utf8');
    await fs.writeFile(path.join(md, 'server.py'), '# server\n', 'utf8');
    await fs.writeFile(path.join(md, 'summarize_job.py'), '# job\n', 'utf8');
    await fs.writeFile(path.join(md, 'bundle', 'skills', 's', 'SKILL.md'), '---\nname: s\ndescription: d.\n---\n', 'utf8');
    await fs.writeFile(path.join(md, 'bundle', 'hooks', 'h', 'handler.py'), 'PY = r"${VENV_PYTHON}"\n', 'utf8');

    // http MCP module (hmem) — no venv, url templated with the per-client bank
    const hd = path.join(modulesDir, 'hmem');
    await fs.mkdir(hd, { recursive: true });
    await fs.writeFile(path.join(hd, 'hermes-manifest.yaml'), HMEM_MANIFEST, 'utf8');

    // file-cred module (fcred) + a host key file
    const fd = path.join(modulesDir, 'fcred');
    await fs.mkdir(fd, { recursive: true });
    await fs.writeFile(path.join(fd, 'hermes-manifest.yaml'), FCRED_MANIFEST, 'utf8');
    keyFile = path.join(root, 'AuthKey_ABC123.p8');
    await fs.writeFile(keyFile, '-----BEGIN PRIVATE KEY-----\n', 'utf8');

    const paths = new PathService();
    const lock = new FileLockService();
    const env = new EnvFileService(lock, new BackupService(), new AtomicFileService());
    const config = new HermesConfigService(paths, new YamlDocService(), lock, new BackupService(), new AtomicFileService());
    const installation = new InstallationService(paths);
    const catalog = new CatalogService(paths);
    const installer = new ModuleInstallerService(installation, catalog, env, config, paths);
    docker = new FakeDocker(root, '/opt/data');
    jobs = new InstallJobService(installation, catalog, installer, new FakeShell() as any, paths, docker as any);

    // connect WITH a container context → pipeline runs via docker exec, container paths
    await installation.connect(root, { name: 'hermes-test', dataDir: '/opt/data' });
  });

  afterAll(async () => {
    process.env.CATALOG_MODULES_DIR = prev;
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(modulesDir, { recursive: true, force: true });
  });

  async function runToEnd(id: string, env: Record<string, string> = {}) {
    const { jobId } = await jobs.start(id, env);
    let job = jobs.get(jobId);
    for (let i = 0; i < 200 && job.status === 'running'; i++) {
      await new Promise((r) => setTimeout(r, 25));
      job = jobs.get(jobId);
    }
    return job;
  }

  it('runs venv/deps/warmup/cron via docker exec with CONTAINER paths', async () => {
    const job = await runToEnd('vmem');
    expect(job.status).toBe('completed');

    // uv venv + uv pip install --python <container venv> + warmup + hermes cron create — all via exec
    const flat = docker.calls.map((c) => c.argv.join(' '));
    expect(flat.some((a) => a.startsWith('uv venv /opt/data/modules/vmem/.venv'))).toBe(true);
    expect(flat.some((a) => a.includes('uv pip install --python /opt/data/modules/vmem/.venv/bin/python -e /opt/data/modules/vmem'))).toBe(true);
    expect(flat.some((a) => a.includes('/opt/data/modules/vmem/.venv/bin/python -c') && a.includes("print('warm')"))).toBe(true);
    expect(flat.some((a) => a.startsWith('hermes cron create'))).toBe(true);

    // mcp block + server_env use CONTAINER paths; bundle files land on the host volume
    const cfg = await fs.readFile(path.join(root, 'config.yaml'), 'utf8');
    expect(cfg).toContain('/opt/data/modules/vmem/.venv/bin/python');
    expect(cfg).toContain('/opt/data/vmem-data'); // server_env DATA_DIR resolved against HERMES_ROOT
    expect(existsSync(path.join(root, 'skills', 'memory', 's', 'SKILL.md'))).toBe(true);

    // cron launcher (on host volume) re-execs the CONTAINER venv python
    const launcher = await fs.readFile(path.join(root, 'scripts', 'vmem-sum.py'), 'utf8');
    expect(launcher).toContain('/opt/data/modules/vmem/.venv/bin/python');
    expect(launcher).toContain('/opt/data/modules/vmem/summarize_job.py');
  });

  it('installs an http MCP module with the per-client bank URL (no venv)', async () => {
    const job = await runToEnd('hmem');
    expect(job.status).toBe('completed');
    // http transport → venv/deps/warmup skipped
    expect(job.steps.find((s) => s.key === 'venv')?.status).toBe('skipped');
    // BANK_ID = container name minus `hermes-` → 'test'
    const cfg = await fs.readFile(path.join(root, 'config.yaml'), 'utf8');
    expect(cfg).toContain('http://hindsight:8888/mcp/test/');
  });

  it('copies a file credential into the install secrets dir and stores the container path', async () => {
    const job = await runToEnd('fcred', { APPLE_KEY: keyFile });
    expect(job.status).toBe('completed');

    // the .p8 was copied into <root>/secrets and .env points at the CONTAINER path
    expect(existsSync(path.join(root, 'secrets', 'AuthKey_ABC123.p8'))).toBe(true);
    const env = await fs.readFile(path.join(root, '.env'), 'utf8');
    expect(env).toContain('APPLE_KEY=/opt/data/secrets/AuthKey_ABC123.p8');
  });
});
