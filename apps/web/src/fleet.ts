import { defineStore } from 'pinia';
import { api, type ClientView, type ImageBuildJob, type ImageStatus, type MemoryStatus } from './api';

interface State {
  image: ImageStatus | null;
  clients: ClientView[];
  buildJob: ImageBuildJob | null;
  memory: MemoryStatus | null;
  memoryJob: ImageBuildJob | null;
  loading: boolean;
  error: string | null;
}

export const useFleet = defineStore('fleet', {
  state: (): State => ({ image: null, clients: [], buildJob: null, memory: null, memoryJob: null, loading: false, error: null }),
  actions: {
    async run<T>(fn: () => Promise<T>): Promise<T | undefined> {
      this.error = null;
      this.loading = true;
      try {
        return await fn();
      } catch (e) {
        this.error = (e as Error).message;
        return undefined;
      } finally {
        this.loading = false;
      }
    },
    async refresh() {
      this.image = (await this.run(() => api.fleetImage())) ?? this.image;
      this.clients = (await this.run(() => api.fleetClients())) ?? [];
      this.memory = (await api.fleetMemory().catch(() => null)) ?? this.memory;
    },
    async memUp() {
      const res = await this.run(() => api.fleetMemoryUp());
      if (!res) return;
      await this.pollMemory(res.jobId);
    },
    async pollMemory(jobId: string) {
      this.memoryJob = await api.fleetMemoryJob(jobId).catch(() => null);
      if (this.memoryJob?.status === 'running') {
        setTimeout(() => this.pollMemory(jobId), 1500);
      } else {
        this.memory = (await api.fleetMemory().catch(() => null)) ?? this.memory;
      }
    },
    async memStop() {
      await this.run(() => api.fleetMemoryStop());
      this.memory = (await api.fleetMemory().catch(() => null)) ?? this.memory;
    },
    async build() {
      const res = await this.run(() => api.fleetBuild());
      if (!res) return;
      await this.pollBuild(res.jobId);
    },
    async pollBuild(jobId: string) {
      this.buildJob = await api.fleetBuildJob(jobId).catch(() => null);
      if (this.buildJob?.status === 'running') {
        setTimeout(() => this.pollBuild(jobId), 1500);
      } else {
        await this.refresh();
      }
    },
    async create(name: string) {
      const res = await this.run(() => api.fleetCreate(name));
      await this.refresh();
      return res;
    },
    async start(id: string) {
      await this.run(() => api.fleetStart(id));
      await this.refresh();
    },
    async stop(id: string) {
      await this.run(() => api.fleetStop(id));
      await this.refresh();
    },
    async restart(id: string) {
      await this.run(() => api.fleetRestart(id));
      await this.refresh();
    },
    async remove(id: string, removeData: boolean) {
      await this.run(() => api.fleetRemove(id, removeData));
      await this.refresh();
    },
  },
});
