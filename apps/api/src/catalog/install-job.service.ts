import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { existsSync } from 'fs';
import { copyFile, mkdir, readdir, writeFile } from 'fs/promises';
import * as path from 'path';
import { DockerService } from '../core/docker.service';
import { PathService } from '../core/path.service';
import { ShellService } from '../core/shell.service';
import { ConnectedInstallation } from '../installation/installation.types';
import { InstallationService } from '../installation/installation.service';
import { CatalogService } from './catalog.service';
import { ModuleInstallerService } from './module-installer.service';

type ContainerCtx = { name: string; dataDir: string };

export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export interface InstallStep {
  key: string;
  title: string;
  status: StepStatus;
  log: string;
}

export interface InstallJob {
  id: string;
  module: string;
  status: 'running' | 'completed' | 'failed';
  steps: InstallStep[];
  result?: Record<string, unknown>;
  error?: string;
  startedAt: string;
}

const EXCLUDE_NAMES = new Set(['.venv', '__pycache__', '.git', 'node_modules']);

/**
 * Turnkey install: copies the module onto the client machine, bootstraps its
 * venv, writes the mcp/bundle files, and registers the cron job — all executed
 * by the dashboard so the operator never touches a terminal. Runs async with
 * per-step progress (poll via get()).
 */
@Injectable()
export class InstallJobService {
  private readonly logger = new Logger(InstallJobService.name);
  private readonly jobs = new Map<string, InstallJob>();
  private seq = 0;

  constructor(
    private readonly installation: InstallationService,
    private readonly catalog: CatalogService,
    private readonly installer: ModuleInstallerService,
    private readonly shell: ShellService,
    private readonly paths: PathService,
    private readonly docker: DockerService,
  ) {}

  get(jobId: string): InstallJob {
    const job = this.jobs.get(jobId);
    if (!job) throw new NotFoundException(`No install job '${jobId}'.`);
    return job;
  }

  /** Validate up-front (so obvious errors are synchronous), then run async. */
  async start(id: string, providedEnv: Record<string, string>, tools?: string[]): Promise<{ jobId: string }> {
    const install = this.installation.getActiveOrThrow();
    await this.catalog.getValid(id); // throws 404/422 before we spawn a job

    this.seq += 1;
    const jobId = `job-${this.seq}`;
    const job: InstallJob = {
      id: jobId,
      module: id,
      status: 'running',
      startedAt: new Date().toISOString(),
      steps: [
        { key: 'copy', title: 'Copy module to client', status: 'pending', log: '' },
        { key: 'venv', title: 'Create virtualenv', status: 'pending', log: '' },
        { key: 'deps', title: 'Install dependencies', status: 'pending', log: '' },
        { key: 'warmup', title: 'Warm up model', status: 'pending', log: '' },
        { key: 'configure', title: 'Write config, env & bundle', status: 'pending', log: '' },
        { key: 'cron', title: 'Register cron job', status: 'pending', log: '' },
      ],
    };
    this.jobs.set(jobId, job);
    // fire-and-forget; progress is polled
    void this.run(job, install, id, providedEnv, tools);
    return { jobId };
  }

  private step(job: InstallJob, key: string): InstallStep {
    return job.steps.find((s) => s.key === key)!;
  }

