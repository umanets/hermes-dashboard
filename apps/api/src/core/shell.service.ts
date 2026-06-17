import { Injectable, Logger } from '@nestjs/common';
import { spawn } from 'child_process';

export interface ShellResult {
  code: number | null;
  stdout: string;
  stderr: string;
  ok: boolean;
}

export interface ShellOptions {
  cwd?: string;
  env?: Record<string, string | undefined>;
  timeoutMs?: number;
}

/**
 * Runs commands on the machine the dashboard is executing on (the client's box
 * in the provisioning flow). Used by the install pipeline for venv creation,
 * pip install, and `hermes cron create`. Injectable so tests stub it.
 */
@Injectable()
export class ShellService {
  private readonly logger = new Logger(ShellService.name);

  async run(command: string, args: string[], opts: ShellOptions = {}): Promise<ShellResult> {
    const timeoutMs = opts.timeoutMs ?? 10 * 60_000;
    return new Promise<ShellResult>((resolve) => {
      const child = spawn(command, args, {
        cwd: opts.cwd,
        env: { PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', ...process.env, ...opts.env },
        shell: false,
        windowsHide: true,
      });
      let stdout = '';
      let stderr = '';
      const timer = setTimeout(() => {
        child.kill();
        stderr += `\n[timed out after ${timeoutMs}ms]`;
      }, timeoutMs);

      child.stdout?.on('data', (d) => (stdout += d.toString()));
      child.stderr?.on('data', (d) => (stderr += d.toString()));
      child.on('error', (err) => {
        clearTimeout(timer);
        resolve({ code: null, stdout, stderr: `${stderr}\n${err.message}`, ok: false });
      });
      child.on('close', (code) => {
        clearTimeout(timer);
        resolve({ code, stdout, stderr, ok: code === 0 });
      });
    });
  }

  /** Like run(), but invokes onLine for each stdout line as it arrives (for progress). */
  async runStreaming(
    command: string,
    args: string[],
    onLine: (line: string) => void,
    opts: ShellOptions = {},
  ): Promise<ShellResult> {
    const timeoutMs = opts.timeoutMs ?? 30 * 60_000;
    return new Promise<ShellResult>((resolve) => {
      const child = spawn(command, args, {
        cwd: opts.cwd,
        env: { PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1', ...process.env, ...opts.env },
        shell: false,
        windowsHide: true,
      });
      let stdout = '';
      let stderr = '';
      const bufs = { out: '', err: '' };
      const timer = setTimeout(() => child.kill(), timeoutMs);
      const pump = (key: 'out' | 'err', chunk: string) => {
        bufs[key] += chunk;
        let nl: number;
        while ((nl = bufs[key].indexOf('\n')) >= 0) {
          const line = bufs[key].slice(0, nl).replace(/\r$/, '');
          bufs[key] = bufs[key].slice(nl + 1);
          if (line) onLine(line);
        }
      };
      child.stdout?.on('data', (d) => {
        const s = d.toString();
        stdout += s;
        pump('out', s);
      });
      // Many tools (docker/buildkit, pip) write progress to stderr — stream it too.
      child.stderr?.on('data', (d) => {
        const s = d.toString();
        stderr += s;
        pump('err', s);
      });
      child.on('error', (err) => {
        clearTimeout(timer);
        resolve({ code: null, stdout, stderr: `${stderr}\n${err.message}`, ok: false });
      });
      child.on('close', (code) => {
        clearTimeout(timer);
        for (const k of ['out', 'err'] as const) if (bufs[k].trim()) onLine(bufs[k].trim());
        resolve({ code, stdout, stderr, ok: code === 0 });
      });
    });
  }

  /** Resolve an executable on PATH (cross-platform), or null. */
  async which(command: string): Promise<string | null> {
    const probe = process.platform === 'win32' ? 'where' : 'which';
    const res = await this.run(probe, [command], { timeoutMs: 10_000 });
    if (!res.ok) return null;
    return res.stdout.split(/\r?\n/).map((s) => s.trim()).find(Boolean) ?? null;
  }
}
