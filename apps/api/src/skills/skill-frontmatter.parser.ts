import { parse } from 'yaml';
import { z } from 'zod';
import { SkillFrontmatter } from './skill.types';

const FrontmatterSchema = z
  .object({
    name: z.string().min(1).max(64),
    description: z.string().min(1),
    version: z.string().optional(),
    license: z.string().optional(),
    author: z.string().optional(),
    platforms: z.array(z.enum(['macos', 'linux', 'windows'])).optional(),
    prerequisites: z
      .object({
        env_vars: z.array(z.string()).optional(),
        commands: z.array(z.string()).optional(),
      })
      .partial()
      .passthrough()
      .optional(),
    metadata: z.record(z.unknown()).optional(),
  })
  .passthrough();

const FRONTMATTER = /^﻿?---\r?\n([\s\S]*?)\r?\n---/;

export interface FrontmatterResult {
  data: SkillFrontmatter | null;
  valid: boolean;
  errors: string[];
}

/**
 * Extract + validate the YAML frontmatter block of a SKILL.md. On schema
 * failure we still return the best-effort parsed object so the UI can show the
 * skill (flagged invalid) rather than dropping it.
 */
export function parseFrontmatter(content: string): FrontmatterResult {
  const m = FRONTMATTER.exec(content);
  if (!m) return { data: null, valid: false, errors: ['No YAML frontmatter block found.'] };

  let raw: unknown;
  try {
    raw = parse(m[1]);
  } catch (err) {
    return { data: null, valid: false, errors: [`Invalid YAML frontmatter: ${(err as Error).message}`] };
  }

  const result = FrontmatterSchema.safeParse(raw);
  if (!result.success) {
    return {
      data: (raw ?? null) as SkillFrontmatter | null,
      valid: false,
      errors: result.error.issues.map((i) => `${i.path.join('.') || '(root)'}: ${i.message}`),
    };
  }
  return { data: result.data as SkillFrontmatter, valid: true, errors: [] };
}
