import { Injectable, NotFoundException, UnprocessableEntityException } from '@nestjs/common';
import { existsSync } from 'fs';
import { copyFile, mkdir, readFile, readdir } from 'fs/promises';
import * as path from 'path';
import { PathService } from '../core/path.service';
import { InstallationService } from '../installation/installation.service';
import { parseFrontmatter } from '../skills/skill-frontmatter.parser';

export type Collection = 'my' | 'community';
const COLLECTIONS: Collection[] = ['my', 'community'];

export interface LibrarySkill {
  collection: Collection;
  /** top-level folder under the collection (e.g. "media") */
  category: string;
  /** skill folder name */
  name: string;
  /** stable id for install calls: "<category>/<name>" */
  key: string;
  description: string;
  valid: boolean;
  errors: string[];
}

/**
 * Operator-curated skill library that lives on the dashboard host (NOT per-agent):
 *   <SKILL_LIBRARY_DIR or <repo>/skill-library>/<collection>/<category>/<skill>/SKILL.md
 * Two collections: `my` (your own skills) and `community` (shared, filled later).
 * Skills are COPIED into an agent's volume on demand — the library is the source of truth.
 */
@Injectable()
export class SkillLibraryService {
  constructor(
    private readonly installation: InstallationService,
    private readonly paths: PathService,
  ) {}

  private root(): string {
    if (process.env.SKILL_LIBRARY_DIR) return this.paths.canonical(process.env.SKILL_LIBRARY_DIR);
    // Find the repo root (dir with apps/ or an existing skill-library/), independent of cwd.
    for (const start of [process.cwd(), __dirname]) {
      let dir = start;
      for (let i = 0; i < 8; i++) {
        if (existsSync(path.join(dir, 'skill-library')) || existsSync(path.join(dir, 'apps'))) {
          return this.paths.canonical(path.join(dir, 'skill-library'));
        }
        const up = path.dirname(dir);
        if (up === dir) break;
        dir = up;
      }
    }
    return this.paths.canonical(path.join(process.cwd(), 'skill-library'));
  }

  private assertCollection(c: string): Collection {
    if (!COLLECTIONS.includes(c as Collection)) {
      throw new UnprocessableEntityException(`Unknown skill collection '${c}'. Use one of: ${COLLECTIONS.join(', ')}.`);
    }
    return c as Collection;
  }

  async list(collection: string): Promise<LibrarySkill[]> {
    const col = this.assertCollection(collection);
    const base = path.join(this.root(), col);
    const out: LibrarySkill[] = [];
    for (const file of await this.findSkillFiles(base)) {
      const rel = this.paths.toForwardSlashes(path.relative(base, path.dirname(file)));
      const category = rel.split('/')[0] ?? '';
      const folderName = path.basename(path.dirname(file));
      let content = '';
      try {
        content = await readFile(file, 'utf8');
      } catch {
        continue;
      }
      const fm = parseFrontmatter(content);
      const name = fm.data?.name ?? folderName;
      out.push({
        collection: col,
        category,
        name: folderName,
        key: `${category}/${folderName}`,
        description: fm.data?.description ?? '',
        valid: fm.valid,
        errors: fm.errors,
      });
    }
    out.sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
    return out;
  }

  async preview(collection: string, category: string, name: string): Promise<{ collection: string; category: string; name: string; content: string }> {
    const dir = this.skillDir(collection, category, name);
    const file = path.join(dir, 'SKILL.md');
    if (!existsSync(file)) throw new NotFoundException(`No skill '${category}/${name}' in '${collection}'.`);
    return { collection, category, name, content: await readFile(file, 'utf8') };
  }

  /** Copy a library skill into the currently-connected install's skills dir. */
  async installToActive(collection: string, category: string, name: string): Promise<{ placed: string }> {
    const install = this.installation.getActiveOrThrow();
    return { placed: await this.copyOne(install.skillsDir, collection, category, name) };
  }

  /** Copy selected library skills into an explicit skills dir (used at agent creation). */
  async seedInto(targetSkillsDir: string, items: { collection?: string; category: string; name: string }[]): Promise<string[]> {
    const placed: string[] = [];
    for (const it of items) {
      placed.push(await this.copyOne(targetSkillsDir, it.collection ?? 'my', it.category, it.name));
    }
    return placed;
  }

  // ---- internals -------------------------------------------------------

  private skillDir(collection: string, category: string, name: string): string {
    const col = this.assertCollection(collection);
    // guard against path traversal in category/name
    const safe = (s: string) => {
      if (!s || s.includes('..') || s.includes('/') || s.includes('\\')) {
        throw new UnprocessableEntityException(`Invalid skill path segment '${s}'.`);
      }
      return s;
    };
    return path.join(this.root(), col, safe(category), safe(name));
  }

  private async copyOne(targetSkillsDir: string, collection: string, category: string, name: string): Promise<string> {
    const src = this.skillDir(collection, category, name);
    if (!existsSync(src)) throw new NotFoundException(`No skill '${category}/${name}' in '${collection}'.`);
    const dest = path.join(targetSkillsDir, category, name);
    await this.copyDir(src, dest);
    return this.paths.toForwardSlashes(dest);
  }

  private async copyDir(src: string, dest: string): Promise<void> {
    await mkdir(dest, { recursive: true });
    for (const entry of await readdir(src, { withFileTypes: true })) {
      if (entry.name === '__pycache__' || entry.name === '.git') continue;
      const s = path.join(src, entry.name);
      const d = path.join(dest, entry.name);
      if (entry.isDirectory()) await this.copyDir(s, d);
      else if (entry.isFile()) await copyFile(s, d);
    }
  }

  private async findSkillFiles(root: string, depth = 0): Promise<string[]> {
    if (depth > 4) return [];
    let entries: import('fs').Dirent[];
    try {
      entries = await readdir(root, { withFileTypes: true });
    } catch {
      return [];
    }
    const found: string[] = [];
    for (const entry of entries) {
      if (entry.isFile() && entry.name === 'SKILL.md') found.push(path.join(root, entry.name));
      else if (entry.isDirectory() && !entry.name.startsWith('.')) {
        found.push(...(await this.findSkillFiles(path.join(root, entry.name), depth + 1)));
      }
    }
    return found;
  }
}
