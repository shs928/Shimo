<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ title?: string; disabled?: boolean; kind?: 'default' | 'danger' | 'accent' }>(),
  { kind: 'default' },
)

const cls = computed(() => (props.kind === 'default' ? undefined : `icon-btn--${props.kind}`))
</script>

<template>
  <button
    type="button"
    class="icon-btn"
    :class="cls"
    :title="title"
    :aria-label="title"
    :disabled="disabled"
  >
    <slot />
  </button>
</template>
