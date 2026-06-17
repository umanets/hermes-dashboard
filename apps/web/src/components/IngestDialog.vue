<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue';
import { api, type IngestJob } from '../api';

const props = defineProps<{ moduleId: string; label?: string }>();
const emit = defineEmits<{ close: [] }>();

const dir = ref('');
const vault = ref('');
const submitting = ref(false);
const error = ref<string | null>(null);
const job = ref<IngestJob | null>(null);
let poll: ReturnType<typeof setTimeout> | null = null;
onUnmounted(() => poll && clearTimeout(poll));

const pct = computed(() => {
  const p = job.value?.progress;
  if (!p || !p.total) return 0;
  return Math.min(100, Math.round((p.done / p.total) * 100));
});

async function submit() {
  submitting.value = true;
  error.value = null;
  try {
    const { jobId } = await api.ingest(props.moduleId, dir.value.trim(), vault.value.trim() || undefined);
    await pollJob(jobId);
  } catch (e) {
    error.value = (e as Error).message;
    submitting.value = false;
  }
}

async function pollJob(jobId: string) {
  try {
    job.value = await api.ingestJob(jobId);
  } catch (e) {
    error.value = (e as Error).message;
    submitting.value = false;
    return;
  }
  if (job.value.status === 'running') {
    poll = setTimeout(() => pollJob(jobId), 700);
  } else {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="modal">
      <h3 style="margin-top:0">Ingest → {{ moduleId }}</h3>

      <!-- running / result -->
      <template v-if="job">
        <div class="muted" style="font-size:12px; margin-bottom:6px">
          {{ job.progress.done }} / {{ job.progress.total || '…' }} files
          <span v-if="job.status === 'running'" class="scan">· ingesting</span>
        </div>
        <div class="pbar"><div class="pfill" :style="{ width: pct + '%' }" /></div>
        <div class="muted" style="font-size:11px; margin-top:4px">{{ pct }}%</div>

        <pre v-if="job.log" class="ingest-log">{{ job.log }}</pre>

        <div v-if="job.status === 'completed' && job.result" class="ok" style="margin-top:10px">
          <strong>Done.</strong>
          {{ job.result.added }} added · {{ job.result.skipped }} skipped ·
          {{ job.result.chunks }} chunks from {{ job.result.files }} files.
        </div>
        <div v-else-if="job.status === 'failed'" class="err" style="margin-top:10px">{{ job.error }}</div>

        <div class="actions">
          <button :disabled="job.status === 'running'" @click="emit('close')">
            {{ job.status === 'running' ? 'Ingesting…' : 'Done' }}
          </button>
        </div>
      </template>

      <!-- form -->
      <template v-else>
        <p class="muted" style="margin-top:0">
          {{ label ?? 'Ingest a folder of Markdown into memory.' }}
          Enter the <strong>absolute folder path</strong> on this machine (browsers can't
          pick a server-side folder).
        </p>
        <div class="field">
          <label>Folder path <span class="req">*</span></label>
          <input v-model="dir" placeholder="C:/Users/you/Obsidian/MyVault  (or /home/you/vault)" />
        </div>
        <div class="field">
          <label>Obsidian vault name <span class="muted">(optional — default: folder name)</span></label>
          <input v-model="vault" placeholder="MyVault" />
        </div>
        <div v-if="error" class="err">{{ error }}</div>
        <div class="actions">
          <button class="ghost" @click="emit('close')">Cancel</button>
          <button :disabled="submitting || !dir.trim()" @click="submit">
            {{ submitting ? 'Starting…' : 'Ingest' }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>
