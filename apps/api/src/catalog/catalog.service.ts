import { Injectable, NotFoundException, UnprocessableEntityException } from '@nestjs/common';
import { existsSync } from 'fs';
import * as fs from 'fs/promises';
import * as path from 'path';
import { PathService } from '../core/path.service';
import { parseManifest } from './manifest.parser';
import { CatalogModule } from './manifest.types';

const MANIFEST_FILE = 'hermes-manifest.yaml';

/**
 * Reads the operator's private module catalog from the repo. Each module is a
 * directory containing a `hermes-manifest.yaml`.
 *
 * Modules dir resolution (zero-config for dev before/after the modules/ move):
 *   1. $CATALOG_MODULES_DIR if set;
 *   2. <repoRoot>/modules if it contains any module;
 *   3. <repoRoot> itself (covers apple-tidal still sitting at the repo root).
 */
@Injectable()
export class CatalogService {
  constructor(private readonly paths: PathService) {}

  /**
   * Roots scanned for modules. With $CATALOG_MODULES_DIR set we honor only that.
   * Otherwise we scan BOTH <repo>/modules and the repo root, so a module that
   * hasn't been relocated into modules/ yet (e.g. apple-tidal) still shows up.
   */
  async modulesRoots(): Promise<string[]> {
    const envDir = process.env.CATALOG_MODULES_DIR;
    if (envDir) return [this.paths.canonical(envDir)];
    const repoRoot = this.repoRoot();
    const modules = path.join(repoRoot, 'modules');
    const roots = [modules, repoRoot].map((r) => this.paths.canonical(r));
    return [...new Set(roots)];
  }

  /**
   * Locate the repo root independent of the launch directory: works whether you
   * run from the repo root (`npm start` → cwd = root) or from apps/api
   * (`start:dev` → cwd = apps/api), and in prod where the code lives in
   * apps/api/dist. Walk up from cwd, then from this file, until we hit a dir
   * that looks like the repo (has `apps/` or `modules/`).
   */
  private repoRoot(): string {
    const isRoot = (d: string) => existsSync(path.join(d, 'apps')) || existsSync(path.join(d, 'modules'));
    for (const start of [process.cwd(), __dirname]) {
      let dir = start;
      for (let i = 0; i < 8; i++) {
        if (isRoot(dir)) return dir;
        const up = path.dirname(dir);
        if (up === dir) break;
        dir = up;
      }
    }
    return process.cwd();
  }

  /** Back-compat: the primary modules dir (modules/ if present, else repo root). */
  async modulesDir(): Promise<string> {
    const [first] = await this.modulesRoots();
    return first;
  }

  async list(): Promise<CatalogModule[]> {
    const byId = new Map<string, CatalogModule>();
    for (const dir of await this.modulesRoots()) {
      for (const mod of await this.scanRoot(dir)) {
        if (!byId.has(mod.id)) byId.set(mod.id, mod); // first root (modules/) wins
      }
    }
    return [...byId.values()].sort((a, b) => a.id.localeCompare(b.id));
  }

  private async scanRoot(dir: string): Promise<CatalogModule[]> {
    let entries: import('fs').Dirent[];
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return [];
    }
    const modules: CatalogModule[] = [];
    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
      if (entry.name === 'node_modules' || entry.name === 'apps') continue;
      const manifestPath = path.join(dir, entry.name, MANIFEST_FILE);
      let content: string;
      try {
        content = await fs.readFile(manifestPath, 'utf8');
      } catch {
        continue; // not a module
      }
      const parsed = parseManifest(content);
      modules.push({
        id: entry.name,
        manifestPath: this.paths.toForwardSlashes(manifestPath),
        modulePath: this.paths.canonical(path.join(dir, entry.name)),
        manifest: parsed.manifest,
        manifestValid: parsed.valid,
        validationErrors: parsed.errors,
      });
    }
    return modules;
  }

  async get(id: string): Promise<CatalogModule> {
    const found = (await this.list()).find((m) => m.id === id);
    if (!found) throw new NotFoundException(`No catalog module '${id}'.`);
    return found;
  }

  /** Like get(), but rejects an invalid manifest (used before install). */
  async getValid(id: string): Promise<CatalogModule & { manifest: NonNullable<CatalogModule['manifest']> }> {
    const mod = await this.get(id);
    if (!mod.manifestValid || !mod.manifest) {
      throw new UnprocessableEntityException(`Module '${id}' has an invalid manifest: ${mod.validationErrors.join('; ')}`);
    }
    return mod as CatalogModule & { manifest: NonNullable<CatalogModule['manifest']> };
  }
}
