export interface ManifestEnvVar {
  name: string;
  prompt: string;
  default?: string;
  required: boolean;
  secret: boolean;
  /** value is a path to a FILE (e.g. an Apple .p8). The installer copies the
   *  host file into the install's secrets dir and stores the resolved path. */
  file?: boolean;
}

export interface ManifestCronJob {
  id: string;
  name?: string;
  schedule: string;
  no_agent?: boolean;
  python?: string;
  script: string;
  workdir?: string;
  env?: Record<string, string>;
}

export interface ManifestProvides {
  /** env vars set on the mcp_servers.<name> block (passed to the server process) */
  server_env?: Record<string, string>;
  /** skill folders copied into <install>/<dest> */
  skills?: { src: string; dest: string }[];
  /** hook folders copied into <install>/<dest>; template => resolve ${...} in files */
  hooks?: { src: string; dest: string; template?: boolean }[];
  /** best-effort cron jobs merged into <install>/cron/jobs.json */
  cron?: ManifestCronJob[];
}

export interface HermesManifest {
  manifestVersion: number;
  name: string;
  description: string;
  source?: string;
  transport: {
    type: 'stdio' | 'http';
    command?: string;
    args?: string[];
    url?: string;
  };
  install?: {
    type: 'git' | 'npm' | 'uvx' | 'local';
    url?: string;
    ref?: string;
    bootstrap?: string[];
    /** python expression run via the venv (`python -c <warmup>`) after deps —
     *  e.g. to pre-download an embedding model so first use isn't a timeout. */
    warmup?: string;
  };
  auth: {
    type: 'api_key' | 'oauth' | 'none';
    env: ManifestEnvVar[];
  };
  tools: { default_enabled: string[] };
  provides?: ManifestProvides;
  /** Optional bulk-ingest: a script (in the module) the dashboard runs over a chosen folder. */
  ingest?: { script: string; label?: string };
  post_install?: string;
}

export interface CatalogModule {
  /** module directory name (== mcp_servers key on install) */
  id: string;
  manifestPath: string;
  /** absolute module dir, used as ${INSTALL_DIR} */
  modulePath: string;
  manifest: HermesManifest | null;
  manifestValid: boolean;
  validationErrors: string[];
}
