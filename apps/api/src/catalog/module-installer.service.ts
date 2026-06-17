import { Injectable, UnprocessableEntityException } from '@nestjs/common';
import { existsSync } from 'fs';
import { copyFile, mkdir, readFile, readdir, writeFile } from 'fs/promises';
import * as path from 'path';
import { EnvFileService } from '../core/env-file.service';
import { PathService } from '../core/path.service';
import { HermesConfigService, RawMcpServer } from '../hermes-config/hermes-config.service';
import { InstallationService } from '../installation/installation.service';
import { CatalogService } from './catalog.service';
import { HermesManifest, ManifestCronJob } from './manifest.types';

const TEXT_EXT = new Set([
  '.py', '.yaml', '.yml', '.md', '.json', '.txt', '.cfg', '.toml', '.ini', '.sh', '.ps1',
]);

export interface CredentialField {
  name: string;
  prompt: string;
  default?: string;
  required: boolean;
  secret: boolean;
  /** value is a host path to a file that gets copied into the install's secrets dir */
  file: boolean;
  present: boolean;
}

export interface PlacedCron {
  id: string;
  specFile: string;
  command: string;
}

export interface InstallResult {
  module: string;
  serverName: string;
  appliedEnvKeys: string[];
  server: RawMcpServer;
  serverEnv: Record<string, string>;
  placedSkills: string[];
  placedHooks: string[];
  cron: PlacedCron[];
  warnings: string[];
  bootstrap: string[];
  postInstall?: string;
  notice: string;
}

@Injectable()
export class ModuleInstallerService {
  constructor(
    private readonly installation: InstallationService,
    private readonly catalog: CatalogService,
    private readonly env: EnvFileService,
    private readonly config: HermesConfigService,
    private readonly paths: PathService,
  ) {}

  /** Fields to render in the install dialog, with current .env presence. */
  async credentialForm(id: string): Promise<{ module: string; fields: CredentialField[] }> {
    const install = this.installation.getActiveOrThrow();
    const mod = await this.catalog.getValid(id);
    const existing = await this.env.read(install.envPath);
    const fields = mod.manifest.auth.env.map((e) => ({
      name: e.name,
      prompt: e.prompt,
      default: e.default,
      required: e.required,
      secret: e.secret,
      file: !!e.file,
      present: existing.has(e.name),
    }));
    return { module: id, fields };
  }

  /**
   * Legacy file-only install (no shell): writes against the catalog module path
   * in place and emits a cron SPEC + command. Kept for the manual path and for
   * unit tests. The full turnkey flow is InstallJobService (copy+venv+cron).
   */
  async install(id: string, providedEnv: Record<string, string>, tools?: string[]): Promise<InstallResult> {
    const install = this.installation.getActiveOrThrow();
    const mod = await this.catalog.getValid(id);
    const manifest = mod.manifest;
    const warnings: string[] = [];

    const secretsHostDir = path.join(install.rootPath, 'secrets');
    const appliedEnvKeys = await this.validateAndWriteCredentials(install, manifest, providedEnv, {
      secretsHostDir,
      secretsRuntimeDir: this.paths.toForwardSlashes(secretsHostDir),
    });
    const vars = this.buildVars(mod.modulePath, install.rootPath);
    const { server, serverEnv, placedSkills, placedHooks } = await this.applyServerAndBundle(
      install,
      mod.modulePath,
      manifest,
      tools,
      vars,
      warnings,
    );

    const cron: PlacedCron[] = [];
    for (const job of manifest.provides?.cron ?? []) {
      cron.push(await this.writeCronSpec(install.rootPath, job, vars));
    }
    if (cron.length) {
      warnings.push(
        'Cron written as a spec file only — register with the printed `hermes cron create` command.',
      );
    }

    return {
      module: id,
      serverName: manifest.name,
      appliedEnvKeys,
      server,
      serverEnv,
      placedSkills,
      placedHooks,
      cron,
      warnings,
      bootstrap: manifest.install?.bootstrap ?? [],
      postInstall: manifest.post_install,
      notice: 'Restart the Hermes session to load the server, hook and skill.',
    };
  }

  // ---- reusable steps (shared with InstallJobService) ------------------

  /** Resolution vars for the server block + bundle artifacts. */
  buildVars(modulePath: string, rootPath: string): Record<string, string> {
    return {
      INSTALL_DIR: modulePath,
      HERMES_ROOT: rootPath,
      VENV_PYTHON: this.findVenvPython(modulePath) ?? 'python',
    };
  }