  private async run(
    job: InstallJob,
    install: ConnectedInstallation,
    id: string,
    providedEnv: Record<string, string>,
    tools?: string[],
  ): Promise<void> {
    try {
      const mod = await this.catalog.getValid(id);
      const manifest = mod.manifest;
      const isStdio = manifest.transport.type === 'stdio';
      const container = install.container;
      const warnings: string[] = [];

      // The module always lives on the host volume; `runtime*` paths are how the
      // process that runs the module sees it (the container's /opt/data when
      // containerized, the host path otherwise) and drive all written config.
      const hostModuleDir = this.paths.canonical(path.join(install.rootPath, 'modules', id));
      const runtimeModuleDir = container ? path.posix.join(container.dataDir, 'modules', id) : hostModuleDir;
      const runtimeRoot = container ? container.dataDir : install.rootPath;
      // venv interpreter: container uses the Linux layout (bin/python), host uses
      // the platform layout. hostVenvPy is the on-disk path used for reuse checks.
      const hostVenvPy = container ? path.join(hostModuleDir, '.venv', 'bin', 'python') : this.venvPython(hostModuleDir);
      const runtimeVenvPy = container
        ? path.posix.join(runtimeModuleDir, '.venv', 'bin', 'python')
        : this.paths.toForwardSlashes(this.venvPython(hostModuleDir));

      // 1) copy module to <root>/modules/<id> (host volume — shared with the container)
      const copy = this.step(job, 'copy');
      copy.status = 'running';
      await this.copyModule(mod.modulePath, hostModuleDir);
      copy.log = `${mod.modulePath} → ${hostModuleDir}${container ? ` (container: ${runtimeModuleDir})` : ''}`;
      copy.status = 'done';

      // 2) venv + 3) deps (stdio only). Idempotent: if a venv already exists
      // (reconfigure over a live install), reuse it — recreating would try to
      // overwrite the running python and fail (EPERM on Windows; in-use in the container).
      const venv = this.step(job, 'venv');
      const deps = this.step(job, 'deps');
      const venvReused = isStdio && existsSync(hostVenvPy);
      if (isStdio && !venvReused) {
        venv.status = 'running';
        if (container) {
          // In the container we use `uv` (the image ships it; its bare `python3`
          // is Hermes' own venv with no pip). uv creates the venv + installs deps.
          const r1 = await this.docker.exec(container.name, ['uv', 'venv', `${runtimeModuleDir}/.venv`], {
            timeoutMs: 120_000,
          });
          venv.log = (r1.stdout + r1.stderr).trim();
          if (!r1.ok) throw new Error(`venv creation failed (container): ${venv.log || 'unknown'}`);
          venv.status = 'done';

          deps.status = 'running';
          const r2 = await this.docker.exec(
            container.name,
            ['uv', 'pip', 'install', '--python', runtimeVenvPy, '-e', runtimeModuleDir],
            { timeoutMs: 15 * 60_000 },
          );
          deps.log = (r2.stdout + r2.stderr).slice(-4000).trim();
          if (!r2.ok) throw new Error(`uv pip install failed (see log)`);
          deps.status = 'done';
        } else {
          // Resolve a real interpreter path. Prefer `python` on Windows (avoids the
          // broken Microsoft Store `python3` alias); prefer `python3` on Ubuntu.
          const order = process.platform === 'win32' ? ['python', 'python3'] : ['python3', 'python'];
          let basePy: string | null = null;
          for (const c of order) {
            basePy = await this.shell.which(c);
            if (basePy) break;
          }
          if (!basePy) {
            throw new Error(
              'No python3/python on PATH. Install Python 3.11+ (Ubuntu: `sudo apt install python3 python3-venv`).',
            );
          }
          const r1 = await this.shell.run(basePy, ['-m', 'venv', '.venv'], { cwd: hostModuleDir, timeoutMs: 120_000 });
          venv.log = (r1.stdout + r1.stderr).trim();
          if (!r1.ok) {
            const hint = /ensurepip|venv|No module/i.test(venv.log)
              ? ' (on Ubuntu you may need `sudo apt install python3-venv`)'
              : '';
            throw new Error(`venv creation failed${hint}: ${venv.log || 'unknown'}`);
          }
          venv.status = 'done';

          deps.status = 'running';
          const r2 = await this.shell.run(hostVenvPy, ['-m', 'pip', 'install', '-e', '.'], {
            cwd: hostModuleDir,
            timeoutMs: 15 * 60_000,
          });
          deps.log = (r2.stdout + r2.stderr).slice(-4000).trim();
          if (!r2.ok) throw new Error(`pip install failed (see log)`);
          deps.status = 'done';
        }
      } else if (venvReused) {
        venv.log = 'existing venv reused (reconfigure)';
        venv.status = 'done';
        deps.log = 'dependencies assumed present (venv reused)';
        deps.status = 'done';
      } else {
        venv.status = 'skipped';
        deps.status = 'skipped';
      }

      // 3b) warm up (e.g. pre-download an embedding model) — best-effort, never fatal
      const warm = this.step(job, 'warmup');
      if (isStdio && manifest.install?.warmup && !venvReused) {
        warm.status = 'running';
        const r = container
          ? await this.docker.exec(container.name, [runtimeVenvPy, '-c', manifest.install.warmup], {
              timeoutMs: 10 * 60_000,
            })
          : await this.shell.run(hostVenvPy, ['-c', manifest.install.warmup], {
              cwd: hostModuleDir,
              timeoutMs: 10 * 60_000,
            });
        warm.log = (r.stdout + r.stderr).slice(-2000).trim();
        if (r.ok) {
          warm.status = 'done';
        } else {
          warm.status = 'failed';
          warnings.push('Warm-up failed — first model use may be slow/time out once, then it caches. ' + warm.log.slice(-200));
        }
      } else {
        warm.status = 'skipped';
      }

      // 4) write env + mcp block + bundle. Files are written to the host volume
      // (install.rootPath); the values inside them use RUNTIME (container) paths.
      const cfg = this.step(job, 'configure');
      cfg.status = 'running';
      const secretsHostDir = path.join(install.rootPath, 'secrets');
      const secretsRuntimeDir = container
        ? path.posix.join(container.dataDir, 'secrets')
        : this.paths.toForwardSlashes(secretsHostDir);
      const appliedEnvKeys = await this.installer.validateAndWriteCredentials(install, manifest, providedEnv, {
        secretsHostDir,
        secretsRuntimeDir,
      });
      const vars: Record<string, string> = container
        ? { INSTALL_DIR: runtimeModuleDir, HERMES_ROOT: runtimeRoot, VENV_PYTHON: runtimeVenvPy }
        : this.installer.buildVars(hostModuleDir, install.rootPath);
      // BANK_ID = the client id (container name minus the `hermes-` prefix) — used by http modules
      // like hindsight-memory whose URL embeds a per-client memory bank.
      vars.BANK_ID = container ? container.name.replace(/^hermes-/, '') : this.slug(path.basename(install.rootPath));
      const applied = await this.installer.applyServerAndBundle(install, hostModuleDir, manifest, tools, vars, warnings);
      cfg.log = `server '${manifest.name}', env keys: ${appliedEnvKeys.join(', ') || '(none)'}`;
      cfg.status = 'done';

      // 5) register cron via a launcher in <root>/scripts + `hermes cron create`
      const cron = this.step(job, 'cron');
      const cronJobs = manifest.provides?.cron ?? [];
      if (!cronJobs.length) {
        cron.status = 'skipped';
      } else {
        cron.status = 'running';
        const cronResults: string[] = [];
        for (const cj of cronJobs) {
          cronResults.push(await this.registerCron(install.rootPath, cj, vars, warnings, container));
        }
        cron.log = cronResults.join('\n');
        cron.status = 'done';
      }

      job.result = {
        module: id,
        serverName: manifest.name,
        localPath: hostModuleDir,
        appliedEnvKeys,
        placedSkills: applied.placedSkills,
        placedHooks: applied.placedHooks,
        warnings,
        notice: 'Restart the Hermes session to load the server, hook and skill.',
      };
      job.status = 'completed';
    } catch (err) {
      const running = job.steps.find((s) => s.status === 'running');
      if (running) {
        running.status = 'failed';
        running.log = (running.log ? running.log + '\n' : '') + (err as Error).message;
      }
      job.error = (err as Error).message;
      job.status = 'failed';
      this.logger.warn(`install job ${job.id} failed: ${job.error}`);
    }
  }

