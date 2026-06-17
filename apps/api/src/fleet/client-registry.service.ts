import { Injectable } from '@nestjs/common';
import * as fs from 'fs/promises';
import * as os from 'os';
import * as path from 'path';
import { AtomicFileService } from '../core/atomic-file.service';
import { PathService } from '../core/path.service';
import { Client } from './fleet.types';

/**
 * Persists the client→container registry as a single JSON file under the
 * clients root. Atomic writes via AtomicFileService.
 */
@Injectable()
export class ClientRegistryService {
  constructor(
    private readonly atomic: AtomicFileService,
    private readonly paths: PathService,
  ) {}

  clientsRoot(): string {
    return this.paths.canonical(process.env.CLIENTS_ROOT || path.join(os.homedir(), 'hermes-clients'));
  }

  private registryPath(): string {
    return path.join(this.clientsRoot(), 'clients.json');
  }

  async list(): Promise<Client[]> {
    try {
      const raw = await fs.readFile(this.registryPath(), 'utf8');
      const data = JSON.parse(raw);
      return Array.isArray(data) ? data : [];
    } catch {
      return [];
    }
  }

  async get(id: string): Promise<Client | undefined> {
    return (await this.list()).find((c) => c.id === id);
  }

  async add(client: Client): Promise<void> {
    const all = (await this.list()).filter((c) => c.id !== client.id);
    all.push(client);
    await this.save(all);
  }

  async remove(id: string): Promise<void> {
    await this.save((await this.list()).filter((c) => c.id !== id));
  }

  private async save(clients: Client[]): Promise<void> {
    await fs.mkdir(this.clientsRoot(), { recursive: true });
    await this.atomic.write(this.registryPath(), JSON.stringify(clients, null, 2), false);
  }
}
