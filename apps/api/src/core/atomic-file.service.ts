import { Injectable, Logger } from '@nestjs/common';
import * as fs from 'fs/promises';
import * as path from 'path';

const RENAME_RETRIES = 5;
const RENAME_BACKOFF_MS = 80;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Atomic file writes: temp file in the SAME directory -> fsync -> rename.
 *
 * Rationale: Hermes may read config.yaml/.env at any moment (it loads config at
 * session start). A rename is atomic on a single volume, so a concurrent reader
 * always sees either the complete old file or the complete new one — never a
 * half-written file. We never edit in place.
 *
 * On Windows, renaming over a file that another process has open can throw
 * EPERM/EBUSY; we retry with backoff, then fail loudly. We NEVER fall back to a
 * non-atomic in-place write.
 */
@Injectable()
export class AtomicFileService {
  private readonly logger = new Logger(AtomicFileService.name);

  /**
   * @param filePath  target path
   * @param content   bytes/string to write
   * @param withBom   when false (default) writes UTF-8 with no BOM
   */
  async write(filePath: string, content: string, withBom = false): Promise<void> {
    const dir = path.dirname(filePath);
    const tmp = path.join(
      dir,
      `.${path.basename(filePath)}.${process.pid}.${Math.floor(performance.now())}.tmp`,
    );

    const buffer = withBom
      ? Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(content, 'utf8')])
      : Buffer.from(content, 'utf8');

    let handle: fs.FileHandle | undefined;
    try {
      handle = await fs.open(tmp, 'w');
      await handle.write(buffer);
      await handle.sync();
    } finally {
      await handle?.close();
    }

    let lastErr: unknown;
    for (let attempt = 0; attempt < RENAME_RETRIES; attempt++) {
      try {
        await fs.rename(tmp, filePath);
        return;
      } catch (err) {
        lastErr = err;
        const code = (err as NodeJS.ErrnoException).code;
        if (code === 'EPERM' || code === 'EBUSY' || code === 'EACCES') {
          this.logger.warn(
            `rename ${tmp} -> ${filePath} failed (${code}), retry ${attempt + 1}/${RENAME_RETRIES}`,
          );
          await sleep(RENAME_BACKOFF_MS * (attempt + 1));
          continue;
        }
        break;
      }
    }
    // Clean up the temp file; do NOT attempt a non-atomic fallback.
    await fs.rm(tmp, { force: true }).catch(() => undefined);
    throw lastErr;
  }
}