  /**
   * Validate required creds present, persist declared non-empty values. Returns written keys.
   * For env vars marked `file: true`, the provided value is a HOST path: the file is copied
   * into the install's secrets dir and the stored value becomes the resolved path that the
   * server process will see (a container path under `secretsRuntimeDir` when containerized).
   */
  async validateAndWriteCredentials(
    install: { envPath: string },
    manifest: HermesManifest,
    providedEnv: Record<string, string>,
    fileCtx?: { secretsHostDir: string; secretsRuntimeDir: string },
  ): Promise<string[]> {
    const declared = new Map(manifest.auth.env.map((e) => [e.name, e]));
    const existing = await this.env.read(install.envPath);
    for (const e of manifest.auth.env) {
      if (!e.required) continue;
      const incoming = providedEnv[e.name];
      const hasValue = (incoming !== undefined && incoming !== '') || existing.has(e.name) || e.default !== undefined;
      if (!hasValue) throw new UnprocessableEntityException(`Required credential '${e.name}' is missing.`);
    }
    const toWrite: Record<string, string> = {};
    for (const [key, value] of Object.entries(providedEnv)) {
      const decl = declared.get(key);
      if (!decl) continue;
      if (value === undefined || value === '') continue;
      if (decl.file) {
        toWrite[key] = await this.placeCredentialFile(key, value, fileCtx);
      } else {
        toWrite[key] = value;
      }
    }
    if (Object.keys(toWrite).length) await this.env.setMany(install.envPath, toWrite);
    return Object.keys(toWrite);
  }

  /** Copy a host credential file into the install's secrets dir; return the resolved (runtime) path. */
  private async placeCredentialFile(
    key: string,
    hostPath: string,
    fileCtx?: { secretsHostDir: string; secretsRuntimeDir: string },
  ): Promise<string> {
    if (!fileCtx) return hostPath; // no install context → store the path as-is
    const src = this.paths.canonical(hostPath);
    if (!existsSync(src)) {
      throw new UnprocessableEntityException(`Credential file for '${key}' not found: ${hostPath}`);
    }
    const base = path.basename(src);
    await mkdir(fileCtx.secretsHostDir, { recursive: true });
    await copyFile(src, path.join(fileCtx.secretsHostDir, base));
    return this.paths.toForwardSlashes(path.posix.join(fileCtx.secretsRuntimeDir, base));
  }

  /**
   * Write the mcp_servers block + copy skills/hooks. `vars` drives all path
   * resolution (INSTALL_DIR/HERMES_ROOT/VENV_PYTHON) so a container install
   * gets container paths; `copySourceDir` is the HOST dir bundle files are read
   * from (the module copy), and bundle files are written under `install.rootPath`
   * (the host volume) regardless of container vs host.
   */
  async applyServerAndBundle(
    install: { rootPath: string },
    copySourceDir: string,
    manifest: HermesManifest,
    tools: string[] | undefined,
    vars: Record<string, string>,
    warnings: string[],
  ): Promise<{ server: RawMcpServer; serverEnv: Record<string, string>; placedSkills: string[]; placedHooks: string[] }> {
    const serverEnv = this.resolveServerEnv(manifest, vars);
    const server = this.buildServerBlock(manifest, tools, warnings, serverEnv, vars);
    await this.config.mutate(install.rootPath, (doc) => {
      doc.setIn(['mcp_servers', manifest.name], server);
    });

    const placedSkills: string[] = [];
    const placedHooks: string[] = [];
    for (const s of manifest.provides?.skills ?? []) {
      placedSkills.push(await this.placeTree(copySourceDir, install.rootPath, s.src, s.dest, false, vars, warnings));
    }
    for (const h of manifest.provides?.hooks ?? []) {
      placedHooks.push(await this.placeTree(copySourceDir, install.rootPath, h.src, h.dest, !!h.template, vars, warnings));
    }
    return { server, serverEnv, placedSkills: placedSkills.filter(Boolean), placedHooks: placedHooks.filter(Boolean) };
  }

  // ---- server block ----------------------------------------------------

