<script setup lang="ts">
import { computed } from 'vue';
import { getAgentAvatarUrl } from '../../avatar';

const props = withDefaults(defineProps<{
  title: string;
  subtitle: string;
  overline?: string;
  employeeNumber?: string;
  avatarName?: string;
  avatarSeed?: string;
  selected?: boolean;
  empty?: boolean;
  readonly?: boolean;
  variant?: 'template' | 'graph' | 'leader' | 'featured' | 'profile';
}>(), {
  employeeNumber: '',
  avatarName: '',
  avatarSeed: '',
  selected: false,
  empty: false,
  readonly: false,
  variant: 'template',
});

defineEmits<{
  click: [];
}>();

const avatarAlt = computed(() => `${props.avatarName || props.title} avatar`);
const avatarLookupKey = computed(() => props.avatarSeed || props.avatarName || props.title);
const normalizedEmployeeNumber = computed(() => (
  /^\d+$/.test(props.employeeNumber) ? props.employeeNumber : ''
));
const normalizedOverline = computed(() => props.overline?.trim() || '');
</script>

<template>
  <button
    class="entity-card"
    :class="[
      `entity-card--${variant}`,
      {
        selected,
        'is-empty': empty,
        'is-readonly': readonly,
        'has-overline': !!normalizedOverline,
        'has-badge': !!normalizedEmployeeNumber,
      },
    ]"
    type="button"
    @click="$emit('click')"
  >
    <small v-if="normalizedEmployeeNumber && !empty" class="entity-card__badge">
      #{{ normalizedEmployeeNumber }}
    </small>
    <small v-if="normalizedOverline && !empty" class="entity-card__overline" :title="normalizedOverline">
      {{ normalizedOverline }}
    </small>
    <img
      v-if="avatarName && !empty"
      class="entity-card__avatar"
      :src="getAgentAvatarUrl(avatarLookupKey)"
      :alt="avatarAlt"
    />
    <strong class="entity-card__title" :title="title">{{ title }}</strong>
    <small class="entity-card__subtitle" :title="subtitle">{{ subtitle }}</small>
  </button>
</template>

<style scoped>
.entity-card {
  width: var(--entity-card-width);
  aspect-ratio: 3 / 4;
  --entity-card-height: calc(var(--entity-card-width) * 4 / 3);
  --entity-avatar-size: calc(var(--entity-card-width) * var(--entity-avatar-size-ratio, 0.46));
  --entity-avatar-top: calc(var(--entity-card-height) * var(--entity-avatar-top-ratio, 0.209));
  --entity-overline-top: calc(var(--entity-card-height) * var(--entity-overline-top-ratio, 0.094));
  --entity-title-top: calc(var(--entity-card-height) * var(--entity-title-top-ratio, 0.615));
  --entity-subtitle-top: calc(var(--entity-card-height) * var(--entity-subtitle-top-ratio, 0.772));
  --entity-badge-top: calc(var(--entity-card-width) * var(--entity-badge-offset-ratio, 0.078));
  --entity-badge-left: calc(var(--entity-card-width) * var(--entity-badge-offset-ratio, 0.078));
  --entity-overline-clearance: calc(var(--entity-badge-size) * var(--entity-overline-clearance-ratio, 1.5));
  box-sizing: border-box;
  position: relative;
  border: 1px solid var(--team-create-node-border);
  border-radius: var(--entity-card-radius);
  background: var(--surface-soft);
  color: var(--text-strong);
  padding: var(--entity-card-padding-y) var(--entity-card-padding-x);
  text-align: center;
  cursor: pointer;
  box-shadow: none;
  transition:
    transform 0.18s ease,
    border-color 0.18s ease,
    background 0.18s ease,
    box-shadow 0.18s ease;
}

.entity-card:hover {
  transform: translateY(-2px);
  border-color: var(--focus-border);
  background: var(--selected);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--focus-border) 55%, transparent);
}

.entity-card.selected {
  border-color: var(--focus-border);
  background: var(--selected);
  box-shadow: inset 0 0 0 1px var(--focus-border);
}

.entity-card.is-readonly {
  cursor: grab;
}

.entity-card.is-empty {
  color: var(--muted);
  cursor: default;
  background: color-mix(in srgb, var(--surface-soft) 92%, var(--selected) 8%);
  border: 1px dashed color-mix(in srgb, var(--panel-border-strong) 88%, var(--focus-border) 12%);
  box-shadow: none;
}

.entity-card.is-empty:hover {
  transform: none;
  border-color: color-mix(in srgb, var(--panel-border-strong) 88%, var(--focus-border) 12%);
  background: color-mix(in srgb, var(--surface-soft) 92%, var(--selected) 8%);
  box-shadow: none;
}

.entity-card__avatar {
  position: absolute;
  top: var(--entity-avatar-top);
  left: 50%;
  width: var(--entity-avatar-size);
  aspect-ratio: 1 / 1;
  height: auto;
  transform: translateX(-50%);
  border-radius: var(--entity-avatar-radius);
  display: block;
  object-fit: cover;
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--panel-border-strong) 30%, transparent);
}

