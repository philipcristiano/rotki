<script setup lang="ts">
import ActiveModules from '@/components/defi/ActiveModules.vue';
import ModuleNotActive from '@/components/defi/ModuleNotActive.vue';
import ProgressScreen from '@/components/helper/ProgressScreen.vue';
import NoPremiumPlaceholder from '@/components/premium/NoPremiumPlaceholder.vue';

import { Sushi } from '@/premium/premium';
import { useSushiswapStore } from '@/store/defi/sushiswap';
import { Module } from '@/types/modules';
import { Section } from '@/types/status';

const section = Section.DEFI_SUSHISWAP_BALANCES;
const secondSection = Section.DEFI_SUSHISWAP_EVENTS;
const modules: Module[] = [Module.SUSHISWAP];

const { fetchBalances, fetchEvents } = useSushiswapStore();
const { isModuleEnabled } = useModules();
const { shouldShowLoadingScreen, isSectionRefreshing } = useSectionLoading();
const premium = usePremium();
const { tc } = useI18n();

const primaryRefreshing = isSectionRefreshing(section);
const secondaryRefreshing = isSectionRefreshing(secondSection);
const loading = shouldShowLoadingScreen(section);
const isEnabled = isModuleEnabled(modules[0]);

onMounted(async () => {
  await Promise.all([fetchBalances(false), fetchEvents(false)]);
});
</script>

<template>
  <no-premium-placeholder v-if="!premium" :text="tc('sushiswap.premium')" />
  <module-not-active v-else-if="!isEnabled" :modules="modules" />
  <progress-screen v-else-if="loading">
    <template #message>
      {{ tc('sushiswap.loading') }}
    </template>
  </progress-screen>
  <div v-else>
    <sushi
      class="mt-4"
      :refreshing="primaryRefreshing || secondaryRefreshing"
      :secondary-loading="secondaryRefreshing"
    >
      <template #modules>
        <active-modules :modules="modules" />
      </template>
    </sushi>
  </div>
</template>
