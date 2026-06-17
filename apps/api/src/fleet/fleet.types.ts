export interface Client {
  /** slug derived from name; also the container suffix and host-dir name */
  id: string;
  name: string;
  /** docker container name = `hermes-<id>` */
  containerName: string;
  /** host dir mounted to /opt/data in the container (forward slashes) */
  hostDir: string;
  createdAt: string;
}

export interface ClientView extends Client {
  /** docker state: running | exited | created | paused | … | 'missing' (no container) */
  status: string;
  /** true once `hermes setup` has produced a config.yaml in the volume */
  setupDone: boolean;
}

export interface ImageBuildJob {
  id: string;
  status: 'running' | 'completed' | 'failed';
  log: string;
  error?: string;
  startedAt: string;
}
