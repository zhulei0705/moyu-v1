<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import App from './App.vue'
import Knowledge from './Knowledge.vue'
import Observation from './Observation.vue'
import ServiceStatus from './ServiceStatus.vue'
const page=ref(window.location.hash)
const update=()=>page.value=window.location.hash
onMounted(()=>window.addEventListener('hashchange',update))
onUnmounted(()=>window.removeEventListener('hashchange',update))
</script>
<template><Knowledge v-if="['#/knowledge','#/flow','#/sources'].includes(page)" :page="page"/><Observation v-else-if="['#/observations','#/media','#/analysis','#/annotations','#/boss-samples','#/review','#/twins','#/evidence'].includes(page)" :page="page"/><App v-else :page="page"/><ServiceStatus/></template>
