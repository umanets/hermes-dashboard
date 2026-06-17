import { Injectable } from '@nestjs/common';
import * as path from 'path';

/**
 * Path normalization + placeholder resolution.
 *
 * Hermes config.yaml stores paths with forward slashes even on Windows
 * (Python accepts them), so everything we WRITE is normalized to forward
 * slashes. Reads canonicalize via path.resolve so equality checks are stable.
 */
@Injectable()
export class PathService {
  /** Convert backslashes to forward slashes (the form Hermes config uses). */
  toForwardSlashes(p: string): string {
    return p.replace(/\\/g, '/');
  }

  /** True for absolute paths on either platform (handles `C:\`, `C:/`, `/...`). */
  isAbsolute(p: string): boolean {
    return path.win32.isAbsolute(p) || path.posix.isAbsolute(p);
  }

  /**
   * Canonical absolute path with forward slashes. Used both for storage and as
   * the identity of an installation/module.
   */
  canonical(p: string): string {
    return this.toForwardSlashes(path.resolve(p));
  }

  join(...parts: string[]): string {
    return this.toForwardSlashes(path.join(...parts));
  }

  /**
   * Substitute `${KEY}` placeholders from a map. Unknown placeholders are left
   * intact so {@link findUnresolved} can flag them.
   */
  substitute(value: string, vars: Record<string, string>): string {
    return value.replace(/\$\{([^}]+)\}/g, (whole, key: string) =>
      Object.prototype.hasOwnProperty.call(vars, key) ? vars[key] : whole,
    );
  }

  /** Return every unresolved `${...}` placeholder remaining in a string. */
  findUnresolved(value: string): string[] {
    return [...value.matchAll(/\$\{([^}]+)\}/g)].map((m) => m[0]);
  }
}
