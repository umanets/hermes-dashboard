import { defineStore } from 'pinia';
import { api, type CatalogModule, type Installation, type LibrarySkill, type McpServer, type Skill } from './api';

interface State {
  installation: Installation | null;
  skills: Skill[];
  servers: McpServer[];
  catalog: CatalogModule[];
  mySkills: LibrarySkill[];
  communitySkills: LibrarySkill[];
  loading: boolean;
  error: string | null;
}

export const useDashboard = defineStore('dashboard', {
  state: (): State => ({
    installation: null,
    skills: [],
    servers: [],
    catalog: [],
    mySkills: [],
    communitySkills: [],
    loading: false,
    error: null,
  }),
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
    async refreshCurrent() {
      this.installation = (await this.run(() => api.current())) ?? null;
      if (this.installation?.valid) await this.refreshAll();
    },
    async connect(path: string) {
      const inst = await this.run(() => api.connect(path));
      if (inst) {
        this.installation = inst;
        if (inst.valid) await this.refreshAll();
      }
    },
    async disconnect() {
      await this.run(() => api.disconnect());
      this.installation = null;
      this.skills = [];
      this.servers = [];
      this.catalog = [];
    },
    async refreshAll() {
      await Promise.all([this.refreshSkills(), this.refreshServers(), this.refreshCatalog(), this.refreshLibrary()]);
    },
    async refreshLibrary() {
      this.mySkills = (await api.skillLibrary('my').catch(() => [])) ?? [];
      this.communitySkills = (await api.skillLibrary('community').catch(() => [])) ?? [];
    },
    async installLibrarySkill(collection: string, category: string, name: string) {
      const res = await this.run(() => api.skillLibraryInstall(collection, category, name));
      if (res) await this.refreshSkills(); // it now appears in the agent's Skills tab
      return res;
    },
    async refreshSkills() {
      this.skills = (await this.run(() => api.skills())) ?? [];
    },
    async refreshServers() {
      this.servers = (await this.run(() => api.mcpServers())) ?? [];
    },
    async refreshCatalog() {
      this.catalog = (await this.run(() => api.catalog())) ?? [];
    },
    async toggleSkill(name: string, enabled: boolean) {
      await this.run(() => api.toggleSkill(name, enabled));
      await this.refreshSkills();
    },
    async bulkSkills(enabled: boolean, category?: string) {
      await this.run(() => api.bulkSkills(enabled, category));
      await this.refreshSkills();
    },
    async toggleServer(name: string, enabled: boolean) {
      await this.run(() => api.toggleServer(name, enabled));
      await this.refreshServers();
    },
    async toggleTool(name: string, tool: string, enabled: boolean) {
      await this.run(() => api.toggleTool(name, tool, enabled));
      await this.refreshServers();
    },
  },
});