  /** Recursive copy, skipping venv/cache/git — explicit so it behaves on Windows. */
  private async copyModule(src: string, dest: string): Promise<void> {
    await mkdir(dest, { recursive: true });
    for (const entry of await readdir(src, { withFileTypes: true })) {
      if (EXCLUDE_NAMES.has(entry.name)) continue;
      const s = path.join(src, entry.name);
      const d = path.join(dest, entry.name);
      if (entry.isDirectory()) await this.copyModule(s, d);
      else if (entry.isFile()) await copyFile(s, d);
    }
  }

  private slug(name: string): string {
    return (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'default';
  }

  private venvPython(localPath: string): string {
    return process.platform === 'win32'
      ? path.join(localPath, '.venv', 'Scripts', 'python.exe')
      : path.join(localPath, '.venv', 'bin', 'python');
  }

  /**
   * Hermes runs no_agent cron scripts with ITS OWN python and only from
   * <root>/scripts/. So we drop a tiny stdlib launcher there that re-execs the
   * module's venv python on the job script, then register it with the CLI.
   */
  private async registerCron(
    rootPath: string,
    job: { id: string; name?: string; schedule: string; python?: string; script: string; env?: Record<string, string> },
    vars: Record<string, string>,
    warnings: string[],
    container?: ContainerCtx,
  ): Promise<string> {
    const sub = (v: string) => this.paths.substitute(v, vars);
    const py = job.python ? sub(job.python) : 'python';
    const script = this.paths.toForwardSlashes(sub(job.script));
    const env = Object.fromEntries(Object.entries(job.env ?? {}).map(([k, v]) => [k, sub(v)]));

    // The launcher always lives on the host volume's scripts dir (= <dataDir>/scripts
    // inside the container); its PY/SCRIPT use the runtime (container) paths from `vars`.
    const scriptsDir = path.join(rootPath, 'scripts');
    await mkdir(scriptsDir, { recursive: true });
    const scriptName = `${job.id}.py`;
    await writeFile(path.join(scriptsDir, scriptName), this.launcherSource(py, script, env), 'utf8');

    // `hermes cron create <schedule> ... --script <name-under-scripts-dir>`.
    // schedule is POSITIONAL (not --schedule); --script resolves against <root>/scripts.
    const createArgs = ['cron', 'create', job.schedule, '--no-agent', '--name', job.name ?? job.id, '--script', scriptName];

    if (container) {
      // Run the Hermes CLI INSIDE the container (it's the runtime that owns cron).
      const listed = await this.docker.exec(container.name, ['hermes', 'cron', 'list'], { timeoutMs: 30_000 });
      if (listed.ok && listed.stdout.includes(scriptName)) {
        return `${job.id}: cron already registered (skipped)`;
      }
      const res = await this.docker.exec(container.name, ['hermes', ...createArgs], { timeoutMs: 60_000 });
      if (!res.ok) {
        warnings.push(`cron registration failed for ${job.id}: ${(res.stderr || res.stdout).trim()}`);
        return `${job.id}: cron create FAILED — ${(res.stderr || res.stdout).trim().slice(0, 200)}`;
      }
      return `${job.id}: ${(res.stdout || '').trim().slice(0, 200) || 'registered (container)'}`;
    }

    const manualCmd = `hermes cron create "${job.schedule}" --no-agent --name "${job.name ?? job.id}" --script "${scriptName}"`;
    const hermes = await this.shell.which('hermes');
    if (!hermes) {
      warnings.push(`'hermes' CLI not on PATH — launcher written but cron not registered. Run: ${manualCmd}`);
      return `${job.id}: launcher written, cron NOT registered (hermes CLI missing) — ${manualCmd}`;
    }
    // Idempotent: don't create a duplicate if a job already points at this launcher.
    const listed = await this.shell.run(hermes, ['cron', 'list'], { timeoutMs: 30_000 });
    if (listed.ok && listed.stdout.includes(scriptName)) {
      return `${job.id}: cron already registered (skipped)`;
    }
    const res = await this.shell.run(hermes, createArgs, { timeoutMs: 60_000 });
    if (!res.ok) {
      warnings.push(`cron registration failed for ${job.id}: ${(res.stderr || res.stdout).trim()}`);
      return `${job.id}: cron create FAILED — ${(res.stderr || res.stdout).trim().slice(0, 200)}`;
    }
    return `${job.id}: ${(res.stdout || '').trim().slice(0, 200) || 'registered'}`;
  }

  private launcherSource(py: string, script: string, env: Record<string, string>): string {
    const lines = [
      '"""Auto-generated launcher: runs the module venv python on the cron script.',
      'Hermes runs this under its own python; we re-exec the module venv here."""',
      'import os, subprocess, sys',
      `PY = r"${py}"`,
      `SCRIPT = r"${script}"`,
      'env = dict(os.environ)',
    ];
    for (const [k, v] of Object.entries(env)) lines.push(`env[${JSON.stringify(k)}] = r"${v}"`);
    lines.push('sys.exit(subprocess.run([PY, SCRIPT], env=env).returncode)');
    return lines.join('\n') + '\n';
  }
}
