<script setup lang="ts">
import CardTitle from '@/components/typography/CardTitle.vue';
import { useAggregatedBalancesStore } from '@/store/balances/aggregated';
import { type AssetPriceInfo } from '@/types/prices';

const props = defineProps({
  identifier: { required: true, type: String },
  isCollectionParent: { required: false, type: Boolean, default: false }
});

const { identifier, isCollectionParent } = toRefs(props);
const { assetPriceInfo } = useAggregatedBalancesStore();

const info = computed<AssetPriceInfo>(() => {
  return get(assetPriceInfo(identifier, isCollectionParent));
});

const { t } = useI18n();
</script>
<template>
  <v-row>
    <v-col>
      <v-card>
        <v-card-title>
          <card-title>{{ t('common.price') }}</card-title>
        </v-card-title>
        <v-card-text class="text-end text-h5 font-weight-medium pt-4">
          <amount-display
            v-if="info.usdPrice && info.usdPrice.gte(0)"
            show-currency="symbol"
            :price-asset="identifier"
            :price-of-asset="info.usdPrice"
            fiat-currency="USD"
            :value="info.usdPrice"
          />
          <div v-else class="pt-3 d-flex justify-end">
            <v-skeleton-loader height="20" width="70" type="text" />
          </div>
        </v-card-text>
      </v-card>
    </v-col>
    <v-col>
      <v-card>
        <v-card-title>
          <card-title>{{ t('assets.amount') }}</card-title>
        </v-card-title>
        <v-card-text class="text-end text-h5 font-weight-medium pt-4">
          <amount-display :value="info.amount" :asset="identifier" />
        </v-card-text>
      </v-card>
    </v-col>
    <v-col>
      <v-card>
        <v-card-title>
          <card-title>{{ t('assets.value') }}</card-title>
        </v-card-title>
        <v-card-text class="text-end text-h5 font-weight-medium pt-4">
          <amount-display
            show-currency="symbol"
            :amount="info.amount"
            :price-asset="identifier"
            :price-of-asset="info.usdPrice"
            fiat-currency="USD"
            :value="info.usdValue"
          />
        </v-card-text>
      </v-card>
    </v-col>
  </v-row>
</template>
