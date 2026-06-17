import {
  ConflictException,
  Injectable,
  Logger,
  NotFoundException,
  UnprocessableEntityException,
} from '@nestjs/common';
import { existsSync } from 'fs';
import * as fs from 'fs/promises';
import * as path from 'path';
import { DockerService } from '../core/docker.service';
import { PathService } from '../core/path.service';
import { ShellService } from '../core/shell.service';
import { HermesConfigService } from '../hermes-config/hermes-config.service';
import { InstallationService } from '../installation/installation.service';
import { ClientRegistryService } from './client-registry.service';
import { Client, ClientView, ImageBuildJob } from './fleet.types';

@Injectable()
export class FleetService {
  private readonly logger = new Logger(FleetService.name);
  private readonly buildJobs = new Map<string, ImageBuildJob>();
  private buildSeq = 0;
  private readonly memoryJobs = new Map<string, ImageBuildJob>();
  private memSeq = 0;

  constructor(
    private readonly registry: ClientRegistryService,
    private readonly docker: DockerService,
    private readonly installation: InstallationService,
    private readonly paths: PathService,
    private readonly shell: ShellService,
    private readonly config: HermesConfigService,
  ) {}

  private repoUrl(): string {
    return process.env.HERMES_AGENT_REPO_URL || 'git@github.com:your-org/hermes-agent.git';
  }
  /** Where the dashboard clones/maintains the Hermes source (build context). */
  private srcDir(): string {
    if (process.env.HERMES_SRC_DIR) return this.paths.canonical(process.env.HERMES_SRC_DIR);
    return this.paths.canonical(path.join(path.dirname(this.registry.clientsRoot()), 'hermes-agent-src'));
  }