  private resolveServerEnv(manifest: HermesManifest, vars: Record<string, string>): Record<string, string> {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(manifest.provides?.server_env ?? {})) {
      const resolved = this.paths.substitute(v, vars);
      this.assertResolved([resolved]);
      out[k] = resolved;
    }
    return out;
  }

  private buildServerBlock(
    manifest: HermesManifest,
    tools: string[] | undefined,
    warnings: string[],
    serverEnv: Record<string, string>,
    vars: Record<string, string>,
  ): RawMcpServer {
    const server: RawMcpServer = { enabled: true };

    if (manifest.transport.type === 'stdio') {
      let command = this.paths.toForwardSlashes(this.paths.substitute(manifest.transport.command ?? '', vars));
      const args = (manifest.transport.args ?? []).map((a) =>
        this.paths.toForwardSlashes(this.paths.substitute(a, vars)),
      );
      // Swap a bare `python`/`python3` for the module's venv interpreter
      // (vars.VENV_PYTHON — a container path when containerized). 'python'
      // is the buildVars fallback meaning "no venv found".
      const venv = vars.VENV_PYTHON && vars.VENV_PYTHON !== 'python' ? vars.VENV_PYTHON : null;
      if (this.looksLikeBarePython(command) && venv) command = venv;
      else if (this.looksLikeBarePython(command) && !venv) {
        warnings.push(
          `No virtualenv resolved for the server command — using '${command}' from PATH. Run the module's bootstrap first.`,
        );
      }
      this.assertResolved([command, ...args]);
      server.command = command;
      server.args = args;
    } else {
      const url = this.paths.substitute(manifest.transport.url ?? '', vars);
      this.assertResolved([url]);
      server.url = url;
    }

    if (Object.keys(serverEnv).length) server.env = serverEnv;

    const toolList = tools ?? manifest.tools.default_enabled;
    if (toolList && toolList.length) server.tools = { include: toolList };
    return server;
  }

  // ---- bundle file placement -------------------------------------------

  /** Copy a folder from the module into the install, optionally templating ${...}. */
  private async placeTree(
    modulePath: string,
    rootPath: string,
    src: string,
    dest: string,
    template: boolean,
    vars: Record<string, string>,
    warnings: string[],
  ): Promise<string> {
    const absSrc = path.resolve(modulePath, src);
    const absDest = path.resolve(rootPath, dest);
    if (!absDest.startsWith(path.resolve(rootPath))) {
      throw new UnprocessableEntityException(`Refusing to write outside the install: ${dest}`);
    }
    if (!existsSync(absSrc)) {
      warnings.push(`Bundle source missing, skipped: ${src}`);
      return '';
    }
    await this.copyDir(absSrc, absDest, template, vars);
    return this.paths.toForwardSlashes(absDest);
  }

  private async copyDir(src: string, dest: string, template: boolean, vars: Record<string, string>): Promise<void> {
    await mkdir(dest, { recursive: true });
    for (const entry of await readdir(src, { withFileTypes: true })) {
      if (entry.name === '__pycache__') continue;
      const s = path.join(src, entry.name);
      const d = path.join(dest, entry.name);
      if (entry.isDirectory()) {
        await this.copyDir(s, d, template, vars);
      } else if (template && TEXT_EXT.has(path.extname(entry.name))) {
        const content = await readFile(s, 'utf8');
        await writeFile(d, this.paths.substitute(content, vars), 'utf8');
      } else {
        await copyFile(s, d);
      }
    }
  }

  /**
   * Write a resolved cron job spec next to jobs.json (NOT into jobs.json — its
   * live schema/normalization is owned by Hermes' cron API). Returns the exact
   * `hermes cron create` command to register it reliably.
   */
  private async writeCronSpec(
    rootPath: string,
    job: ManifestCronJob,
    vars: Record<string, string>,
  ): Promise<PlacedCron> {
    const sub = (v: string) => this.paths.substitute(v, vars);
    const resolved = {
      id: job.id,
      name: job.name,
      schedule: { value: job.schedule },
      no_agent: job.no_agent ?? false,
      python: job.python ? sub(job.python) : undefined,
      script: this.paths.toForwardSlashes(sub(job.script)),
      workdir: job.workdir ? this.paths.toForwardSlashes(sub(job.workdir)) : undefined,
      env: Object.fromEntries(Object.entries(job.env ?? {}).map(([k, v]) => [k, sub(v)])),
      enabled: true,
      origin: { source: 'hermes-dashboard' },
    };
    const cronDir = path.join(rootPath, 'cron');
    await mkdir(cronDir, { recursive: true });
    const specFile = path.join(cronDir, `${job.id}.job.json`);
    await writeFile(specFile, JSON.stringify(resolved, null, 2), 'utf8');

    const py = resolved.python ?? 'python';
    const command =
      `hermes cron create --name "${job.name ?? job.id}" --schedule "${job.schedule}" --no-agent ` +
      `--script "${py} ${resolved.script}"`;
    return { id: job.id, specFile: this.paths.toForwardSlashes(specFile), command };
  }

  // ---- helpers ---------------------------------------------------------

  private looksLikeBarePython(command: string): boolean {
    const base = command.split('/').pop() ?? command;
    return base === 'python' || base === 'python3' || base === 'python.exe';
  }

  private findVenvPython(modulePath: string): string | null {
    const candidates = [
      path.join(modulePath, '.venv', 'Scripts', 'python.exe'),
      path.join(modulePath, '.venv', 'bin', 'python'),
    ];
    for (const c of candidates) {
      if (existsSync(c)) return this.paths.toForwardSlashes(c);
    }
    return null;
  }

  private assertResolved(values: string[]): void {
    const unresolved = values.flatMap((v) => this.paths.findUnresolved(v));
    if (unresolved.length) {
      throw new UnprocessableEntityException(
        `Unresolved placeholders in server config: ${[...new Set(unresolved)].join(', ')}`,
      );
    }
  }
}
