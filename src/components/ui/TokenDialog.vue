<script setup lang="ts">
import { ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { setToken } from '../../authStore';
import { showTokenDialog } from '../../appUiState';
import { loadTeams } from '../../teamStore';
import { startRealtimeClient, stopRealtimeClient } from '../../realtime/wsClient';

const { t } = useI18n();
const token = ref('');
const error = ref('');
const pending = ref(false);

async function handleConfirm() {
  if (!token.value.trim()) {
    error.value = t('auth.tokenRequired');
    return;
  }

  pending.value = true;
  error.value = '';

  try {
    // 设置 token
    setToken(token.value.trim());

    // 验证 token 并加载团队数据
    await loadTeams();

    // 验证成功，关闭对话框并重连 WebSocket
    showTokenDialog.value = false;
    stopRealtimeClient();
    startRealtimeClient();
  } catch (err) {
    if (err instanceof Error && err.message === 'Auth required') {
      error.value = t('auth.tokenError');
    } else {
      error.value = t('error.requestFailedTitle');
    }
  } finally {
    pending.value = false;
  }
}

function handleClear() {
  token.value = '';
  error.value = '';
}
</script>

<template>
  <Teleport to="body">
    <div class="token-overlay">
      <section class="token-dialog panel">
        <div class="token-head">
          <p class="token-eyebrow">{{ t('auth.dialogEyebrow') }}</p>
          <h3>{{ t('auth.dialogTitle') }}</h3>
        </div>

        <p class="token-message">{{ t('auth.dialogMessage') }}</p>

        <div class="token-input-wrapper">
          <input
            v-model="token"
            type="text"
            class="token-input"
            :placeholder="t('auth.tokenPlaceholder')"
            :disabled="pending"
            @keyup.enter="handleConfirm"
          />
        </div>

        <p v-if="error" class="token-error">{{ error }}</p>

        <div class="token-actions">
          <button type="button" class="ghost-button" :disabled="pending" @click="handleClear">
            {{ t('common.reset') }}
          </button>
          <button type="button" class="secondary-button" :disabled="pending" @click="handleConfirm">
            {{ pending ? t('auth.verifying') : t('auth.confirm') }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.token-overlay {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(6, 10, 16, 0.52);
  backdrop-filter: blur(8px);
}

.token-dialog {
  width: min(420px, 100%);
  padding: 18px;
  display: grid;
  gap: 14px;
  border-radius: 18px;
  border: 1px solid color-mix(in srgb, var(--interactive-focus-border) 26%, var(--border-default) 74%);
  background:
    linear-gradient(
      180deg,
      color-mix(in srgb, var(--surface-panel) 95%, transparent) 0%,
      color-mix(in srgb, var(--surface-panel-muted) 92%, transparent) 100%
    );
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.34);
}

.token-head {
  display: grid;
  gap: 4px;
}

.token-eyebrow {
  margin: 0;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.token-head h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 1.12rem;
}

.token-message {
  margin: 0;
  color: var(--text-secondary);
  font-size: 0.9rem;
  line-height: 1.55;
}

.token-input-wrapper {
  display: grid;
}

.token-input {
  width: 100%;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-elevated);
  color: var(--text-primary);
  font-size: 0.9rem;
}

.token-input:focus {
  outline: none;
  border-color: var(--interactive-focus-border);
}

.token-input::placeholder {
  color: var(--text-tertiary);
}

.token-error {
  margin: 0;
  color: var(--state-danger);
  font-size: 0.85rem;
}

.token-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.token-actions > .ghost-button,
.token-actions > .secondary-button {
  min-width: 88px;
  height: 32px;
  padding: 0 14px;
  font-size: 0.84rem;
}
</style>