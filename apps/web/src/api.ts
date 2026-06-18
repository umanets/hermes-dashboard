export interface Installation {
  id: string;
  rootPath: string;
  valid: boolean;
  validationErrors: string[];
}

export interface Skill {
  name: string;
  category: string;
  path: string;
  enabled: boolean;
  frontmatterValid: boolean;
  frontmatterErrors: string[];
  frontmatter: { description?: string } | null;
}

export interface SkillPreview {
  name: string;
  category: string;
  path: string;
  enabled: boolean;
  frontmatterValid: boolean;
  frontmatter: { description?: string; version?: string; author?: string; license?: string } | null;
  content: string;
}

export interface McpTool {
  name: string;
  enabled: boolean;
}

export interface McpServer {
  name: string;
  transport: string;
  enabled: boolean;
  command?: string;
  url?: string;
  toolFilterMode: string;
  tools: McpTool[];
  warnings: string[];
}

export interface CatalogModule {
  id: string;
  modulePath: string;
  manifestValid: boolean;
  validationErrors: string[];
  manifest: {
    name: string;
    description: string;
    transport: { type: string };
    auth: { type: string; env: { name: string }[] };
    tools: { default_enabled: string[] };
    provides?: {
      skills?: unknown[];
      hooks?: unknown[];
      cron?: unknown[];
    };
    ingest?: { script: string; label?: string };
  } | null;
}

export interface LibrarySkill {
  collection: 'my' | 'community';
  category: string;
  name: string;
  key: string;
  description: string;
  valid: boolean;
  errors: string[];
}

export interface CredentialField {
  name: string;
  prompt: string;
  default?: string;
  required: boolean;
  secret: boolean;
  /** value is a host path to a file copied into the install (e.g. an Apple .p8) */
  file: boolean;
  present: boolean;
}

export interface InstallStep {
  key: string;
  title: string;
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped';
  log: string;
}

export interface InstallJobResult {
  module: string;
  serverName: string;
  localPath: string;
  appliedEnvKeys: string[];
  placedSkills: string[];
  placedHooks: string[];
  warnings: string[];
  notice: string;
}

export interface InstallJob {
  id: string;
  module: string;
  status: 'running' | 'completed' | 'failed';
  steps: InstallStep[];
  result?: InstallJobResult;
  error?: string;
  startedAt: string;
}

export interface AppleAuthJob {
  id: string;
  status: 'running' | 'completed' | 'failed';
  message: string;
  error?: string;
  startedAt: string;
}

export interface IngestJob {
  id: string;
  module: string;
  dir: string;
  status: 'running' | 'completed' | 'failed';
  progress: { done: number; total: number };
  result?: { files: number; added: number; skipped: number; chunks: number };
  log: string;
  error?: string;
  startedAt: string;
}

export interface ClientView {
  id: string;
  name: string;
  containerName: string;
  hostDir: string;
  createdAt: string;
  status: string;
  setupDone: boolean;
}

export interface ImageStatus {
  tag: string;
  exists: boolean;
  dockerOk: boolean;
}

export interface ImageBuildJob {
  id: string;
  status: 'running' | 'completed' | 'failed';
  log: string;
  error?: string;
  startedAt: string;
}

export interface MemoryStatus {
  name: string;
  image: string;
  imagePulled: boolean;
  running: boolean;
  exists: boolean;
  dockerOk: boolean;
  llmKeySet: boolean;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const msg = data?.message ?? res.statusText;
    throw new Error(Array.isArray(msg) ? JSON.stringify(msg) : String(msg));
  }
  return data as T;
}