.entity-card__title,
.entity-card__subtitle,
.entity-card__overline {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.entity-card__overline {
  position: absolute;
  top: var(--entity-overline-top);
  left: 50%;
  width: calc(100% - (2 * var(--entity-card-padding-x)));
  transform: translateX(-50%);
  color: color-mix(in srgb, var(--accent) 64%, var(--text-strong) 36%);
  font-size: var(--entity-overline-size);
  line-height: 1.15;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.entity-card.has-overline.has-badge .entity-card__overline {
  width: calc(100% - (2 * var(--entity-card-padding-x)) - var(--entity-overline-clearance));
}

/* graph variant stacks badge above overline, no horizontal clearance needed */
.entity-card--graph.has-overline.has-badge .entity-card__overline {
  width: calc(100% - (2 * var(--entity-card-padding-x)));
}

.entity-card__title {
  position: absolute;
  top: var(--entity-title-top);
  left: 50%;
  width: calc(100% - (2 * var(--entity-card-padding-x)));
  transform: translateX(-50%);
  font-size: var(--entity-title-size);
  line-height: 1.2;
  font-weight: 600;
  min-height: var(--entity-title-block-height);
}

.entity-card__subtitle {
  position: absolute;
  top: var(--entity-subtitle-top);
  left: 50%;
  width: calc(100% - (2 * var(--entity-card-padding-x)));
  transform: translateX(-50%);
  color: var(--muted);
  font-size: var(--entity-subtitle-size);
  line-height: 1.2;
  min-height: var(--entity-subtitle-block-height);
}

.entity-card__badge {
  position: absolute;
  top: var(--entity-badge-top);
  left: var(--entity-badge-left);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  max-width: calc(100% - (2 * var(--entity-badge-left)));
  padding: 0;
  color: color-mix(in srgb, var(--muted) 78%, transparent);
  font-size: var(--entity-badge-size);
  line-height: 1;
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  letter-spacing: 0.04em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.entity-card.is-empty .entity-card__title {
  color: color-mix(in srgb, var(--text-strong) 58%, var(--muted) 42%);
}

.entity-card--template {
  --entity-card-width: 78px;
  --entity-card-radius: 12px;
  --entity-card-padding-y: 7px;
  --entity-card-padding-x: 5px;
  --entity-avatar-size-ratio: 0.41;
  --entity-avatar-top-ratio: 0.106;
  --entity-avatar-radius: 9px;
  --entity-overline-size: 0.68rem;
  --entity-overline-top-ratio: 0.115;
  --entity-title-top-ratio: 0.558;
  --entity-subtitle-top-ratio: 0.765;
  --entity-title-size: 0.68rem;
  --entity-subtitle-size: 0.6rem;
  --entity-title-block-height: 1.8em;
  --entity-subtitle-block-height: 1.6em;
  --entity-badge-size: 0.7rem;
  --entity-badge-offset-ratio: 0.077;
}

.entity-card--graph {
  --entity-card-width: var(--member-card-width, 102px);
  --entity-card-radius: 14px;
  --entity-card-padding-y: 6px;
  --entity-card-padding-x: 7px;
  --entity-avatar-radius: 24%;
  --entity-overline-size: 0.8rem;
  --entity-overline-top-ratio: 0.149;
  --entity-avatar-top-ratio: 0.268;
  --entity-title-size: 0.8rem;
  --entity-title-top-ratio: 0.628;
  --entity-subtitle-size: 0.64rem;
  --entity-subtitle-top-ratio: 0.785;
  --entity-title-block-height: 2.2em;
  --entity-subtitle-block-height: 1.35em;
  --entity-badge-size: 0.8rem;
  --entity-badge-offset-ratio: 0.058;
  --entity-overline-clearance-ratio: 1.45;
}

.entity-card--leader {
  --entity-card-width: 132px;
  --entity-card-radius: 20px;
  --entity-card-padding-y: 8px;
  --entity-card-padding-x: 7px;
  --entity-avatar-radius: 24%;
  --entity-overline-size: 0.84rem;
  --entity-title-size: 0.84rem;
  --entity-subtitle-size: 0.68rem;
  --entity-title-block-height: 2.2em;
  --entity-subtitle-block-height: 1.35em;
  --entity-badge-size: 0.84rem;
  --entity-badge-offset-ratio: 0.05;
  --entity-overline-clearance-ratio: 1.3;
}

.entity-card--featured {
  --entity-card-width: 117px;
  --entity-card-radius: 18px;
  --entity-card-padding-y: 11px;
  --entity-card-padding-x: 9px;
  --entity-avatar-size-ratio: 0.41;
  --entity-avatar-top-ratio: 0.109;
  --entity-avatar-radius: 12px;
  --entity-overline-size: 0.82rem;
  --entity-overline-top-ratio: 0.09;
  --entity-meta-top-ratio: 0.57;
  --entity-meta-gap-ratio: 0.051;
  --entity-title-size: 0.82rem;
  --entity-subtitle-size: 0.68rem;
  --entity-title-block-height: 2.1em;
  --entity-subtitle-block-height: 1.7em;
  --entity-badge-size: 0.84rem;
  --entity-badge-offset-ratio: 0.068;
  --entity-overline-clearance-ratio: 1.4;
}

.entity-card--profile {
  --entity-card-width: 132px;
  --entity-card-radius: 20px;
  --entity-card-padding-y: 8px;
  --entity-card-padding-x: 8px;
  --entity-avatar-radius: 24%;
  --entity-overline-size: 0.8rem;
  --entity-avatar-size-ratio: 0.42;
  --entity-overline-top-ratio: 0.134;
  --entity-avatar-top-ratio: 0.312;
  --entity-title-top-ratio: 0.719;
  --entity-subtitle-top-ratio: 0.852;
  --entity-title-size: 0.84rem;
  --entity-subtitle-size: 0.68rem;
  --entity-title-block-height: 2.2em;
  --entity-subtitle-block-height: 1.35em;
  --entity-badge-size: 0.84rem;
  --entity-badge-offset-ratio: 0.05;
  --entity-overline-clearance-ratio: 1.3;
}
</style>
