<script setup lang="ts">
import ErrorScreen from '@/components/error/ErrorScreen.vue';

const { tc } = useI18n();
const { closeApp } = useInterop();
</script>

<template>
  <error-screen
    class="win-unsupported"
    :header="tc('win_unsupported.header')"
    :alternative="tc('win_unsupported.message')"
  >
    <v-btn depressed color="primary" @click="closeApp()">
      {{ tc('common.actions.terminate') }}
    </v-btn>
  </error-screen>
</template>

<style scoped lang="scss">
.win-unsupported {
  padding: 2rem 0;
  background-color: white;
}
</style>
