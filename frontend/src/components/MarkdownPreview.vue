<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { renderAfter, renderMarkdown } from '../md'

const props = withDefaults(defineProps<{ content: string; currentPath?: string }>(), {
  currentPath: 'template.md',
})

const root = ref<HTMLElement | null>(null)
let renderVersion = 0

async function render(): Promise<void> {
  const target = root.value
  if (!target) return
  const version = ++renderVersion
  target.innerHTML = renderMarkdown(props.content, props.currentPath).html
  await nextTick()
  if (version === renderVersion && root.value) await renderAfter(root.value)
}

watch(() => [props.content, props.currentPath], () => void render())
onMounted(() => void render())
</script>

<template>
  <div class="preview template-markdown-preview">
    <article ref="root" class="preview-inner" />
  </div>
</template>
