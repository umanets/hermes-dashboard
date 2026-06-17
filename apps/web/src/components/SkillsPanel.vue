<script setup lang="ts">
import { computed, ref } from 'vue';
import { useDashboard } from '../store';
import SkillPreview from './SkillPreview.vue';

const store = useDashboard();
const filter = ref('');
const expanded = ref<Set<string>>(new Set());
const selected = ref<string | null>(null);

// Collapsed by default; a group is open when explicitly expanded, or whenever a
// filter is active (so matches are always visible).
function isOpen(category: string): boolean {
  return filter.value.trim() !== '' || expanded.value.has(category);
}
function toggleOpen(category: string): void {
  const next = new Set(expanded.value);
  next.has(category) ? next.delete(category) : next.add(category);
  expanded.value = next;
}

const groups = computed(() => {
  const q = filter.value.toLowerCase();
  const map = new Map<string, typeof store.skills>();
  for (const s of store.skills) {
    if (q && !s.name.toLowerCase().includes(q) && !s.category.toLowerCase().includes(q)) continue;
    if (!map.has(s.category)) map.set(s.category, []);
    map.get(s.category)!.push(s);
  }
  return [...map.entries()]
    .map(([category, skills]) => ({
      category,
      skills,
      enabled: skills.filter((s) => s.enabled).length,
      total: skills.length,
    }))
    .sort((a, b) => a.category.localeCompare(b.category));
});

const totals = computed(() => ({
  enabled: store.skills.filter((s) => s.enabled).length,
  total: store.skills.length,
}));
</script>

<template>
  <div class="bar" style="margin-bottom:12px; gap:12px">
    <input v-model="filter" placeholder="Filter skills…" />
    <span class="muted" style="white-space:nowrap">{{ totals.enabled }} / {{ totals.total }} enabled</span>
    <button class="ghost sm" :disabled="store.loading" @click="store.bulkSkills(true)">Enable all</button>
    <button class="ghost sm" :disabled="store.loading" @click="store.bulkSkills(false)">Disable all</button>
  </div>

  <template v-for="g in groups" :key="g.category">
    <div class="group-head clickable" @click="toggleOpen(g.category)">
      <div style="display:flex; align-items:center; gap:8px">
        <span class="chevron" :class="{ open: isOpen(g.category) }">▶</span>
        <span class="label" style="text-transform:uppercase">{{ g.category }}</span>
        <span class="muted" style="font-size:12px">[ {{ g.enabled }} / {{ g.total }} ]</span>
      </div>
      <div style="display:flex; gap:6px" @click.stop>
        <button
          class="ghost sm"
          :disabled="store.loading || g.enabled === g.total"
          @click="store.bulkSkills(true, g.category)"
        >Enable group</button>
        <button
          class="ghost sm"
          :disabled="store.loading || g.enabled === 0"
          @click="store.bulkSkills(false, g.category)"
        >Disable group</button>
      </div>
    </div>

    <div
      v-show="isOpen(g.category)"
      v-for="s in g.skills"
      :key="s.name"
      class="row clickable"
      :class="{ active: selected === s.name }"
      @click="selected = s.name"
    >
      <div class="meta">
        <div class="name">
          <span class="poke">▷</span>
          {{ s.name }}
          <span v-if="!s.frontmatterValid" class="tag warn" :title="s.frontmatterErrors.join('; ')">invalid frontmatter</span>
        </div>
        <div class="desc">{{ s.frontmatter?.description ?? s.path }}</div>
      </div>
      <div class="toggle" :class="{ on: s.enabled }" @click.stop="store.toggleSkill(s.name, !s.enabled)" />
    </div>
  </template>

  <p v-if="!store.skills.length" class="muted">No skills found in this installation.</p>

  <SkillPreview v-if="selected" :name="selected" @close="selected = null" />
</template>
