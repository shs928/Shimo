<script setup lang="ts">
defineProps<{ title: string; count?: number | string }>()
</script>

<template>
  <div class="panel-header">
    <span class="panel-header-title">{{ title }}</span>
    <span v-if="count !== undefined && count !== ''" class="panel-header-count">{{ count }}</span>
    <div class="panel-header-actions">
      <slot name="actions" />
    </div>
  </div>
</template>
