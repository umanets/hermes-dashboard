<script setup lang="ts">
import { marked } from 'marked';
import { computed, ref, watch } from 'vue';
import { api, type SkillPreview } from '../api';

const props = defineProps<{ name: string }>();
const emit = defineEmits<{ close: [] }>();

const data = ref<SkillPreview | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

// SKILL.md content is operator-curated local markdown; we render it for preview.
const bodyHtml = computed(() => {
  if (!data.value) return '';
  const withoutFrontmatter = data.value.content.replace(/^﻿?---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');
  return marked.parse(withoutFrontmatter, { async: false }) as string;
});

async function load(name: string) {
  loading.value = true;
  error.value = null;
  data.value = null;
  try {
    data.value = await api.skillPreview(name);
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    loading.value = false;
  }
}

watch(() => props.name, (n) => load(n), { immediate: true });
</script>

<template>
  <div class="drawer-scrim" @click.self="emit('close')">
    <aside class="drawer">
      <span class="bracket tl" /><span class="bracket tr" />
      <span class="bracket bl" /><span class="bracket br" />

      <header class="drawer-head">
        <div class="drawer-kicker">// SKILL READOUT</div>
        <div class="drawer-title">{{ name }}</div>
        <div class="drawer-meta">
          <span class="tag">{{ data?.category ?? '…' }}</span>
          <span class="tag" :class="data?.enabled ? 'live' : 'off'">
            {{ data?.enabled ? 'ENABLED' : 'DISABLED' }}
          </span>
          <span v-if="data?.frontmatter?.version" class="tag">v{{ data.frontmatter.version }}</span>
          <span v-if="!data?.frontmatterValid && data" class="tag warn">FRONTMATTER ERR</span>
        </div>
        <button class="x" @click="emit('close')" aria-label="close">✕</button>
      </header>

      <div class="drawer-body">
        <div v-if="loading" class="muted scan">decrypting…</div>
        <div v-else-if="error" class="err">{{ error }}</div>
        <article v-else class="md" v-html="bodyHtml" />
      </div>

      <footer v-if="data" class="drawer-foot">
        <span class="muted">{{ data.path }}/SKILL.md</span>
        <span v-if="data.frontmatter?.author" class="muted">✶ {{ data.frontmatter.author }}</span>
      </footer>
    </aside>
  </div>
</template>
