<script setup lang="ts">
import { ref } from 'vue';
import { api } from '../api';
import { useDashboard } from '../store';

const store = useDashboard();

// ---- Apple Music User Token capture (only shown on the `music` server) ----
const appleBusy = ref(false);
const appleMsg = ref<string | null>(null);
const appleErr = ref<string | null>(null);
const pasteToken = ref('');
let poll: ReturnType<typeof setTimeout> | null = null;

async function authorizeApple() {
  appleBusy.value = true;
  appleErr.value = null;
  appleMsg.value = 'Opening Apple Music sign-in in your browser — finish it there…';
  try {
    const { jobId } = await api.appleAuthorize();
    await pollApple(jobId);
  } catch (e) {
    appleErr.value = (e as Error).message;
    appleBusy.value = false;
  }
}

async function pollApple(jobId: string) {
  let job;
  try {
    job = await api.appleAuthorizeJob(jobId);
  } catch (e) {
    appleErr.value = (e as Error).message;
    appleBusy.value = false;
    return;
  }
  appleMsg.value = job.message;
  if (job.status === 'running') {
    poll = setTimeout(() => pollApple(jobId), 1500);
  } else {
    appleBusy.value = false;
    if (job.status === 'failed') {
      appleErr.value = job.error ?? 'authorize failed';
      appleMsg.value = null;
    }
  }
}

async function saveToken() {
  if (!pasteToken.value.trim()) return;
  appleBusy.value = true;
  appleErr.value = null;
  try {
    const res = await api.appleSetToken(pasteToken.value.trim());
    appleMsg.value = res.message;
    pasteToken.value = '';
  } catch (e) {
    appleErr.value = (e as Error).message;
  } finally {
    appleBusy.value = false;
  }
}
</script>

<template>
  <div v-for="srv in store.servers" :key="srv.name" class="card">
    <div class="row" style="background:transparent;border:0;padding:0;margin:0">
      <div class="meta">
        <div class="name">
          {{ srv.name }}
          <span class="tag">{{ srv.transport }}</span>
          <span v-for="w in srv.warnings" :key="w" class="tag warn">{{ w }}</span>
        </div>
        <div class="desc">{{ srv.command ?? srv.url }}</div>
      </div>
      <div class="toggle" :class="{ on: srv.enabled }" @click="store.toggleServer(srv.name, !srv.enabled)" />
    </div>

    <div v-if="srv.toolFilterMode === 'include'" class="tool-list">
      <span
        v-for="t in srv.tools"
        :key="t.name"
        class="chip"
        :class="t.enabled ? 'on' : 'off'"
        @click="store.toggleTool(srv.name, t.name, !t.enabled)"
      >{{ t.name }}</span>
    </div>
    <p v-else class="muted" style="margin:10px 0 0; font-size:12px">
      All tools enabled (no allowlist). Per-tool toggles appear once an allowlist exists
      (set automatically when installed from the catalog).
    </p>

    <!-- Apple Music: capture the per-user Music User Token -->
    <div v-if="srv.name === 'music'" class="apple-auth">
      <div class="apple-row">
        <button class="sm" :disabled="appleBusy" @click="authorizeApple">
          {{ appleBusy ? 'Authorizing…' : ' Authorize Apple Music' }}
        </button>
        <span class="muted" style="font-size:11px">browser sign-in opens on the host; the token is saved &amp; applied automatically</span>
      </div>
      <div class="apple-row">
        <input
          v-model="pasteToken"
          type="password"
          placeholder="…or paste a Music User Token"
          @keyup.enter="saveToken"
        />
        <button class="ghost sm" :disabled="appleBusy || !pasteToken.trim()" @click="saveToken">Save token</button>
      </div>
      <p v-if="appleMsg" class="ok" style="margin:8px 0 0; font-size:12px">{{ appleMsg }}</p>
      <p v-if="appleErr" class="err" style="margin:8px 0 0; font-size:12px">{{ appleErr }}</p>
    </div>
  </div>
  <p v-if="!store.servers.length" class="muted">No MCP servers configured.</p>
</template>

<style scoped>
.apple-auth {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed var(--line, #2a2a3a);
}
.apple-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 8px;
}
.apple-row input {
  flex: 1;
}
</style>
