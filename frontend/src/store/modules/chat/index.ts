import { useWebSocket } from '@vueuse/core';
import json5 from 'json5';

function resolveWsBaseURL() {
  try {
    const parsed = json5.parse(import.meta.env.VITE_OTHER_SERVICE_BASE_URL) as { ws?: string };
    const ws = parsed?.ws?.trim();
    if (ws) return ws.replace(/\/$/, '');
  } catch {
    // Ignore parse error and fallback below.
  }

  if (import.meta.env.DEV) {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    return `${protocol}://${window.location.hostname}:8081`;
  }

  return '';
}

export const useChatStore = defineStore(SetupStoreId.Chat, () => {
  const conversationId = ref<string>('');
  const input = ref<Api.Chat.Input>({ message: '' });

  const list = ref<Api.Chat.Message[]>([]);

  const authStore = useAuthStore();
  const wsBaseURL = resolveWsBaseURL();
  const wsUrl = computed(() => {
    const token = authStore.token || '';
    if (!token) return undefined;

    if (!wsBaseURL) return `/proxy-ws/chat/${token}`;
    const base = wsBaseURL === '/' ? '' : wsBaseURL;
    return `${base}/chat/${token}`;
  });

  const {
    status: wsStatus,
    data: wsData,
    send: wsSend,
    open: wsOpen,
    close: wsClose
  } = useWebSocket(wsUrl, {
    autoReconnect: true
  });

  const scrollToBottom = ref<null | (() => void)>(null);

  return {
    input,
    conversationId,
    list,
    wsStatus,
    wsData,
    wsSend,
    wsOpen,
    wsClose,
    scrollToBottom
  };
});
