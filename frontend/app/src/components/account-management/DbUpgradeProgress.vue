<script setup lang="ts">
import { type CurrentDbUpgradeProgress } from '@/types/login';

const props = defineProps<{
  progress: CurrentDbUpgradeProgress | null;
}>();

const { progress } = toRefs(props);

const { tc } = useI18n();

const multipleUpgrades = computed(() => {
  if (isDefined(progress)) {
    const { toVersion, fromVersion } = get(progress);
    return toVersion - fromVersion > 1;
  }
  return false;
});
</script>

<template>
  <v-alert v-if="progress" type="warning" text>
    <template #prepend>
      <div class="mr-4">
        <v-progress-circular
          rounded
          :value="progress.percentage"
          size="45"
          width="4"
          color="warning"
        >
          <div v-if="multipleUpgrades">
            <v-progress-circular
              :value="progress.totalPercentage"
              color="warning"
            />
          </div>
        </v-progress-circular>
      </div>
    </template>

    <div>
      <div>
        {{ tc('login.upgrading_db.warning', 0, progress) }}
      </div>
      <v-divider class="my-2" />
      <div>
        {{ tc('login.upgrading_db.current', 0, progress) }}
      </div>
    </div>
  </v-alert>
</template>
