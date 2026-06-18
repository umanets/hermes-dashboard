<script setup lang="ts">
import { computed, ref } from 'vue';
import { api, type LibrarySkill } from '../api';
import { useDashboard } from '../store';

const props = defineProps<{ collection: 'my' | 'community' }>();
const store = useDashboard();

const busy = ref<string | null>(null);
const note = ref<string | null>(null);
const previewOf = ref<LibrarySkill | null>(null);
const previewText = ref('');

const skills = computed<LibrarySkill[]>(() => (props.collection === 'my' ? store.mySkills : store.communitySkills));

// names already present in the connected agent's skills tab → show "in agent"
const installedNames = computed(() => new Set(store.skills.map((s) => s.name)));

async function add(s: LibrarySkill) {
  busy.value = s.key;
  note.value = null;
  const res = await store.installLibrarySkill(s.collection, s.category, s.name).catch(() => null);
  busy.value = null;
  note.value = res ? `Added "${s.name}" → ${res.placed}. Restart the agent to load it.` : (store.error ?? 'failed');
}

async function preview(s: LibrarySkill) {
  if (previewOf.value?.key === s.key) { previewOf.value = null; return; }
  previewOf.value = s;
  previewText.value = 'loading…';
  previewText.value = (await api.skillLibraryPreview(s.collection, s.category, s.name).catch((e) => ({ content: String(e) }))).content;
}
</script>

<template>
  <p class="muted" style="margin-top:0">
    <template v-if="collection === 'my'">Your own skills. “Add to this agent” copies the skill into the selected agent's volume.</template>
    <template v-else>Community skills (shared). Same one-click add into the selected agent.</template>
  </p>

  <div v-if="note" class="ok" style="margin-bottom:10px; font-size:12px">{{ note }}</div>

  <div v-for="s in skills" :key="s.key" class="row" :class="{ active: previewOf?.key === s.key }">
    <div class="meta clickable" @click="preview(s)">
      <div class="name">
        <span class="poke">▷</span>
        {{ s.name }}
        <span class="tag">{{ s.category }}</span>
        <span v-if="installedNames.has(s.name)" class="tag live">in agent</span>
        <span v-if="!s.valid" class="tag warn" :title="s.errors.join('; ')">invalid frontmatter</span>
      </div>
      <div class="desc">{{ s.description || '(no description)' }}</div>
    </div>
    <button
      class="sm"
      style="flex:none"
      :disabled="busy === s.key || !store.installation?.valid"
      :title="store.installation?.valid ? '' : 'select/set up an agent first'"
      @click="add(s)"
    >{{ busy === s.key ? 'Adding…' : 'Add to this agent' }}</button>
  </div>

  <pre v-if="previewOf" class="ingest-log" style="margin-top:10px; max-height:50vh; overflow:auto; white-space:pre-wrap">{{ previewText }}</pre>

  <p v-if="!skills.length" class="muted">
    <template v-if="collection === 'my'">No skills in your library yet. Add folders under <code>skill-library/my/&lt;category&gt;/&lt;skill&gt;/SKILL.md</code>.</template>
    <template v-else>No community skills yet.</template>
  </p>
</template>
