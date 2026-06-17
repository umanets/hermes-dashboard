import { ConflictException, Injectable, NotFoundException } from '@nestjs/common';
import { CatalogService } from '../catalog/catalog.service';
import { HermesConfigService, RawMcpServer } from '../hermes-config/hermes-config.service';
import { InstallationService } from '../installation/installation.service';
import { McpServer, McpTransport, ToolFilterMode } from './mcp.types';

@Injectable()
export class McpService {
  constructor(
    private readonly installation: InstallationService,
    private readonly config: HermesConfigService,
    private readonly catalog: CatalogService,
  ) {}

  async list(): Promise<McpServer[]> {
    const install = this.installation.getActiveOrThrow();
    const servers = (await this.config.read(install.rootPath)).mcp_servers;
    return Object.entries(servers).map(([name, raw]) => this.toView(name, raw));
  }

  async get(name: string): Promise<McpServer> {
    const found = (await this.list()).find((s) => s.name === name);
    if (!found) throw new NotFoundException(`No MCP server '${name}'.`);
    return found;
  }

  async setServerEnabled(name: string, enabled: boolean): Promise<{ name: string; enabled: boolean }> {
    const install = this.installation.getActiveOrThrow();
    await this.assertExists(name);
    await this.config.mutate(install.rootPath, (doc) => {
      doc.setIn(['mcp_servers', name, 'enabled'], enabled);
    });
    return { name, enabled };
  }

  async setToolEnabled(
    name: string,
    tool: string,
    enabled: boolean,
  ): Promise<{ name: string; tool: string; enabled: boolean }> {
    const install = this.installation.getActiveOrThrow();
    const server = await this.get(name);

    // For include-mode we mutate the list directly. For all/exclude we must
    // materialize an allowlist, which needs the full tool universe.
    let universe: string[] | null = null;
    if (server.toolFilterMode !== 'include') {
      universe = await this.resolveUniverse(name);
    }

    await this.config.mutate(install.rootPath, (doc) => {
      const raw = (doc.toJS()?.mcp_servers?.[name] ?? {}) as RawMcpServer;
      if (server.toolFilterMode === 'include') {
        const set = new Set(raw.tools?.include ?? []);
        if (enabled) set.add(tool);
        else set.delete(tool);
        doc.setIn(['mcp_servers', name, 'tools', 'include'], [...set]);
      } else {
        const excluded = new Set(raw.tools?.exclude ?? []);
        const enabledSet = new Set((universe as string[]).filter((t) => !excluded.has(t)));
        if (enabled) enabledSet.add(tool);
        else enabledSet.delete(tool);
        const include = (universe as string[]).filter((t) => enabledSet.has(t));
        doc.setIn(['mcp_servers', name, 'tools', 'include'], include);
        doc.deleteIn(['mcp_servers', name, 'tools', 'exclude']);
      }
    });
    return { name, tool, enabled };
  }

  private toView(name: string, raw: RawMcpServer): McpServer {
    const transport: McpTransport = raw.command ? 'stdio' : raw.url ? 'http' : 'unknown';
    const warnings: string[] = [];
    if (transport === 'unknown') warnings.push('Server has neither command nor url — malformed entry.');
    if (raw.command && raw.url) warnings.push('Server has both command and url — malformed entry.');

    let mode: ToolFilterMode = 'all';
    let tools: { name: string; enabled: boolean }[] = [];
    if (raw.tools?.include) {
      mode = 'include';
      tools = raw.tools.include.map((t) => ({ name: t, enabled: true }));
    } else if (raw.tools?.exclude) {
      mode = 'exclude';
      tools = raw.tools.exclude.map((t) => ({ name: t, enabled: false }));
    }

    return {
      name,
      transport,
      enabled: raw.enabled !== false,
      command: raw.command,
      args: raw.args,
      url: raw.url,
      toolFilterMode: mode,
      tools,
      warnings,
    };
  }

  private async assertExists(name: string): Promise<void> {
    const install = this.installation.getActiveOrThrow();
    const servers = (await this.config.read(install.rootPath)).mcp_servers;
    if (!servers[name]) throw new NotFoundException(`No MCP server '${name}'.`);
  }

  /** v1: derive the tool universe from the matching catalog manifest. */
  private async resolveUniverse(name: string): Promise<string[]> {
    const mod = (await this.catalog.list()).find((m) => m.manifest?.name === name || m.id === name);
    const universe = mod?.manifest?.tools.default_enabled ?? [];
    if (!universe.length) {
      throw new ConflictException(
        `Cannot determine the full tool list for '${name}' (no catalog manifest). ` +
          `Toggle individual tools only for servers installed from the catalog.`,
      );
    }
    return universe;
  }
}