  private image(): string {
    return process.env.HERMES_IMAGE || 'hermes-agent';
  }
  private uid(): string {
    return process.env.HERMES_UID || '10000';
  }
  private gid(): string {
    return process.env.HERMES_GID || '10000';
  }
  /** User-defined docker network linking the fleet to the shared Hindsight memory service. */
  private network(): string {
    return process.env.HERMES_NET || 'hermes-net';
  }
  private memoryImage(): string {
    return process.env.HINDSIGHT_IMAGE || 'ghcr.io/vectorize-io/hindsight:latest';
  }
  private memoryName(): string {
    return process.env.HINDSIGHT_NAME || 'hindsight';
  }
  private memoryModel(): string {
    return process.env.HINDSIGHT_LLM_MODEL || 'openai/gpt-4o-mini';
  }
  /** Host dir backing Hindsight's embedded Postgres (pg0) — kept next to client volumes. */
  private memoryDataDir(): string {
    return this.paths.canonical(path.join(this.registry.clientsRoot(), '_hindsight', '.pg0'));
  }
  private slug(name: string): string {
    return (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  }

  // ---- base image -------------------------------------------------------

  async imageStatus(): Promise<{ tag: string; exists: boolean; dockerOk: boolean }> {
    const dockerOk = await this.docker.available();
    return { tag: this.image(), exists: dockerOk ? await this.docker.imageExists(this.image()) : false, dockerOk };
  }

  startImageBuild(): { jobId: string } {
    this.buildSeq += 1;
    const jobId = `build-${this.buildSeq}`;
    const job: ImageBuildJob = { id: jobId, status: 'running', log: '', startedAt: new Date().toISOString() };
    this.buildJobs.set(jobId, job);
    void (async () => {
      const onLine = (l: string) => {
        job.log = (job.log + l + '\n').split('\n').slice(-60).join('\n');
      };
      try {
        const ctx = await this.ensureSource(onLine); // provided dir, or clone/pull
        onLine(`# docker build -t ${this.image()} ${ctx}`);
        const ok = await this.docker.buildImage(this.image(), ctx, onLine);
        job.status = ok ? 'completed' : 'failed';
        if (!ok) job.error = 'docker build failed (see log)';
      } catch (e) {
        job.status = 'failed';
        job.error = (e as Error).message;
        onLine(`ERROR: ${job.error}`);
      }
    })();
    return { jobId };
  }

  /** Resolve the build context: a provided source dir with a Dockerfile, else clone/pull. */
  private async ensureSource(onLine: (l: string) => void): Promise<string> {
    const provided = process.env.HERMES_AGENT_REPO;
    if (provided && existsSync(path.join(provided, 'Dockerfile'))) {
      onLine(`# using provided HERMES_AGENT_REPO: ${provided}`);
      return this.paths.canonical(provided);
    }
    const url = this.repoUrl();
    const dir = this.srcDir();
    // CRITICAL on Windows: force LF line endings. The repo ships Linux shell +
    // s6-overlay config; with the host's core.autocrlf=true, a normal clone
    // rewrites them to CRLF and the Linux container's init fails
    // (`s6-rc-compile: invalid .../type`). These -c flags keep LF.
    const lf = ['-c', 'core.autocrlf=false', '-c', 'core.eol=lf'];
    if (existsSync(path.join(dir, '.git'))) {
      onLine(`# git pull (update) in ${dir}`);
      await this.shell.runStreaming('git', [...lf, '-C', dir, 'pull', '--ff-only'], onLine, { timeoutMs: 10 * 60_000 });
    } else {
      await fs.mkdir(path.dirname(dir), { recursive: true });
      onLine(`# git clone ${url} → ${dir}`);
      const r = await this.shell.runStreaming('git', [...lf, 'clone', '--depth', '1', url, dir], onLine, {
        timeoutMs: 30 * 60_000,
      });
      if (!r.ok) {
        throw new Error('git clone failed — check SSH access to the private your-org/hermes-agent repo (see log)');
      }
    }
    if (!existsSync(path.join(dir, 'Dockerfile'))) {
      throw new Error(`No Dockerfile in ${dir} after clone/pull.`);
    }
    return dir;
  }

  getBuildJob(jobId: string): ImageBuildJob {
    const job = this.buildJobs.get(jobId);
    if (!job) throw new NotFoundException(`No build job '${jobId}'.`);
    return job;
  }

  // ---- shared Hindsight memory service ----------------------------------

  async memoryStatus() {
    const llmKeySet = !!process.env.OPENROUTER_API_KEY;
    const dockerOk = await this.docker.available();
    if (!dockerOk) {
      return { name: this.memoryName(), image: this.memoryImage(), imagePulled: false, running: false, exists: false, dockerOk: false, llmKeySet };
    }
    const status = await this.docker.status(this.memoryName());
    return {
      name: this.memoryName(),
      image: this.memoryImage(),
      imagePulled: await this.docker.imageExists(this.memoryImage()),
      running: status === 'running',
      exists: status !== null,
      dockerOk: true,
      llmKeySet,
    };
  }

  /** Pull + run the shared Hindsight container on hermes-net, then attach all clients. Streamed. */
  startMemory(): { jobId: string } {
    if (!process.env.OPENROUTER_API_KEY) {
      throw new UnprocessableEntityException(
        'OPENROUTER_API_KEY is not set on the dashboard — set it before starting the Hindsight memory service.',
      );
    }
    this.memSeq += 1;
    const jobId = `memory-${this.memSeq}`;
    const job: ImageBuildJob = { id: jobId, status: 'running', log: '', startedAt: new Date().toISOString() };
    this.memoryJobs.set(jobId, job);
    const onLine = (l: string) => {
      job.log = (job.log + l + '\n').split('\n').slice(-60).join('\n');
    };
    void (async () => {
      try {
        const net = this.network();
        const name = this.memoryName();
        onLine(`# ensure network ${net}`);
        const n = await this.docker.ensureNetwork(net);
        if (!n.ok) throw new Error(`network create failed: ${n.error}`);

        onLine(`# docker pull ${this.memoryImage()}`);
        if (!(await this.docker.pull(this.memoryImage(), onLine))) throw new Error('docker pull failed (see log)');

        const status = await this.docker.status(name);
        if (status === null) {
          await fs.mkdir(this.memoryDataDir(), { recursive: true });
          onLine(`# docker run ${name} (network ${net}, UI 127.0.0.1:9999)`);
          const res = await this.docker.run({
            name,
            image: this.memoryImage(),
            network: net,
            restart: true,
            volumes: [{ host: this.memoryDataDir(), container: '/home/hindsight/.pg0' }],
            extraArgs: ['-p', '127.0.0.1:9999:9999'],
            env: {
              HINDSIGHT_API_LLM_PROVIDER: 'openrouter',
              HINDSIGHT_API_LLM_API_KEY: process.env.OPENROUTER_API_KEY!,
              HINDSIGHT_API_LLM_MODEL: this.memoryModel(),
              HINDSIGHT_API_LLM_SEND_BANK_AS_USER: 'true',
            },
          });
          if (!res.ok) throw new Error(`docker run failed: ${res.error}`);
        } else if (status !== 'running') {
          onLine(`# docker start ${name}`);
          const s = await this.docker.start(name);
          if (!s.ok) throw new Error(`docker start failed: ${s.error}`);
        } else {
          onLine(`# ${name} already running`);
        }

        // Attach every registered client to the network so they can resolve `hindsight`.
        for (const c of await this.registry.list()) {
          const r = await this.docker.connectNetwork(net, c.containerName);
          onLine(`# connect ${c.containerName}: ${r.ok ? 'ok' : r.error}`);
        }
        job.status = 'completed';
      } catch (e) {
        job.status = 'failed';
        job.error = (e as Error).message;
        onLine(`ERROR: ${job.error}`);
      }
    })();
    return { jobId };
  }

  getMemoryJob(jobId: string): ImageBuildJob {
    const job = this.memoryJobs.get(jobId);
    if (!job) throw new NotFoundException(`No memory job '${jobId}'.`);
    return job;
  }

  async memoryStop() {
    return this.docker.stop(this.memoryName());
  }
  async memoryRemove() {
    return this.docker.rm(this.memoryName());
  }
  async memoryLogs(tail = 200): Promise<{ logs: string }> {
    return { logs: await this.docker.logs(this.memoryName(), tail) };
  }

  // ---- clients ----------------------------------------------------------

  async listClients(): Promise<ClientView[]> {
    const clients = await this.registry.list();
    return Promise.all(
      clients.map(async (c) => ({
        ...c,
        status: (await this.docker.status(c.containerName)) ?? 'missing',
        setupDone: existsSync(path.join(c.hostDir, 'config.yaml')),
      })),
    );
  }

  setupCommand(c: Client): string {
    return `docker exec -it ${c.containerName} hermes setup`;
  }

  async createClient(name: string): Promise<{ client: Client; setupCommand: string }> {
    const id = this.slug(name);
    if (!id) throw new UnprocessableEntityException('A non-empty client name is required.');
    if (await this.registry.get(id)) throw new ConflictException(`Client '${id}' already exists.`);
    if (!(await this.docker.imageExists(this.image()))) {
      throw new UnprocessableEntityException(`Base image '${this.image()}' not built yet — build it first.`);
    }

    const hostDir = this.paths.canonical(path.join(this.registry.clientsRoot(), id, '.hermes'));
    await fs.mkdir(hostDir, { recursive: true });
    const containerName = `hermes-${id}`;

    await this.docker.ensureNetwork(this.network()); // `docker run --network` fails if it doesn't exist yet
    const res = await this.docker.run({
      name: containerName,
      image: this.image(),
      network: this.network(), // join hermes-net so the agent can reach the shared `hindsight` memory
      volumes: [{ host: hostDir, container: '/opt/data' }],
      env: { HERMES_UID: this.uid(), HERMES_GID: this.gid() },
      restart: true,
      // Keep the container stably alive for provisioning instead of the default
      // interactive `hermes` (which exits without a TTY → crash loop). cont-init
      // still seeds bundled skills. The gateway is launched on demand (see below).
      command: ['sleep', 'infinity'],
    });
    if (!res.ok) {
      throw new UnprocessableEntityException(`docker run failed: ${res.error}`);
    }

    const client: Client = {
      id,
      name,
      containerName,
      hostDir: this.paths.toForwardSlashes(hostDir),
      createdAt: new Date().toISOString(),
    };
    await this.registry.add(client);
    return { client, setupCommand: this.setupCommand(client) };
  }

  private async require(id: string): Promise<Client> {
    const c = await this.registry.get(id);
    if (!c) throw new NotFoundException(`No client '${id}'.`);
    return c;
  }

  async start(id: string) {
    return this.docker.start((await this.require(id)).containerName);
  }
  async stop(id: string) {
    return this.docker.stop((await this.require(id)).containerName);
  }
  /** Restart the container; if the gateway was running, relaunch it (a plain
   *  `docker restart` kills the exec-started gateway and would not bring it back). */
  async restart(id: string): Promise<{ ok: boolean; gatewayRelaunched?: boolean; error?: string }> {
    const c = await this.require(id);
    const wasUp = await this.gatewayRunning(c.containerName);
    const r = await this.docker.restart(c.containerName);
    if (!r.ok) return r;
    let gatewayRelaunched = false;
    if (wasUp) {
      const g = await this.docker.exec(c.containerName, ['hermes', 'gateway', 'start'], { timeoutMs: 60_000 });
      gatewayRelaunched = g.ok;
      if (!g.ok) this.logger.warn(`gateway relaunch after restart failed for ${c.containerName}: ${(g.stderr || g.stdout).trim()}`);
    }
    return { ok: true, gatewayRelaunched };
  }

  /** Best-effort check whether the background gateway service is currently up. */
  private async gatewayRunning(name: string): Promise<boolean> {
    const r = await this.docker.exec(name, ['hermes', 'gateway', 'status'], { timeoutMs: 20_000 });
    if (!r.ok) return false;
    const out = (r.stdout + ' ' + r.stderr).toLowerCase();
    if (/stopped|not running|inactive|\bdown\b|no .*gateway/.test(out)) return false;
    return /running|active|\bup\b|online|listening|started/.test(out);
  }

  async remove(id: string, removeData = false): Promise<{ removed: boolean }> {
    const c = await this.require(id);
    await this.docker.rm(c.containerName); // -f; ignore if already gone
    await this.registry.remove(id);
    if (removeData) {
      await fs.rm(path.dirname(c.hostDir), { recursive: true, force: true }).catch(() => undefined);
    }
    return { removed: true };
  }

  async logs(id: string, tail = 200): Promise<{ logs: string }> {
    return { logs: await this.docker.logs((await this.require(id)).containerName, tail) };
  }

  /** Start the messaging gateway as a managed background service inside the container. */
  async startGateway(id: string): Promise<{ ok: boolean; error?: string }> {
    const c = await this.require(id);
    const r = await this.docker.exec(c.containerName, ['hermes', 'gateway', 'start'], { timeoutMs: 60_000 });
    return r.ok ? { ok: true } : { ok: false, error: (r.stderr || r.stdout).trim() };
  }

  /** Stop the background gateway service inside the container. */
  async stopGateway(id: string): Promise<{ ok: boolean; error?: string }> {
    const c = await this.require(id);
    const r = await this.docker.exec(c.containerName, ['hermes', 'gateway', 'stop'], { timeoutMs: 60_000 });
    return r.ok ? { ok: true } : { ok: false, error: (r.stderr || r.stdout).trim() };
  }

  async gatewayStatus(id: string): Promise<{ status: string }> {
    const c = await this.require(id);
    const r = await this.docker.exec(c.containerName, ['hermes', 'gateway', 'status'], { timeoutMs: 20_000 });
    return { status: (r.stdout + (r.stderr ? '\n' + r.stderr : '')).trim().slice(-1500) };
  }

  /** Point the existing dashboard (skills/mcp/catalog) at this client's volume.
   *  Pass the container context so the install pipeline runs INSIDE the
   *  container (docker exec) with container paths under /opt/data. */
  async select(id: string) {
    const c = await this.require(id);
    return this.installation.connect(c.hostDir, { name: c.containerName, dataDir: '/opt/data' });
  }

  // ---- per-client memory provider (Hindsight native) -------------------

  /** Exact config the Hermes native hindsight plugin reads from $HERMES_HOME/hindsight/config.json.
   *  Self-hosted server → `local_external`; bank lives in THREE places, all = the client id. */
  private hindsightProviderConfig(bank: string) {
    return {
      mode: 'local_external',
      apiKey: '',
      timeout: 120,
      idle_timeout: 300,
      retain_tags: '',
      observation_scopes: '',
      retain_source: '',
      retain_user_prefix: 'User',
      retain_assistant_prefix: 'Assistant',
      banks: { [bank]: { bankId: bank, budget: 'mid', enabled: true } },
      api_url: `http://${this.memoryName()}:8888`,
      bank_id: bank,
      recall_budget: 'mid',
    };
  }

  /** Reload the gateway so a memory-provider change takes effect (loads at session start). */
  private async reloadGateway(containerName: string): Promise<boolean> {
    if (!(await this.gatewayRunning(containerName))) return false;
    await this.docker.exec(containerName, ['hermes', 'gateway', 'stop'], { timeoutMs: 60_000 });
    const s = await this.docker.exec(containerName, ['hermes', 'gateway', 'start'], { timeoutMs: 60_000 });
    return s.ok;
  }

  /** Current memory provider for a client (best-effort; needs the container running). */
  async memoryProviderStatus(id: string): Promise<{ provider: string; bank: string | null }> {
    const c = await this.require(id);
    const r = await this.docker.exec(c.containerName, ['hermes', 'memory', 'status'], { timeoutMs: 20_000 });
    const active = r.ok && /provider:\s*hindsight/i.test(r.stdout);
    return { provider: active ? 'hindsight' : 'none', bank: active ? c.id : null };
  }

  /** One-click: make Hindsight this client's native memory provider (bank = client id),
   *  disable the redundant MCP hindsight block, and reload the gateway. */
  async enableHindsightMemory(id: string): Promise<{ ok: boolean; bank: string; reloaded: boolean; warning?: string }> {
    const c = await this.require(id);
    const bank = c.id;
    const hostDir = this.paths.canonical(c.hostDir);

    // 1) provider config the plugin reads verbatim
    const cfgDir = path.join(hostDir, 'hindsight');
    await fs.mkdir(cfgDir, { recursive: true });
    await fs.writeFile(path.join(cfgDir, 'config.json'), JSON.stringify(this.hindsightProviderConfig(bank), null, 2), 'utf8');

    // 2) activate provider in config.yaml; turn off MCP hindsight (use native OR MCP, not both)
    await this.config.mutate(hostDir, (doc) => {
      doc.setIn(['memory', 'provider'], 'hindsight');
      if (doc.hasIn(['mcp_servers', 'hindsight'])) doc.setIn(['mcp_servers', 'hindsight', 'enabled'], false);
    });

    // 3) reload so it takes effect
    const reloaded = await this.reloadGateway(c.containerName);
    const warnings: string[] = [];
    if (!reloaded) warnings.push('Gateway not running — memory loads when you Start Background Service.');
    if ((await this.docker.status(this.memoryName())) !== 'running') {
      warnings.push('Hindsight memory service is not running — start it on the Agents screen.');
    }
    return { ok: true, bank, reloaded, warning: warnings.join(' ') || undefined };
  }

  /** Revert to Hermes built-in memory only. */
  async disableMemory(id: string): Promise<{ ok: boolean; reloaded: boolean; error?: string }> {
    const c = await this.require(id);
    const r = await this.docker.exec(c.containerName, ['hermes', 'memory', 'off'], { timeoutMs: 30_000 });
    const reloaded = await this.reloadGateway(c.containerName);
    return { ok: r.ok, reloaded, error: r.ok ? undefined : (r.stderr || r.stdout).trim() };
  }
}
