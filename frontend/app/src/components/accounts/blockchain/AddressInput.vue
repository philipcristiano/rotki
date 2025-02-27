<script setup lang="ts">
import { trimOnPaste } from '@/utils/event';

const props = withDefaults(
  defineProps<{
    addresses: string[];
    disabled: boolean;
    multi: boolean;
    errorMessages?: string[];
  }>(),
  {
    errorMessages: () => []
  }
);

const emit = defineEmits<{
  (e: 'update:addresses', addresses: string[]): void;
}>();

const { t, tc } = useI18n();
const { errorMessages, addresses, disabled } = toRefs(props);
const address = ref('');
const userAddresses = ref('');
const multiple = ref(false);
const entries = computed(() => {
  const allAddresses = get(userAddresses)
    .split(',')
    .map(value => value.trim())
    .filter(entry => entry.length > 0);

  const entries: Record<string, string> = {};
  for (const address of allAddresses) {
    const lowerCase = address.toLocaleLowerCase();
    if (entries[lowerCase]) {
      continue;
    }
    entries[lowerCase] = address;
  }
  return Object.values(entries);
});

watch(multiple, () => {
  set(userAddresses, '');
});

const onPasteMulti = (event: ClipboardEvent) => {
  if (get(disabled)) return;
  const paste = trimOnPaste(event);
  if (paste) {
    userAddresses.value += paste.replace(/,(0x)/g, ',\n0x');
  }
};

const onPasteAddress = (event: ClipboardEvent) => {
  if (get(disabled)) return;
  const paste = trimOnPaste(event);
  if (paste) {
    set(address, paste);
  }
};

const updateAddresses = (addresses: string[]) => {
  emit('update:addresses', addresses);
};

watch(entries, addresses => updateAddresses(addresses));
watch(address, address => {
  updateAddresses(address ? [address.trim()] : []);
});

const setAddress = (addresses: string[]) => {
  if (addresses.length === 1) {
    set(address, addresses[0]);
  }
};

watch(addresses, addresses => setAddress(addresses));
onMounted(() => setAddress(get(addresses)));

const rules = [
  (v: string) => {
    return !!v || t('account_form.validation.address_non_empty').toString();
  }
];
</script>

<template>
  <v-row no-gutters class="mt-2">
    <v-col>
      <v-row v-if="multi" no-gutters align="center">
        <v-col cols="auto">
          <v-checkbox
            v-model="multiple"
            :disabled="disabled"
            :label="t('account_form.labels.multiple')"
          />
        </v-col>
      </v-row>
      <v-text-field
        v-if="!multiple"
        v-model="address"
        data-cy="account-address-field"
        outlined
        class="account-form__address"
        :label="t('common.account')"
        :rules="rules"
        :error-messages="errorMessages"
        autocomplete="off"
        :disabled="disabled"
        @paste="onPasteAddress"
      />
      <v-textarea
        v-else
        v-model="userAddresses"
        outlined
        :disabled="disabled"
        :error-messages="errorMessages"
        :hint="t('account_form.labels.addresses_hint')"
        :label="t('account_form.labels.addresses')"
        @paste="onPasteMulti"
      />
      <v-row v-if="multiple" no-gutters>
        <v-col>
          <div
            class="text-caption"
            v-text="
              tc('account_form.labels.addresses_entries', entries.length, {
                count: entries.length
              })
            "
          />
        </v-col>
      </v-row>
    </v-col>
  </v-row>
</template>