export const api = {
  connect: (path: string) => req<Installation>('POST', '/installation/connect', { path }),
  current: () => req<Installation | null>('GET', '/installation'),
  disconnect: () => req<{ disconnected: boolean }>('DELETE', '/installation'),

  skills: () => req<Skill[]>('GET', '/skills'),
  toggleSkill: (name: string, enabled: boolean) =>
    req('PATCH', `/skills/${encodeURIComponent(name)}`, { enabled }),
  bulkSkills: (enabled: boolean, category?: string) =>
    req<{ changed: number }>('PATCH', '/skills', category ? { enabled, category } : { enabled }),
  skillPreview: (name: string) =>
    req<SkillPreview>('GET', `/skills/${encodeURIComponent(name)}/preview`),

  mcpServers: () => req<McpServer[]>('GET', '/mcp-servers'),
  toggleServer: (name: string, enabled: boolean) =>
    req('PATCH', `/mcp-servers/${encodeURIComponent(name)}`, { enabled }),
  toggleTool: (name: string, tool: string, enabled: boolean) =>
    req('PATCH', `/mcp-servers/${encodeURIComponent(name)}/tools/${encodeURIComponent(tool)}`, { enabled }),

  catalog: () => req<CatalogModule[]>('GET', '/catalog'),
  credentialForm: (id: string) =>
    req<{ module: string; fields: CredentialField[] }>('GET', `/catalog/${encodeURIComponent(id)}/credential-form`),
  install: (id: string, env: Record<string, string>, tools?: string[]) =>
    req<{ jobId: string }>('POST', `/catalog/${encodeURIComponent(id)}/install`, { env, tools }),
  installJob: (jobId: string) => req<InstallJob>('GET', `/catalog/install-jobs/${encodeURIComponent(jobId)}`),
  ingest: (id: string, dir: string, vault?: string) =>
    req<{ jobId: string }>('POST', `/catalog/${encodeURIComponent(id)}/ingest`, vault ? { dir, vault } : { dir }),
  ingestJob: (jobId: string) => req<IngestJob>('GET', `/catalog/ingest-jobs/${encodeURIComponent(jobId)}`),
  appleAuthorize: () => req<{ jobId: string }>('POST', '/catalog/apple-music/authorize'),
  appleAuthorizeJob: (jobId: string) =>
    req<AppleAuthJob>('GET', `/catalog/apple-music/authorize-jobs/${encodeURIComponent(jobId)}`),
  appleSetToken: (token: string) => req<{ message: string }>('POST', '/catalog/apple-music/token', { token }),

  // ---- skill library (my / community) ----
  skillLibrary: (collection: string) => req<LibrarySkill[]>('GET', `/skill-library/${encodeURIComponent(collection)}`),
  skillLibraryPreview: (collection: string, category: string, name: string) =>
    req<{ collection: string; category: string; name: string; content: string }>(
      'GET',
      `/skill-library/${encodeURIComponent(collection)}/${encodeURIComponent(category)}/${encodeURIComponent(name)}/preview`,
    ),
  skillLibraryInstall: (collection: string, category: string, name: string) =>
    req<{ placed: string }>('POST', `/skill-library/${encodeURIComponent(collection)}/install`, { category, name }),

  // ---- fleet ----
  fleetImage: () => req<ImageStatus>('GET', '/fleet/image'),
  fleetBuild: () => req<{ jobId: string }>('POST', '/fleet/image/build'),
  fleetBuildJob: (jobId: string) => req<ImageBuildJob>('GET', `/fleet/image/build-jobs/${encodeURIComponent(jobId)}`),
  fleetClients: () => req<ClientView[]>('GET', '/fleet/clients'),
  fleetCreate: (name: string, skills?: { collection?: string; category: string; name: string }[]) =>
    req<{ client: ClientView; setupCommand: string; seededSkills: string[] }>(
      'POST',
      '/fleet/clients',
      skills && skills.length ? { name, skills } : { name },
    ),
  fleetStart: (id: string) => req('POST', `/fleet/clients/${encodeURIComponent(id)}/start`),
  fleetStop: (id: string) => req('POST', `/fleet/clients/${encodeURIComponent(id)}/stop`),
  fleetRestart: (id: string) =>
    req<{ ok: boolean; gatewayRelaunched?: boolean; error?: string }>('POST', `/fleet/clients/${encodeURIComponent(id)}/restart`),
  fleetRemove: (id: string, removeData: boolean) =>
    req('DELETE', `/fleet/clients/${encodeURIComponent(id)}?removeData=${removeData ? 'true' : 'false'}`),
  fleetLogs: (id: string) => req<{ logs: string }>('GET', `/fleet/clients/${encodeURIComponent(id)}/logs`),
  fleetSelect: (id: string) => req<Installation>('POST', `/fleet/clients/${encodeURIComponent(id)}/select`),
  fleetStartGateway: (id: string) =>
    req<{ ok: boolean; error?: string }>('POST', `/fleet/clients/${encodeURIComponent(id)}/gateway/start`),
  fleetStopGateway: (id: string) =>
    req<{ ok: boolean; error?: string }>('POST', `/fleet/clients/${encodeURIComponent(id)}/gateway/stop`),
  fleetClientMemory: (id: string) =>
    req<{ provider: string; bank: string | null }>('GET', `/fleet/clients/${encodeURIComponent(id)}/memory`),
  fleetEnableHindsight: (id: string) =>
    req<{ ok: boolean; bank: string; reloaded: boolean; warning?: string }>('POST', `/fleet/clients/${encodeURIComponent(id)}/memory/hindsight`),
  fleetDisableMemory: (id: string) =>
    req<{ ok: boolean; reloaded: boolean; error?: string }>('POST', `/fleet/clients/${encodeURIComponent(id)}/memory/off`),

  // ---- shared Hindsight memory service ----
  fleetMemory: () => req<MemoryStatus>('GET', '/fleet/memory'),
  fleetMemoryUp: () => req<{ jobId: string }>('POST', '/fleet/memory/up'),
  fleetMemoryJob: (jobId: string) => req<ImageBuildJob>('GET', `/fleet/memory/jobs/${encodeURIComponent(jobId)}`),
  fleetMemoryStop: () => req('POST', '/fleet/memory/stop'),
  fleetMemoryLogs: () => req<{ logs: string }>('GET', '/fleet/memory/logs'),
};
