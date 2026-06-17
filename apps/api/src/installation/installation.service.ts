import { Injectable, NotFoundException } from '@nestjs/common';
import * as fs from 'fs/promises';
import { PathService } from '../core/path.service';
import { ConnectedInstallation, ContainerContext } from './installation.types';

/**
 * Holds the single active Hermes installation (v1: one at a time, in memory).
 * Connecting validates that the folder looks like a Hermes install.
 */
@Injectable()
export class InstallationService {
  private active: ConnectedInstallation | null = null;

  constructor(private readonly paths: PathService) {}

  async connect(rawPath: string, container?: ContainerContext): Promise<ConnectedInstallation> {
    if (!rawPath || !this.paths.isAbsolute(rawPath)) {
      throw new NotFoundException('An absolute installation path is required.');
    }
    const rootPath = this.paths.canonical(rawPath);
    const configPath = this.paths.join(rootPath, 'config.yaml');
    const envPath = this.paths.join(rootPath, '.env');
    const skillsDir = this.paths.join(rootPath, 'skills');

    const validationErrors: string[] = [];
    const dirOk = await this.isDir(rootPath);
    if (!dirOk) throw new NotFoundException(`Not a directory: ${rootPath}`);

    const configOk = await this.exists(configPath);
    if (!configOk) validationErrors.push('config.yaml not found — not a Hermes install root.');
    if (!(await this.exists(envPath))) {
      validationErrors.push('.env not found — it will be created on first credential write.');
    }
    if (!(await this.isDir(skillsDir))) {
      validationErrors.push('skills/ directory not found — skill listing will be empty.');
    }

    const installation: ConnectedInstallation = {
      id: rootPath,
      rootPath,
      configPath,
      envPath,
      skillsDir,
      connectedAt: new Date().toISOString(),
      valid: configOk,
      validationErrors,
      container,
    };
    this.active = installation;
    return installation;
  }

  getActive(): ConnectedInstallation | null {
    return this.active;
  }

  getActiveOrThrow(): ConnectedInstallation {
    if (!this.active) {
      throw new NotFoundException('No installation connected. POST /api/installation/connect first.');
    }
    return this.active;
  }

  disconnect(): void {
    this.active = null;
  }

  private async exists(p: string): Promise<boolean> {
    try {
      await fs.access(p);
      return true;
    } catch {
      return false;
    }
  }

  private async isDir(p: string): Promise<boolean> {
    try {
      return (await fs.stat(p)).isDirectory();
    } catch {
      return false;
    }
  }
}
