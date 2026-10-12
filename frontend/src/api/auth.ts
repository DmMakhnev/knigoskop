export interface User {
  id: string;
  email: string;
  role: 'user' | 'admin';
  status: 'active' | 'blocked';
  verified_at: string | null;
}
export interface UsersPage {
  items: User[];
  total: number;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
function csrfCookie(): string {
  const value = document.cookie
    .split('; ')
    .find((item) => item.startsWith('knigoskop_csrf='));
  return value
    ? decodeURIComponent(value.substring(value.indexOf('=') + 1))
    : '';
}
async function readResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      error.code ?? 'request_failed',
      error.message ?? 'Не удалось выполнить запрос',
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export async function request<T>(
  path: string,
  method = 'GET',
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== 'GET') {
    await readResponse(
      await fetch('/api/v1/auth/csrf', { credentials: 'same-origin' }),
    );
    headers['X-CSRF-Token'] = csrfCookie();
    if (!(body instanceof FormData))
      headers['Content-Type'] = 'application/json';
  }
  return readResponse<T>(
    await fetch(`/api/v1${path}`, {
      method,
      headers,
      credentials: 'same-origin',
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    }),
  );
}
export async function getMe(): Promise<User | null> {
  try {
    return await request<User>('/me');
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}
export function errorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : 'Нет связи с сервером. Попробуйте позже';
}
