import { Injectable, NotFoundException } from '@nestjs/common';
import * as fs from 'fs/promises';
import * as path from 'path';
import { HermesConfigService } from '../hermes-config/hermes-config.service';
import { PathService } from '../core/path.service';
import { InstallationService } from '../installation/installation.service';
import { parseFrontmatter } from './skill-frontmatter.parser';
import { Skill } from './skill.types';

const SKIP_DIRS = new Set(['.hub', '.curator_backups', '.curator_state']);
const MAX_DEPTH = 4;

@Injectable()
export class SkillsService {
  constructor(
    private readonly installation: InstallationService,
    private readonly config: HermesConfigService,
    private readonly paths: PathService,
  ) {}

  async list(): Promise<Skill[]> {
    const install = this.installation.getActiveOrThrow();
    const disabled = new Set((await this.config.read(install.rootPath)).skills.disabled);

    const skillMdFiles = await this.findSkillFiles(install.skillsDir);
    const skills: Skill[] = [];
    for (const file of skillMdFiles) {
      const rel = path.relative(install.skillsDir, path.dirname(file));
      const category = this.paths.toForwardSlashes(rel).split('/')[0] ?? '';
      let content = '';
      try {
        content = await fs.readFile(file, 'utf8');
      } catch {
        continue;
      }
      const fm = parseFrontmatter(content);
      const name = fm.data?.name ?? path.basename(path.dirname(file));
      skills.push({
        name,
        category,
        path: this.paths.toForwardSlashes(path.dirname(file)),
        enabled: !disabled.has(name),
        frontmatter: fm.data,
        frontmatterValid: fm.valid,
        frontmatterErrors: fm.errors,
      });
    }
    skills.sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name));
    return skills;
  }

  /**
   * Bulk enable/disable in a SINGLE config write. Scope is all skills, or just
   * one category. Only names of skills actually present (in scope) are touched,
   * so phantom entries in skills.disabled are left alone.
   */
  async setBulkEnabled(
    enabled: boolean,
    category?: string,
  ): Promise<{ changed: number; enabled: boolean; category: string | null }> {
    const install = this.installation.getActiveOrThrow();
    const all = await this.list();
    const scoped = category ? all.filter((s) => s.category === category) : all;
    const names = new Set(scoped.map((s) => s.name));
    if (names.size) {
      await this.config.mutate(install.rootPath, (doc) => {
        const current = (doc.toJS()?.skills?.disabled ?? []) as string[];
        const set = new Set(Array.isArray(current) ? current : []);
        for (const n of names) {
          if (enabled) set.delete(n);
          else set.add(n);
        }
        doc.setIn(['skills', 'disabled'], [...set]);
      });
    }
    return { changed: names.size, enabled, category: category ?? null };
  }

  /** Full SKILL.md content + metadata for the preview drawer. */
  async getPreview(name: string): Promise<{
    name: string;
    category: string;
    path: string;
    enabled: boolean;
    frontmatter: import('./skill.types').SkillFrontmatter | null;
    frontmatterValid: boolean;
    content: string;
  }> {
    const skill = (await this.list()).find((s) => s.name === name);
    if (!skill) throw new NotFoundException(`No skill named '${name}' in this installation.`);
    const content = await fs.readFile(path.join(skill.path, 'SKILL.md'), 'utf8');
    return {
      name: skill.name,
      category: skill.category,
      path: skill.path,
      enabled: skill.enabled,
      frontmatter: skill.frontmatter,
      frontmatterValid: skill.frontmatterValid,
      content,
    };
  }

  /** Add/remove the skill name from config.yaml skills.disabled. */
  async setEnabled(name: string, enabled: boolean): Promise<{ name: string; enabled: boolean }> {
    const install = this.installation.getActiveOrThrow();
    // Confirm the skill exists so we don't write a phantom disable entry.
    const exists = (await this.list()).some((s) => s.name === name);
    if (!exists) throw new NotFoundException(`No skill named '${name}' in this installation.`);

    await this.config.mutate(install.rootPath, (doc) => {
      const current = (doc.toJS()?.skills?.disabled ?? []) as string[];
      const set = new Set(Array.isArray(current) ? current : []);
      if (enabled) set.delete(name);
      else set.add(name);
      doc.setIn(['skills', 'disabled'], [...set]);
    });
    return { name, enabled };
  }

  private async findSkillFiles(root: string, depth = 0): Promise<string[]> {
    if (depth > MAX_DEPTH) return [];
    let entries: import('fs').Dirent[];
    try {
      entries = await fs.readdir(root, { withFileTypes: true });
    } catch {
      return [];
    }
    const found: string[] = [];
    for (const entry of entries) {
      if (entry.isFile() && entry.name === 'SKILL.md') {
        found.push(path.join(root, entry.name));
      } else if (entry.isDirectory() && !entry.name.startsWith('.') && !SKIP_DIRS.has(entry.name)) {
        found.push(...(await this.findSkillFiles(path.join(root, entry.name), depth + 1)));
      }
    }
    return found;
  }
}
