import { Injectable } from '@nestjs/common';
import * as fs from 'fs/promises';
import { Document } from 'yaml';
import { AtomicFileService } from '../core/atomic-file.service';
import { BackupService } from '../core/backup.service';
import { FileLockService } from '../core/file-lock.service';
import { PathService } from '../core/path.service';
import { YamlDocService } from '../core/yaml-doc.service';

export interface ManagedConfig {
  skills: {
    disabled: string[];
    platform_disabled?: Record<string, string[]>;
    [k: string]: unknown;
  };
  mcp_servers: Record<string, RawMcpServer>;
}

export interface RawMcpServer {
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  enabled?: boolean;
  tools?: { include?: string[]; exclude?: string[] };
  [k: string]: unknown;
}

/**
 * THE single chokepoint for config.yaml. Every read goes through here; every
 * write is: lock(config.yaml) -> backup -> parse full doc -> mutate managed
 * nodes -> atomic round-trip write. No other service writes config.yaml.
 */
@Injectable()
export class HermesConfigService {
  constructor(
    private readonly paths: PathService,
    private readonly yaml: YamlDocService,
    private readonly lock: FileLockService,
    private readonly backup: BackupService,
    private readonly atomic: AtomicFileService,
  ) {}

  private configPath(root: string): string {
    return this.paths.join(root, 'config.yaml');
  }

  /** Read the managed slice as plain JS (defaults applied). */
  async read(root: string): Promise<ManagedConfig> {
    const doc = await this.readDoc(root);
    const js = this.yaml.toJS<Record<string, any>>(doc);
    const skills = js.skills ?? {};
    return {
      skills: {
        disabled: Array.isArray(skills.disabled) ? skills.disabled : [],
        platform_disabled: skills.platform_disabled,
        ...skills,
      },
      mcp_servers: (js.mcp_servers ?? {}) as Record<string, RawMcpServer>,
    };
  }

  async readDoc(root: string): Promise<Document> {
    const raw = await fs.readFile(this.configPath(root), 'utf8');
    return this.yaml.parse(raw);
  }

  /**
   * Apply `mutator` to the full document inside the write lock, with a backup,
   * and write atomically. Returns the backup path (null on first write).
   */
  async mutate(root: string, mutator: (doc: Document) => void): Promise<{ backupPath: string | null }> {
    const configPath = this.configPath(root);
    return this.lock.withLock(configPath, async () => {
      const backupPath = await this.backup.backup(configPath);
      const raw = await fs.readFile(configPath, 'utf8');
      const doc = this.yaml.parse(raw);
      mutator(doc);
      await this.atomic.write(configPath, this.yaml.stringify(doc), /* withBom */ false);
      return { backupPath };
    });
  }
}
