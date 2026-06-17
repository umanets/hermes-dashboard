<script setup lang="ts">
import { computed, ref } from 'vue';
import { useDashboard } from '../store';
import InstallDialog from './InstallDialog.vue';
import IngestDialog from './IngestDialog.vue';

const store = useDashboard();
const installing = ref<string | null>(null);
const ingesting = ref<{ id: string; label?: string } | null>(null);

// names already present as MCP servers in the connected install
const installed = computed(() => new Set(store.servers.map((s) => s.name)));
</script>

<template>
  <div v-for="m in store.catalog" :key="m.id" class="card">
    <div class="row" style="background:transparent;border:0;padding:0;margin:0">
      <div class="meta">
        <div class="name">
          {{ m.manifest?.name ?? m.id }}
          <span v-if="m.manifest" class="tag">{{ m.manifest.transport.type }}</span>
          <span v-if="m.manifest" class="tag">{{ m.manifest.auth.type }}</span>
          <span v-if="m.manifest && installed.has(m.manifest.name)" class="tag live">installed</span>
          <span v-if="m.manifest?.provides" class="tag" style="color:var(--magenta);border-color:var(--magenta)">bundle</span>
          <span v-if="!m.manifestValid" class="tag warn">invalid manifest</span>
        </div>
        <div class="desc">{{ m.manifest?.description ?? m.validationErrors.join('; ') }}</div>
      </div>
      <div style="display:flex; gap:6px; flex:none">
        <button class="sm" :disabled="!m.manifestValid" @click="installing = m.id">
          {{ m.manifest && installed.has(m.manifest.name) ? 'Reconfigure' : 'Install' }}
        </button>
        <button
          v-if="m.manifest?.ingest && installed.has(m.manifest.name)"
          class="ghost sm"
          @click="ingesting = { id: m.id, label: m.manifest.ingest.label }"
        >Ingest</button>
      </div>
    </div>
    <p v-if="m.manifest" class="muted" style="margin:10px 0 0; font-size:12px">
      {{ m.manifest.tools.default_enabled.length }} tools · {{ m.manifest.auth.env.length }} credential field(s)
    </p>
  </div>
  <p v-if="!store.catalog.length" class="muted">No modules found in the catalog.</p>

  <InstallDialog v-if="installing" :module-id="installing" @close="installing = null" />
  <IngestDialog v-if="ingesting" :module-id="ingesting.id" :label="ingesting.label" @close="ingesting = null" />
</template>
