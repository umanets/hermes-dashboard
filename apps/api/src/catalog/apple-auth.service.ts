import { Injectable, Logger, NotFoundException, UnprocessableEntityException } from '@nestjs/common';
import { existsSync } from 'fs';
import { readFile } from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import { DockerService } from '../core/docker.service';
import { ShellService } from '../core/shell.service';
import { HermesConfigService } from '../hermes-config/hermes-config.service';
import { ConnectedInstallation } from '../installation/installation.types';
import { InstallationService } from '../installation/installation.service';
import { CatalogService } from './catalog.service';

export interface AppleAuthJob {
  id: string;
  status: 'running' | 'completed' | 'failed';
  message: string;
  error?: string;
  startedAt: string;
}

/**
 * Apple Music User Token (MUT) capture for the selected install's `music` MCP.
 *
 * The MUT can only be minted by MusicKit JS in a browser after an Apple-ID sign-in,
 * which cannot happen inside the (headless) container — so the browser flow runs HOST-side
 * via the music module's own CLI helper. Once captured, the token is written straight into
 * `mcp_servers.<server>.env.APPLE_MUSIC_USER_TOKEN` of the selected client's config (the
 * deterministic spot Hermes injects into the stdio process), and the MCP is reloaded.
 * The raw token is never returned over the API — only a masked confirmation.
 */
@Injectable()
export class AppleAuthService {
  private readonly logger = new Logger(AppleAuthService.name);
  private readonly jobs = new Map<string, AppleAuthJob>();
  private seq = 0;

  constructor(
    private readonly installation: InstallationService,
    private readonly catalog: CatalogService,
    private readonly config: HermesConfigService,
    private readonly shell: ShellService,
    private readonly docker: DockerService,
  ) {}

  getJob(jobId: string): AppleAuthJob {
    const job = this.jobs.get(jobId);
    if (!job) throw new NotFoundException(`No apple-auth job '${jobId}'.`);
    return job;
  }

  /** Run the host browser authorize flow, then write+reload the captured token. Async (poll getJob). */
  async start(server = 'music'): Promise<{ jobId: string }> {
    this.installation.getActiveOrThrow();
    const src = await this.musicSource();
    const venvPy = this.hostVenvPython(src);
    if (!venvPy) {
      throw new UnprocessableEntityException(
        `No host venv for the music module at ${src}. Bootstrap it first: python -m venv .venv && .venv/Scripts/pip install -e .`,
      );
    }

    this.seq += 1;
    const jobId = `apple-${this.seq}`;
    const job: AppleAuthJob = {
      id: jobId,
      status: 'running',
      message: 'Opening the Apple Music sign-in page in your browser — complete it there…',
      startedAt: new Date().toISOString(),
    };
    this.jobs.set(jobId, job);

    void (async () => {
      try {
        // Blocks until the helper captures a token (browser sign-in) and exits, or times out.
        const r = await this.shell.run(venvPy, ['-m', 'music_service.cli', 'auth', 'apple'], {
          cwd: src,
          timeoutMs: 10 * 60_000,
        });
        if (!r.ok) {
          throw new Error((r.stderr || r.stdout || 'authorize helper failed').trim().slice(-400) || 'authorize helper failed');
        }
        const token = await this.readStoredToken();
        job.message = await this.applyToken(token, server);
        job.status = 'completed';
      } catch (e) {
        job.error = (e as Error).message;
        job.status = 'failed';
        this.logger.warn(`apple authorize ${jobId} failed: ${job.error}`);
      }
    })();

    return { jobId };
  }

  /** Manual path: write a pasted MUT into the install + reload. */
  async setToken(token: string, server = 'music'): Promise<{ message: string }> {
    this.installation.getActiveOrThrow();
    const t = (token || '').trim();
    if (!t) throw new UnprocessableEntityException('Empty token.');
    return { message: await this.applyToken(t, server) };
  }

  // ---- internals -------------------------------------------------------

  private async applyToken(token: string, server: string): Promise<string> {
    const install = this.installation.getActiveOrThrow();
    await this.config.mutate(install.rootPath, (doc) => {
      doc.setIn(['mcp_servers', server, 'env', 'APPLE_MUSIC_USER_TOKEN'], token);
    });
    const reload = await this.reload(install, server);
    return `Token (…${token.slice(-6)}) written to mcp_servers.${server}.env. ${reload}`;
  }

  /** The running MCP doesn't hot-reload env → restart so it picks up the token. */
  private async reload(install: ConnectedInstallation, _server: string): Promise<string> {
    const c = install.container;
    if (!c) return 'Restart the Hermes session to apply.';
    const status = await this.docker.status(c.name);
    if (status !== 'running') return 'Saved — it will load when the agent next starts.';
    const r = await this.docker.restart(c.name);
    if (!r.ok) return `Saved, but container restart failed (${r.error}) — restart it manually to apply.`;
    await this.docker.exec(c.name, ['hermes', 'gateway', 'start'], { timeoutMs: 60_000 });
    return 'Container restarted and gateway relaunched — music is ready.';
  }

  /** Host source checkout of the music module (has the Windows/Linux .venv + dev-cred .env). */
  private async musicSource(): Promise<string> {
    const mods = await this.catalog.list();
    const m = mods.find((x) => x.manifest?.name === 'music') ?? mods.find((x) => x.id === 'apple-tidal');
    if (!m) throw new UnprocessableEntityException('Music module not found in the catalog.');
    return m.modulePath;
  }

  private hostVenvPython(src: string): string | null {
    const cands = [path.join(src, '.venv', 'Scripts', 'python.exe'), path.join(src, '.venv', 'bin', 'python')];
    return cands.find((p) => existsSync(p)) ?? null;
  }

  private storePath(): string {
    return path.join(os.homedir(), '.hermes', 'music_tools', 'apple_tokens.json');
  }

  private async readStoredToken(): Promise<string> {
    const p = this.storePath();
    if (!existsSync(p)) throw new Error(`Authorize finished but no token file at ${p}.`);
    let data: { music_user_token?: string };
    try {
      data = JSON.parse(await readFile(p, 'utf8'));
    } catch {
      throw new Error(`Could not parse the token file at ${p}.`);
    }
    const t = (data?.music_user_token || '').trim();
    if (!t) throw new Error('The token file has no music_user_token.');
    return t;
  }
}
