<script setup lang="ts">
import { computed, ref } from 'vue';
import { api } from './api';
import { useDashboard } from './store';
import { useFleet } from './fleet';
import AgentsPanel from './components/AgentsPanel.vue';
import SkillsPanel from './components/SkillsPanel.vue';
import SkillLibraryPanel from './components/SkillLibraryPanel.vue';
import McpPanel from './components/McpPanel.vue';
import CatalogPanel from './components/CatalogPanel.vue';

const store = useDashboard();
const fleet = useFleet();
const view = ref<'agents' | 'manage'>('agents');
const tab = ref<'skills' | 'my' | 'community' | 'mcp' | 'catalog'>('skills');
const managingId = ref<string | null>(null);

const managingName = computed(
  () => fleet.clients.find((c) => c.id === managingId.value)?.name ?? managingId.value,
);

async function manage(id: string) {
  managingId.value = id;
  await api.fleetSelect(id).catch(() => undefined); // server-side: scope to this client's volume
  await store.refreshCurrent(); // load that client's skills / servers / catalog
  tab.value = 'skills';
  view.value = 'manage';
}

function backToAgents() {
  view.value = 'agents';
  store.disconnect();
  fleet.refresh();
}
</script>

<template>
  <div class="app">
    <AgentsPanel v-if="view === 'agents'" @manage="manage" />

    <template v-else>
      <div class="bar" style="justify-content:space-between">
        <button class="ghost" @click="backToAgents()">← Agents</button>
        <span class="muted">
          Managing <strong style="color:var(--cyan)">{{ managingName }}</strong>
          · changes apply on the next agent restart
        </span>
      </div>

      <div v-if="store.error" class="err">{{ store.error }}</div>
      <div v-if="store.installation && !store.installation.valid" class="card" style="margin-top:12px">
        <strong>Not set up yet.</strong>
        <p class="muted" style="margin:6px 0 0">
          Run the agent's <code>hermes setup</code> first (see the Agents screen), then come back.
        </p>
      </div>

      <template v-if="store.installation?.valid">
        <div class="tabs">
          <button :class="{ active: tab === 'skills' }" @click="tab = 'skills'">Skills ({{ store.skills.length }})</button>
          <button :class="{ active: tab === 'my' }" @click="tab = 'my'">My Skills ({{ store.mySkills.length }})</button>
          <button :class="{ active: tab === 'community' }" @click="tab = 'community'">Community ({{ store.communitySkills.length }})</button>
          <button :class="{ active: tab === 'mcp' }" @click="tab = 'mcp'">MCP Servers ({{ store.servers.length }})</button>
          <button :class="{ active: tab === 'catalog' }" @click="tab = 'catalog'">Catalog ({{ store.catalog.length }})</button>
        </div>
        <SkillsPanel v-if="tab === 'skills'" />
        <SkillLibraryPanel v-else-if="tab === 'my'" collection="my" />
        <SkillLibraryPanel v-else-if="tab === 'community'" collection="community" />
        <McpPanel v-else-if="tab === 'mcp'" />
        <CatalogPanel v-else />
      </template>
    </template>
  </div>
</template>
