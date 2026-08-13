<template>
  <div class="empty-state">
    <span v-if="$slots.icon" class="empty-state-icon"><slot name="icon" /></span>
    <p class="empty-state-text"><slot /></p>
    <div v-if="$slots.actions" class="empty-state-actions"><slot name="actions" /></div>
  </div>
</template>
