export interface ConnectedInstallation {
  /** stable id = canonical root path */
  id: string;
  rootPath: string;
  configPath: string;
  envPath: string;
  skillsDir: string;
  connectedAt: string;
  valid: boolean;
  validationErrors: string[];
  /**
   * When set, this install is a Docker container: config edits still happen
   * host-side on the mounted volume (`rootPath`), but the install pipeline
   * (venv/pip/warmup/cron) must run INSIDE the container via `docker exec`,
   * and paths written into config must be CONTAINER paths under `dataDir`.
   */
  container?: { name: string; dataDir: string };
}

/** Optional context passed to connect() when the install is a Docker container. */
export interface ContainerContext {
  name: string;
  dataDir: string;
}
