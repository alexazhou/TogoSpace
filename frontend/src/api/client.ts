import { showGlobalRequestError, showTokenDialog } from '../appUiState';
import { getToken } from '../authStore';
import { t } from '../i18n';

// 运行时可写：桌面模式挂载前由 Tauri 注入后端地址，浏览器模式沿用编译期配置
let runtimeBaseUrl = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export function setApiBaseUrl(url: string): void {
  runtimeBaseUrl = (url || '').replace(/\/$/, '');
}

export function getApiBaseUrl(): string {
  return runtimeBaseUrl;
}

function makeUrl(path: string): string {
  return runtimeBaseUrl ? `${runtimeBaseUrl}${path}` : path;
}

function makeDisplayUrl(path: string): string {
  const target = makeUrl(path);
  try {
    return new URL(target, window.location.origin).toString();
  } catch {
    return target;
  }
}

export function withSearch(path: string, params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();

  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  }

  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

export function makeWsUrl(path: string): string {
  if (runtimeBaseUrl) {
    const url = new URL(runtimeBaseUrl);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = path;
    return url.toString();
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${path}`;
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const requestUrl = makeUrl(path);
  const displayUrl = makeDisplayUrl(path);
  const token = getToken();

  // 构建请求头，自动携带 token（豁免路径除外）
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };
  if (init?.headers) {
    // 合并传入的 headers
    const initHeaders = init.headers;
    if (initHeaders instanceof Headers) {
      initHeaders.forEach((value, key) => {
        (headers as Record<string, string>)[key] = value;
      });
    } else if (Array.isArray(initHeaders)) {
      for (const [key, value] of initHeaders) {
        (headers as Record<string, string>)[key] = value;
      }
    } else {
      Object.assign(headers, initHeaders);
    }
  }
  if (token && !isAuthExemptPath(path)) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  }

  try {
    const response = await fetch(requestUrl, {
      headers,
      ...init,
    });
    const responseContentType = response.headers.get('content-type') || '';

    if (!response.ok) {
      let errorDetail = '';
      let errorCode = '';

      try {
        if (responseContentType.includes('application/json')) {
          const errorBody = await response.json() as {
            error_desc?: unknown;
            error_code?: unknown;
            message?: unknown;
          };
          if (typeof errorBody.error_desc === 'string' && errorBody.error_desc.trim()) {
            errorDetail = errorBody.error_desc.trim();
          } else if (typeof errorBody.message === 'string' && errorBody.message.trim()) {
            errorDetail = errorBody.message.trim();
          } else if (typeof errorBody.error_code === 'string' && errorBody.error_code.trim()) {
            errorDetail = errorBody.error_code.trim();
          }
          if (typeof errorBody.error_code === 'string' && errorBody.error_code.trim()) {
            errorCode = errorBody.error_code.trim();
          }
        } else {
          const errorText = (await response.text()).trim();
          if (errorText) {
            errorDetail = errorText;
          }
        }
      } catch {
        errorDetail = '';
      }

      // 鉴权失败：触发 token 输入
      if (response.status === 401 && (errorCode === 'auth_required' || errorCode === 'auth_invalid')) {
        showTokenDialog.value = true;
        throw new Error('Auth required');
      }

      const isProxyConnectionFailure = errorCode === 'BACKEND_UNAVAILABLE'
        || response.headers.get('x-proxy-error') === 'backend-unavailable';

      if (isProxyConnectionFailure) {
        showGlobalRequestError({
          title: t('error.cannotConnectTitle'),
          path: displayUrl,
          statusCode: response.status,
          detail: t('error.cannotConnectDetail'),
        });
        throw new Error('Backend unavailable');
      }

      showGlobalRequestError({
        title: t('error.requestFailedTitle'),
        path: displayUrl,
        statusCode: response.status,
        detail: errorDetail,
      });
      throw new Error(
        errorDetail
          ? `Request failed: ${response.status} ${errorDetail}`
          : `Request failed: ${response.status}`,
      );
    }

    if (!responseContentType.includes('application/json')) {
      const errorDetail = t('error.invalidJsonDetail');
      showGlobalRequestError({
        title: t('error.requestFailedTitle'),
        path: displayUrl,
        statusCode: response.status,
        detail: errorDetail,
      });
      throw new Error(`Invalid JSON response: ${response.status}`);
    }

    return response.json() as Promise<T>;
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('Request failed:')) {
      throw error;
    }
    if (error instanceof Error && error.message === 'Auth required') {
      throw error;
    }
    if (error instanceof Error && error.message === 'Backend unavailable') {
      throw error;
    }
    if (error instanceof Error && error.message.startsWith('Invalid JSON response:')) {
      throw error;
    }

    showGlobalRequestError({
      title: t('error.cannotConnectTitle'),
      path: displayUrl,
      detail: t('error.cannotConnectDetail'),
    });
    throw error;
  }
}

function isAuthExemptPath(path: string): boolean {
  const exemptPaths = ['/system/status.json'];
  return exemptPaths.includes(path);
}
