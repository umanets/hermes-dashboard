import { Injectable } from '@nestjs/common';

const MASK = '••••••••';

/**
 * Masks secret values so they never reach logs or API responses. Secrets only
 * ever live in the install's `.env`; the dashboard reports presence, not value.
 */
@Injectable()
export class RedactionService {
  /** Mask a single value, preserving null/empty distinction. */
  mask(value: string | null | undefined): string {
    if (value === null || value === undefined || value === '') return '';
    return MASK;
  }

  /**
   * Recursively replace values of keys whose names match a secret list. Used by
   * the logging interceptor as a defense-in-depth scrub of request bodies.
   */
  redactObject<T>(obj: T, secretKeys: Set<string>): T {
    if (Array.isArray(obj)) {
      return obj.map((v) => this.redactObject(v, secretKeys)) as unknown as T;
    }
    if (obj && typeof obj === 'object') {
      const out: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
        if (secretKeys.has(k) && typeof v === 'string') {
          out[k] = this.mask(v);
        } else {
          out[k] = this.redactObject(v, secretKeys);
        }
      }
      return out as T;
    }
    return obj;
  }
}
