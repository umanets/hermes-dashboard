<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { api } from '../api';
import { useFleet } from '../fleet';

const emit = defineEmits<{ manage: [id: string] }>();
const fleet = useFleet();

const newName = ref('');
const creating = ref(false);
const createdCmd = ref<string | null>(null);
const logsFor = ref<string | null>(null);
const logsText = ref('');
const memLogs = ref('');
const memProvider = ref<Record<string, string>>({});
const memBusy = ref<string | null>(null);

onMounted(async () => {
  await fleet.refresh();
  loadMemory();
});

async function loadMemory() {
  for (const c of fleet.clients) {
    if (!c.setupDone) continue;
    api.fleetClientMemory(c.id).then((m) => (memProvider.value[c.id] = m.provider)).catch(() => undefined);
  }
}

async function enableMemory(id: string) {
  memBusy.value = id;
  logsFor.value = id;
  logsText.value = 'enabling Hindsight memory…';
  const r = await api.fleetEnableHindsight(id).catch((e) => ({ ok: false, error: String(e) } as any));
  memBusy.value = null;
  if (r.ok) {
    memProvider.value[id] = 'hindsight';
    logsText.value = `Hindsight memory enabled (bank: ${r.bank}). ${r.reloaded ? 'Gateway reloaded.' : ''} ${r.warning ?? ''}`.trim();
  } else {
    logsText.value = `failed: ${r.error ?? ''}`;
  }
}

async function disableMemory(id: string) {
  memBusy.value = id;
  logsFor.value = id;
  logsText.value = 'reverting to built-in memory…';
  const r = await api.fleetDisableMemory(id).catch((e) => ({ ok: false, error: String(e) } as any));
  memBusy.value = null;
  memProvider.value[id] = 'none';
  logsText.value = r.ok ? 'Reverted to built-in memory.' : `failed: ${r.error ?? ''}`;
}

const dot = (s: string) => (s === 'running' ? 'live' : s === 'missing' || s === 'exited' ? 'off' : 'warn');
const setupCmd = (containerName: string) => `docker exec -it ${containerName} hermes setup`;

async function create() {
  if (!newName.value.trim()) return;
  creating.value = true;
  const res = await fleet.create(newName.value.trim());
  creating.value = false;
  if (res) {
    createdCmd.value = res.setupCommand;
    newName.value = '';
  }
}

async function restartAgent(id: string) {
  logsFor.value = id;
  logsText.value = 'restarting…';
  const r = await api.fleetRestart(id).catch((e) => ({ ok: false, error: String(e) })) as { ok: boolean; gatewayRelaunched?: boolean; error?: string };
  await fleet.refresh();
  logsText.value = r.ok
    ? (r.gatewayRelaunched ? 'Restarted — background service relaunched.' : 'Restarted (gateway was not running; press Start Background Service if needed).')
    : `restart failed: ${r.error ?? ''}`;
}

async function startService(id: string) {
  logsFor.value = id;
  logsText.value = 'starting background service…';
  const r = await api.fleetStartGateway(id).catch((e) => ({ ok: false, error: String(e) }));
  logsText.value = r.ok ? 'Background service started (hermes gateway start). Check logs / Telegram.' : `failed: ${r.error}`;
}

async function stopService(id: string) {
  logsFor.value = id;
  logsText.value = 'stopping background service…';
  const r = await api.fleetStopGateway(id).catch((e) => ({ ok: false, error: String(e) }));
  logsText.value = r.ok ? 'Background service stopped (hermes gateway stop).' : `failed: ${r.error}`;
}

async function showMemoryLogs() {
  if (memLogs.value) { memLogs.value = ''; return; }
  memLogs.value = 'loading…';
  memLogs.value = (await api.fleetMemoryLogs().catch((e) => ({ logs: String(e) }))).logs || '(empty)';
}

async function showLogs(id: string) {
  if (logsFor.value === id) { logsFor.value = null; return; }
  logsFor.value = id;
  logsText.value = 'loading…';
  logsText.value = (await api.fleetLogs(id).catch((e) => ({ logs: String(e) }))).logs || '(empty)';
}
</script>

