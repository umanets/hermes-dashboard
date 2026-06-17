import { Injectable, Logger } from '@nestjs/common';
import * as fs from 'fs/promises';
import * as path from 'path';

const DEFAULT_KEEP = 20;

/**
 * Timestamped backups before every mutating write, matching Hermes' own
 * `config.yaml.bak.*` convention. We use a sortable compact UTC stamp so
 * pruning by name == pruning by age.
 */
@Injectable()
export class BackupService {
  private readonly logger = new Logger(BackupService.name);

  /**
   * Copy `filePath` to `filePath.bak.<UTCstamp>`. No-op if the file does not
   * exist yet (first write). Returns the backup path (or null if skipped).
   */
  async backup(filePath: string, keep = DEFAULT_KEEP): Promise<string | null> {
    try {
      await fs.access(filePath);
    } catch {
      return null; // nothing to back up yet
    }
    // Keep milliseconds: two writes in the same second must not collide (the
    // later copy would otherwise overwrite the earlier rollback point). Still
    // lexically sortable == chronological. e.g. 20260616T101500.123Z
    const stamp = new Date().toISOString().replace(/[-:]/g, '');
    const backupPath = `${filePath}.bak.${stamp}`;
    await fs.copyFile(filePath, backupPath);
    await this.prune(filePath, keep);
    return backupPath;
  }

  private async prune(filePath: string, keep: number): Promise<void> {
    const dir = path.dirname(filePath);
    const prefix = `${path.basename(filePath)}.bak.`;
    let entries: string[];
    try {
      entries = (await fs.readdir(dir)).filter((e) => e.startsWith(prefix));
    } catch {
      return;
    }
    if (entries.length <= keep) return;
    entries.sort(); // lexical == chronological for our stamp
    const stale = entries.slice(0, entries.length - keep);
    await Promise.all(
      stale.map((e) =>
        fs.rm(path.join(dir, e), { force: true }).catch((err) => {
          this.logger.warn(`failed to prune backup ${e}: ${err}`);
        }),
      ),
    );
  }
}
