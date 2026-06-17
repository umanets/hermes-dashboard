import { Injectable, Logger } from '@nestjs/common';
import { ShellService } from './shell.service';

export interface RunOptions {
  name: string;
  image: string;
  volumes?: { host: string; container: string }[];
  env?: Record<string, string>;
  restart?: boolean;
  /** attach to a user-defined network (container reachable by its name as a DNS alias) */
  network?: string;
  extraArgs?: string[];
  /** container command (after the image) — e.g. ['sleep','infinity'] or ['gateway','run'] */
  command?: string[];
}

/**
 * Thin wrapper over the local `docker` CLI (via ShellService). The dashboard
 * runs on the same host as Docker, so every method shells out to `docker`.
 * Cross-platform: `docker` resolves on Windows + Linux alike.
 */
@Injectable()
export class DockerService {
  private readonly logger = new Logger(DockerService.name);

  constructor(private readonly shell: ShellService) {}

  // Use the bare command: Node/libuv resolves `docker.exe` on Windows and
  // `docker` on Linux. (Resolving via `where` can return the extensionless
  // wrapper first, which CreateProcess can't execute on Windows.)
  private async docker(): Promise<string> {
    return process.env.DOCKER_BIN || 'docker';
  }

  async available(): Promise<boolean> {
    const d = await this.docker();
    const r = await this.shell.run(d, ['version', '--format', '{{.Server.Version}}'], { timeoutMs: 10_000 });
    return r.ok;
  }

  async imageExists(tag: string): Promise<boolean> {
    const d = await this.docker();
    const r = await this.shell.run(d, ['image', 'inspect', tag], { timeoutMs: 15_000 });
    return r.ok;
  }

  /** Pull an image; streams progress line-by-line to onLine. */
  async pull(tag: string, onLine: (l: string) => void): Promise<boolean> {
    const d = await this.docker();
    const r = await this.shell.runStreaming(d, ['pull', tag], onLine, { timeoutMs: 30 * 60_000 });
    return r.ok;
  }

  async networkExists(name: string): Promise<boolean> {
    const d = await this.docker();
    const r = await this.shell.run(d, ['network', 'inspect', name], { timeoutMs: 15_000 });
    return r.ok;
  }

  /** Create a user-defined bridge network if it doesn't already exist. */
  async ensureNetwork(name: string): Promise<{ ok: boolean; error?: string }> {
    if (await this.networkExists(name)) return { ok: true };
    return this.simple(['network', 'create', name]);
  }

  /** Attach a running container to a network; treats "already connected" as success. */
  async connectNetwork(network: string, container: string): Promise<{ ok: boolean; error?: string }> {
    const d = await this.docker();
    const r = await this.shell.run(d, ['network', 'connect', network, container], { timeoutMs: 30_000 });
    if (r.ok) return { ok: true };
    const msg = (r.stderr || r.stdout).trim();
    if (/already exists in network|already connected/i.test(msg)) return { ok: true };
    return { ok: false, error: msg };
  }

  /** Build an image; streams build output line-by-line to onLine. */
  async buildImage(tag: string, contextDir: string, onLine: (l: string) => void): Promise<boolean> {
    const d = await this.docker();
    const r = await this.shell.runStreaming(
      d,
      ['build', '--progress=plain', '-t', tag, contextDir],
      onLine,
      { timeoutMs: 60 * 60_000 },
    );
    return r.ok;
  }

  /** docker run -d ... ; returns container id on success. */
  async run(opts: RunOptions): Promise<{ ok: boolean; id?: string; error?: string }> {
    const d = await this.docker();
    const args = ['run', '-d', '--name', opts.name];
    if (opts.restart) args.push('--restart', 'unless-stopped');
    if (opts.network) args.push('--network', opts.network);
    for (const v of opts.volumes ?? []) args.push('-v', `${v.host}:${v.container}`);
    for (const [k, val] of Object.entries(opts.env ?? {})) args.push('-e', `${k}=${val}`);
    if (opts.extraArgs) args.push(...opts.extraArgs);
    args.push(opts.image);
    if (opts.command) args.push(...opts.command); // container command goes AFTER the image
    const r = await this.shell.run(d, args, { timeoutMs: 120_000 });
    if (!r.ok) return { ok: false, error: (r.stderr || r.stdout).trim() };
    return { ok: true, id: r.stdout.trim() };
  }

  /** Container status: 'running' | 'exited' | 'created' | ... | null (no such container). */
  async status(name: string): Promise<string | null> {
    const d = await this.docker();
    const r = await this.shell.run(d, ['inspect', '-f', '{{.State.Status}}', name], { timeoutMs: 15_000 });
    return r.ok ? r.stdout.trim() : null;
  }

  async start(name: string) {
    return this.simple(['start', name]);
  }
  async stop(name: string) {
    return this.simple(['stop', name]);
  }
  async restart(name: string) {
    return this.simple(['restart', name]);
  }
  async rm(name: string) {
    return this.simple(['rm', '-f', name]);
  }

  async logs(name: string, tail = 200): Promise<string> {
    const d = await this.docker();
    const r = await this.shell.run(d, ['logs', '--tail', String(tail), name], { timeoutMs: 20_000 });
    return (r.stdout + (r.stderr ? '\n' + r.stderr : '')).trim();
  }

  /** docker exec (used by the Phase-3 in-container install pipeline). */
  async exec(name: string, argv: string[], opts: { timeoutMs?: number } = {}) {
    const d = await this.docker();
    return this.shell.run(d, ['exec', name, ...argv], { timeoutMs: opts.timeoutMs ?? 10 * 60_000 });
  }

  /** docker exec -d (detached) — for launching long-running processes like the gateway. */
  async execDetached(name: string, argv: string[]) {
    const d = await this.docker();
    return this.shell.run(d, ['exec', '-d', name, ...argv], { timeoutMs: 30_000 });
  }

  private async simple(args: string[]): Promise<{ ok: boolean; error?: string }> {
    const d = await this.docker();
    const r = await this.shell.run(d, args, { timeoutMs: 60_000 });
    return r.ok ? { ok: true } : { ok: false, error: (r.stderr || r.stdout).trim() };
  }
}
