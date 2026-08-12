<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Network } from 'lucide-vue-next'
import { api } from '../api'
import { openTab } from '../store'
import PanelHeader from './PanelHeader.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps<{ tabPath: string }>()
const emit = defineEmits<{ (e: 'notify', message: string, kind: 'info' | 'error'): void }>()

interface GraphNode {
  id: string
  label: string
}
interface GraphEdge {
  source: string
  target: string
}

const nodes = ref<GraphNode[]>([])
const edges = ref<GraphEdge[]>([])
const loading = ref(false)

const SIZE = { w: 380, h: 320, r: 118 }

/** 环形布局：中心节点 + 关联节点均匀环绕 */
const layout = computed(() => {
  const pos = new Map<string, { x: number; y: number }>()
  const center = props.tabPath
  const others = nodes.value.filter((n) => n.id !== center)
  pos.set(center, { x: SIZE.w / 2, y: SIZE.h / 2 })
  others.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / Math.max(1, others.length) - Math.PI / 2
    pos.set(n.id, {
      x: SIZE.w / 2 + SIZE.r * Math.cos(angle),
      y: SIZE.h / 2 + SIZE.r * Math.sin(angle),
    })
  })
  return pos
})

async function load(): Promise<void> {
  if (!props.tabPath) return
  loading.value = true
  try {
    const res = await api.graph(props.tabPath)
    nodes.value = res.nodes
    edges.value = res.edges
  } catch (e) {
    emit('notify', (e as Error).message, 'error')
    nodes.value = []
    edges.value = []
  } finally {
    loading.value = false
  }
}

function openNode(id: string): void {
  void openTab(id).catch((e) => emit('notify', (e as Error).message, 'error'))
}

function onSaved(e: Event): void {
  if ((e as CustomEvent<string>).detail === props.tabPath) void load()
}

watch(() => props.tabPath, () => void load(), { immediate: true })
onMounted(() => window.addEventListener('tab-saved', onSaved))
onBeforeUnmount(() => window.removeEventListener('tab-saved', onSaved))
</script>

<template>
  <div class="panel">
    <PanelHeader title="局部图谱" :count="nodes.length" />
    <div class="panel-body">
      <div class="graph-wrap">
        <EmptyState v-if="loading">加载中…</EmptyState>
        <EmptyState v-else-if="nodes.length === 0">
          <template #icon><Network :size="18" /></template>
          暂无关联笔记
        </EmptyState>
        <svg v-else :viewBox="`0 0 ${SIZE.w} ${SIZE.h}`" class="graph-svg">
          <line
            v-for="(e, i) in edges"
            :key="'e' + i"
            :x1="layout.get(e.source)?.x"
            :y1="layout.get(e.source)?.y"
            :x2="layout.get(e.target)?.x"
            :y2="layout.get(e.target)?.y"
            class="graph-edge"
          />
          <g v-for="n in nodes" :key="n.id" class="graph-node" @click="openNode(n.id)">
            <circle
              :cx="layout.get(n.id)?.x"
              :cy="layout.get(n.id)?.y"
              r="22"
              :class="{ center: n.id === tabPath }"
            />
            <text
              :x="layout.get(n.id)?.x"
              :y="layout.get(n.id)?.y"
              class="graph-label"
              text-anchor="middle"
              dominant-baseline="middle"
            >
              {{ n.label.length > 10 ? n.label.slice(0, 10) + '…' : n.label }}
            </text>
          </g>
        </svg>
        <div class="graph-hint">点击节点打开笔记；仅展示直接关联</div>
      </div>
    </div>
  </div>
</template>
