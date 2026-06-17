import { BadRequestException, Injectable } from '@nestjs/common';
import * as fs from 'fs/promises';
import { AtomicFileService } from './atomic-file.service';
import { BackupService } from './backup.service';
import { FileLockService } from './file-lock.service';

const KEY_LINE = /^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=)(.*)$/;

/**
 * Key-preserving read-modify-write of a Hermes `.env` file.
 *
 * Contract (matches how Hermes reads it):
 *  - UTF-8, NO BOM on write (BOM stripped on read).
 *  - `KEY=VALUE` per line, value written VERBATIM (no auto-quoting) so Windows
 *    paths with backslashes/spaces — e.g. an Apple `.p8` path — survive intact.
 *  - Unrelated keys, comments and blank lines are preserved in place.
 *  - Multiline values are rejected (a `.env` value cannot span lines).
 */
@Injectable()
export class EnvFileService {
  constructor(
    private readonly lock: FileLockService,
    private readonly backup: BackupService,
    private readonly atomic: AtomicFileService,
  ) {}

  private stripBom(s: string): string {
    return s.charCodeAt(0) === 0xfeff ? s.slice(1) : s;
  }

  private async readRaw(envPath: string): Promise<string> {
    try {
      return this.stripBom(await fs.readFile(envPath, 'utf8'));
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') return '';
      throw err;
    }
  }

  /** Parsed key -> value map (last assignment wins). */
  async read(envPath: string): Promise<Map<string, string>> {
    const out = new Map<string, string>();
    for (const line of (await this.readRaw(envPath)).split(/\r?\n/)) {
      const m = KEY_LINE.exec(line);
      if (m) out.set(m[2], m[4]);
    }
    return out;
  }

  async has(envPath: string, key: string): Promise<boolean> {
    return (await this.read(envPath)).has(key);
  }

  /**
   * Upsert the given keys. Existing keys are replaced in place; new keys are
   * appended. All other content is preserved byte-for-byte.
   */
  async setMany(envPath: string, updates: Record<string, string>): Promise<void> {
    for (const [k, v] of Object.entries(updates)) {
      if (/[\r\n]/.test(v)) {
        throw new BadRequestException(`Value for ${k} must not contain newlines.`);
      }
    }
    await this.lock.withLock(envPath, async () => {
      await this.backup.backup(envPath);
      const raw = await this.readRaw(envPath);
      const eol = raw.includes('\r\n') ? '\r\n' : '\n';
      const lines = raw.length ? raw.split(/\r?\n/) : [];
      const remaining = { ...updates };

      const next = lines.map((line) => {
        const m = KEY_LINE.exec(line);
        if (m && Object.prototype.hasOwnProperty.call(remaining, m[2])) {
          const key = m[2];
          const value = remaining[key];
          delete remaining[key];
          return `${m[1]}${key}=${value}`;
        }
        return line;
      });

      // Drop a single trailing empty element so we don't accumulate blank lines.
      if (next.length && next[next.length - 1] === '') next.pop();
      for (const [key, value] of Object.entries(remaining)) {
        next.push(`${key}=${value}`);
      }

      await this.atomic.write(envPath, next.join(eol) + eol, /* withBom */ false);
    });
  }
}
