<script setup lang="ts">
import { onUnmounted, onMounted, ref } from 'vue';
import { api, type CredentialField, type InstallJob } from '../api';
import { useDashboard } from '../store';

const props = defineProps<{ moduleId: string }>();
const emit = defineEmits<{ close: [] }>();
const store = useDashboard();

const fields = ref<CredentialField[]>([]);
const values = ref<Record<string, string>>({});
const loading = ref(true);
const submitting = ref(false);
const error = ref<string | null>(null);
const job = ref<InstallJob | null>(null);
let poll: ReturnType<typeof setTimeout> | null = null;

onMounted(async () => {
  try {
    const form = await api.credentialForm(props.moduleId);
    fields.value = form.fields;
    for (const f of form.fields) values.value[f.name] = f.default ?? '';
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
});
onUnmounted(() => poll && clearTimeout(poll));

const ICON: Record<string, string> = { pending: '○', running: '◐', done: '✓', failed: '✗', skipped: '–' };

async function submit() {
  submitting.value = true;
  error.value = null;
  try {
    const env: Record<string, string> = {};
    for (const [k, v] of Object.entries(values.value)) if (v !== '') env[k] = v;
    const { jobId } = await api.install(props.moduleId, env);
    await pollJob(jobId);
  } catch (e) {
    error.value = (e as Error).message;
    submitting.value = false;
  }
}

async function pollJob(jobId: string) {
  try {
    job.value = await api.installJob(jobId);
  } catch (e) {
    error.value = (e as Error).message;
    return;
  }
  if (job.value.status === 'running') {
    poll = setTimeout(() => pollJob(jobId), 1000);
  } else {
    submitting.value = false;
    if (job.value.status === 'completed') await store.refreshServers();
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="modal">
      <h3 style="margin-top:0">Install: {{ moduleId }}</h3>

      <div v-if="loading" class="muted">Loading credential form…</div>

      <!-- progress / result -->
      <template v-else-if="job">
        <div class="steps">
          <div v-for="s in job.steps" :key="s.key" class="step" :class="s.status">
            <span class="ic">{{ ICON[s.status] }}</span>
            <span class="t">{{ s.title }}</span>
            <span v-if="s.status === 'skipped'" class="muted" style="margin-left:auto;font-size:11px">skipped</span>
          </div>
        </div>
        <pre v-if="job.steps.find((s) => s.status === 'failed')" class="err" style="margin-top:10px">{{ job.steps.find((s) => s.status === 'failed')?.log }}</pre>

        <div v-if="job.status === 'completed' && job.result" class="ok" style="margin-top:12px">
          <strong>Installed “{{ job.result.serverName }}”.</strong>
          <p style="margin:8px 0 0">{{ job.result.notice }}</p>
          <p class="muted" style="margin:6px 0 0">into {{ job.result.localPath }}</p>
          <p v-if="job.result.placedSkills.length || job.result.placedHooks.length" class="muted" style="margin:4px 0 0">
            +{{ job.result.placedSkills.length }} skill, +{{ job.result.placedHooks.length }} hook, cron registered
          </p>
          <ul v-if="job.result.warnings.length" style="margin:8px 0 0">
            <li v-for="w in job.result.warnings" :key="w" class="tag warn" style="display:block;margin:3px 0">{{ w }}</li>
          </ul>
        </div>
        <div v-else-if="job.status === 'failed'" class="err" style="margin-top:12px">{{ job.error }}</div>

        <div class="actions">
          <button :disabled="job.status === 'running'" @click="emit('close')">
            {{ job.status === 'running' ? 'Installing…' : 'Done' }}
          </button>
        </div>
      </template>

      <!-- credential form -->
      <template v-else>
        <p class="muted" style="margin-top:0">
          On install the dashboard copies the module onto this machine, builds its venv, writes config &amp;
          credentials, and registers the cron job — no terminal needed.
        </p>
        <div v-for="f in fields" :key="f.name" class="field">
          <label>
            {{ f.name }} <span v-if="f.required" class="req">*</span>
            <span v-if="f.file" class="present">· file</span>
            <span v-if="f.present" class="present">· already set</span>
          </label>
          <input
            v-model="values[f.name]"
            :type="f.secret ? 'password' : 'text'"
            :placeholder="f.prompt + (f.present ? ' (set — leave blank to keep)' : '')"
          />
          <p v-if="f.file" class="muted" style="margin:4px 0 0; font-size:11px">
            Host path to the file — it's copied into the install's <code>secrets/</code> and the in-container path is stored.
          </p>
        </div>
        <div v-if="error" class="err">{{ error }}</div>
        <div class="actions">
          <button class="ghost" @click="emit('close')">Cancel</button>
          <button :disabled="submitting" @click="submit">{{ submitting ? 'Starting…' : 'Install' }}</button>
        </div>
      </template>
    </div>
  </div>
</template>
