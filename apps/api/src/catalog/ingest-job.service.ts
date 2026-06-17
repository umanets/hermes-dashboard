import { Injectable, Logger, NotFoundException, UnprocessableEntityException } from '@nestjs/common';
import { existsSync } from 'fs';
import * as path from 'path';
import { HermesConfigService } from '../hermes-config/hermes-config.service';
import { PathService } from '../core/path.service';
import { ShellService } from '../core/shell.service';
import { InstallationService } from '../installation/installation.service';
import { CatalogService } from './catalog.service';

export interface IngestJob {
  id: string;
  module: string;
  dir: string;
  status: 'running' | 'completed' | 'failed';
  progress: { done: number; total: number };
  result?: { files: number; added: number; skipped: number; chunks: number };
  log: string;
  error?: string;
  startedAt: string;
}

/**
 * Runs a module's ingest script (e.g. vector-memory's ingest.py) over a chosen
 * folder, streaming its `INGEST progress=...` lines into a polled job so the UI
 * can show a progress bar. The script inherits the installed MCP server's env
 * (so it writes to the same data dir the server reads).
 */
@Injectable()
export class IngestJobService {
  private readonly logger = new Logger(IngestJobService.name);
  private readonly jobs = new Map<string, IngestJob>();
  private seq = 0;

  constructor(
    private readonly installation: InstallationService,
    private readonly catalog: CatalogService,
    private readonly config: HermesConfigService,
    private readonly shell: ShellService,
    private readonly paths: PathService,
  ) {}

  get(jobId: string): IngestJob {
    const job = this.jobs.get(jobId);
    if (!job) throw new NotFoundException(`No ingest job '${jobId}'.`);
    return job;
  }

  async start(id: string, dir: string, vault?: string): Promise<{ jobId: string }> {
    const install = this.installation.getActiveOrThrow();
    const mod = await this.catalog.getValid(id);
    if (!mod.manifest.ingest) {
      throw new UnprocessableEntityException(`Module '${id}' does not support ingest.`);
    }
    if (!dir || !this.paths.isAbsolute(dir)) {
      throw new UnprocessableEntityException('An absolute folder path is required.');
    }
    if (!existsSync(dir)) throw new UnprocessableEntityException(`Folder not found: ${dir}`);

    const localPath = this.paths.canonical(path.join(install.rootPath, 'modules', id));
    const venvPy = this.venvPython(localPath);
    if (!existsSync(venvPy)) {
      throw new UnprocessableEntityException(`Module '${id}' isn't installed yet (no venv) — install it first.`);
    }
    const script = path.join(localPath, mod.manifest.ingest.script);
    if (!existsSync(script)) throw new UnprocessableEntityException(`Ingest script missing: ${script}`);

    // Forward the installed MCP server's env (e.g. VECTOR_MEMORY_DATA_DIR) so the
    // ingest writes to the SAME store the server reads.
    const servers = (await this.config.read(install.rootPath)).mcp_servers;
    const serverEnv = servers[mod.manifest.name]?.env ?? {};

    this.seq += 1;
    const jobId = `ingest-${this.seq}`;
    const job: IngestJob = {
      id: jobId,
      module: id,
      dir: this.paths.toForwardSlashes(dir),
      status: 'running',
      progress: { done: 0, total: 0 },
      log: '',
      startedAt: new Date().toISOString(),
    };
    this.jobs.set(jobId, job);
    void this.run(job, venvPy, script, dir, vault, serverEnv);
    return { jobId };
  }

  private async run(
    job: IngestJob,
    venvPy: string,
    script: string,
    dir: string,
    vault: string | undefined,
    serverEnv: Record<string, string>,
  ): Promise<void> {
    const args = [script, '--dir', dir];
    if (vault) args.push('--vault', vault);

    const onLine = (line: string) => {
      const total = line.match(/total=(\d+)/);
      if (total) job.progress.total = Number(total[1]);
      const prog = line.match(/progress=(\d+)/);
      if (prog) job.progress.done = Number(prog[1]);
      const done = line.match(/^INGEST done files=(\d+) added=(\d+) skipped=(\d+) chunks=(\d+)/);
      if (done) {
        job.result = {
          files: Number(done[1]),
          added: Number(done[2]),
          skipped: Number(done[3]),
          chunks: Number(done[4]),
        };
      }
      job.log = (job.log + line + '\n').split('\n').slice(-12).join('\n');
    };

    const res = await this.shell.runStreaming(venvPy, args, onLine, {
      cwd: path.dirname(script),
      env: serverEnv,
      timeoutMs: 30 * 60_000,
    });

    if (res.ok) {
      job.status = 'completed';
      if (!job.result) {
        job.result = { files: job.progress.total, added: 0, skipped: 0, chunks: 0 };
      }
    } else {
      job.status = 'failed';
      job.error = (res.stderr || res.stdout || 'ingest failed').trim().slice(-500);
      this.logger.warn(`ingest job ${job.id} failed: ${job.error}`);
    }
  }

  private venvPython(localPath: string): string {
    return process.platform === 'win32'
      ? path.join(localPath, '.venv', 'Scripts', 'python.exe')
      : path.join(localPath, '.venv', 'bin', 'python');
  }
}
