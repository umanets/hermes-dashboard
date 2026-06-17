import { ConflictException, Injectable } from '@nestjs/common';
import * as path from 'path';
import * as lockfile from 'proper-lockfile';

/**
 * Serializes writes to a given file. Two layers:
 *   1. an in-process promise queue keyed by resource path (our own concurrent
 *      requests never overlap, and never double-acquire the cross-process lock);
 *   2. a `proper-lockfile` advisory lock (`<file>.lock`) for cross-process
 *      safety against another dashboard instance.
 *
 * Note: Hermes does NOT honor this advisory lock — that race is handled instead
 * by atomic temp+rename writes (see AtomicFileService). This lock only protects
 * against concurrent DASHBOARD writers.
 *
 * Locks are taken per RESOURCE (config.yaml vs .env have distinct locks), so a
 * config mutation and an env mutation never deadlock each other.
 */
@Injectable()
export class FileLockService {
  private readonly queues = new Map<string, Promise<unknown>>();

  async withLock<T>(resourcePath: string, fn: () => Promise<T>): Promise<T> {
    const key = path.resolve(resourcePath);
    const prev = this.queues.get(key) ?? Promise.resolve();

    const run = prev.catch(() => undefined).then(() => this.locked(key, fn));
    this.queues.set(key, run);
    void run.finally(() => {
      if (this.queues.get(key) === run) this.queues.delete(key);
    });
    return run;
  }

  private async locked<T>(key: string, fn: () => Promise<T>): Promise<T> {
    let release: (() => Promise<void>) | undefined;
    try {
      release = await lockfile.lock(key, {
        realpath: false,
        stale: 20_000,
        retries: { retries: 10, minTimeout: 50, maxTimeout: 400 },
      });
    } catch (err) {
      throw new ConflictException(
        `Could not acquire write lock on ${key} (another writer is active): ${(err as Error).message}`,
      );
    }
    try {
      return await fn();
    } finally {
      await release?.().catch(() => undefined);
    }
  }
}