<template>
  <h1>Agent Fleet</h1>
  <p class="sub">One always-on Hermes container per client. Provision skills &amp; tools after setup.</p>

  <div v-if="fleet.error" class="err">{{ fleet.error }}</div>

  <!-- base image status -->
  <div class="card">
    <div class="row" style="background:transparent;border:0;padding:0;margin:0">
      <div class="meta">
        <div class="name">
          Base image
          <span v-if="fleet.image && !fleet.image.dockerOk" class="tag warn">docker unavailable</span>
          <span v-else-if="fleet.image?.exists" class="tag live">{{ fleet.image.tag }} ready</span>
          <span v-else class="tag warn">not built</span>
        </div>
        <div class="desc">Hermes container image used for every agent.</div>
      </div>
      <button
        class="sm"
        :disabled="!fleet.image?.dockerOk || fleet.buildJob?.status === 'running'"
        @click="fleet.build()"
      >{{ fleet.buildJob?.status === 'running' ? 'Building…' : (fleet.image?.exists ? 'Rebuild' : 'Build image') }}</button>
    </div>
    <pre v-if="fleet.buildJob" class="ingest-log" style="margin-top:10px">{{ fleet.buildJob.log || '…' }}</pre>
  </div>

  <!-- shared Hindsight memory service -->
  <div class="card">
    <div class="row" style="background:transparent;border:0;padding:0;margin:0">
      <div class="meta">
        <div class="name">
          Memory service (Hindsight)
          <span v-if="fleet.memory && !fleet.memory.dockerOk" class="tag warn">docker unavailable</span>
          <span v-else-if="fleet.memory?.running" class="tag live">running</span>
          <span v-else-if="fleet.memory?.exists" class="tag warn">stopped</span>
          <span v-else class="tag warn">not started</span>
          <span v-if="fleet.memory && !fleet.memory.llmKeySet" class="tag warn">OPENROUTER_API_KEY unset</span>
        </div>
        <div class="desc">Shared learning memory; each agent gets its own bank. UI: <a href="http://127.0.0.1:9999" target="_blank">127.0.0.1:9999</a></div>
      </div>
      <div style="display:flex; gap:6px; flex:none">
        <button
          class="sm"
          :disabled="!fleet.memory?.dockerOk || !fleet.memory?.llmKeySet || fleet.memoryJob?.status === 'running'"
          @click="fleet.memUp()"
        >{{ fleet.memoryJob?.status === 'running' ? 'Starting…' : (fleet.memory?.running ? 'Restart' : 'Start') }}</button>
        <button v-if="fleet.memory?.running" class="ghost sm" @click="fleet.memStop()">Stop</button>
        <button class="ghost sm" @click="showMemoryLogs()">Logs</button>
      </div>
    </div>
    <pre v-if="fleet.memoryJob" class="ingest-log" style="margin-top:10px">{{ fleet.memoryJob.log || '…' }}</pre>
    <pre v-if="memLogs" class="ingest-log" style="margin-top:10px">{{ memLogs }}</pre>
  </div>

  <!-- create -->
  <div class="bar" style="margin:14px 0">
    <input v-model="newName" placeholder="New client name (e.g. acme-alice)" @keyup.enter="create" />
    <button :disabled="creating || !fleet.image?.exists" @click="create">+ Agent</button>
  </div>
  <div v-if="createdCmd" class="ok" style="margin-bottom:12px">
    Agent created. Run initial setup in a terminal:
    <pre style="white-space:pre-wrap;margin:6px 0 0">{{ createdCmd }}</pre>
  </div>

  <!-- agents -->
  <div v-for="c in fleet.clients" :key="c.id" class="card">
    <div class="row" style="background:transparent;border:0;padding:0;margin:0">
      <div class="meta">
        <div class="name">
          {{ c.name }}
          <span class="tag" :class="dot(c.status)">{{ c.status }}</span>
          <span v-if="!c.setupDone" class="tag warn">needs setup</span>
        </div>
        <div class="desc">{{ c.containerName }} · {{ c.hostDir }}</div>
      </div>
      <div style="display:flex; gap:6px; flex:none">
        <button v-if="c.status !== 'running'" class="ghost sm" @click="fleet.start(c.id)">Start</button>
        <button v-else class="ghost sm" @click="fleet.stop(c.id)">Stop</button>
        <button class="ghost sm" @click="restartAgent(c.id)">Restart</button>
        <button class="ghost sm" @click="startService(c.id)">Start Background Service</button>
        <button class="ghost sm" @click="stopService(c.id)">Stop Background Service</button>
        <button class="ghost sm" @click="showLogs(c.id)">Logs</button>
        <button class="ghost sm" @click="fleet.remove(c.id, false)">Remove</button>
        <button class="sm" :disabled="!c.setupDone" :title="c.setupDone ? '' : 'run setup first'" @click="emit('manage', c.id)">Manage</button>
      </div>
    </div>
    <pre v-if="!c.setupDone" class="muted" style="margin:10px 0 0; font-size:12px; white-space:pre-wrap">{{ setupCmd(c.containerName) }}</pre>

    <!-- memory provider (Hindsight native) -->
    <div v-if="c.setupDone" class="row" style="background:transparent;border:0;padding:8px 0 0;margin:0;align-items:center">
      <span class="muted" style="font-size:12px">
        Memory:
        <strong v-if="memProvider[c.id] === 'hindsight'" style="color:var(--cyan)">Hindsight · bank {{ c.id }}</strong>
        <strong v-else>built-in only</strong>
      </span>
      <button
        v-if="memProvider[c.id] !== 'hindsight'"
        class="ghost sm"
        :disabled="memBusy === c.id || !fleet.memory?.running"
        :title="fleet.memory?.running ? '' : 'start the Memory service (Hindsight) first'"
        @click="enableMemory(c.id)"
      >{{ memBusy === c.id ? 'Enabling…' : 'Enable Hindsight memory' }}</button>
      <button v-else class="ghost sm" :disabled="memBusy === c.id" @click="disableMemory(c.id)">Disable</button>
    </div>

    <pre v-if="logsFor === c.id" class="ingest-log" style="margin-top:10px">{{ logsText }}</pre>
  </div>
  <p v-if="!fleet.clients.length" class="muted">No agents yet. Build the image, then add one.</p>
</template>
