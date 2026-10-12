import { StrictMode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { Protected } from '../../components/Protected';
import { LoginPage, RegisterPage, TokenPage } from '../../pages/AuthPages';
import { App } from '../../app/App';
import { AdminPage } from '../../pages/AdminPage';

function response(data: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => data };
}
function mount(content: React.ReactNode, path = '/') {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[path]}>{content}</MemoryRouter>
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.cookie = 'knigoskop_csrf=; Max-Age=0; path=/';
  window.history.replaceState(null, '', '/');
});
function csrf() {
  document.cookie = `knigoskop_csrf=${'x'.repeat(43)}; path=/`;
  return response({ message: 'ready' });
}

test('registration sends credentials with CSRF and shows email instructions', async () => {
  const fetchMock = vi.fn((url: string) =>
    Promise.resolve(
      url.endsWith('/csrf')
        ? csrf()
        : response({ message: 'Проверьте почту' }, 202),
    ),
  );
  vi.stubGlobal('fetch', fetchMock);
  mount(<RegisterPage />);
  fireEvent.change(screen.getByLabelText('Email'), {
    target: { value: 'reader@example.com' },
  });
  fireEvent.change(screen.getByLabelText('Новый пароль'), {
    target: { value: 'Correct horse battery 42' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Зарегистрироваться' }));
  expect(await screen.findByText('Проверьте почту')).toBeVisible();
  const call = fetchMock.mock.calls.find(([url]) =>
    url.endsWith('/register'),
  ) as unknown as [string, RequestInit];
  expect(call[1].method).toBe('POST');
  expect(call[1].credentials).toBe('same-origin');
  expect(call[1].headers).toHaveProperty('X-CSRF-Token', 'x'.repeat(43));
  expect(JSON.parse(String(call[1].body))).toEqual({
    email: 'reader@example.com',
    password: 'Correct horse battery 42',
  });
});

test('login shows server validation failure', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) =>
      Promise.resolve(
        url.endsWith('/csrf')
          ? csrf()
          : response(
              {
                code: 'invalid_credentials',
                message: 'Неверный email или пароль',
              },
              401,
            ),
      ),
    ),
  );
  mount(<LoginPage />);
  fireEvent.change(screen.getByLabelText('Email'), {
    target: { value: 'reader@example.com' },
  });
  fireEvent.change(screen.getByLabelText('Пароль'), {
    target: { value: 'incorrect' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Неверный email или пароль',
  );
});

test('guest admin route redirects to login', async () => {
  vi.stubGlobal(
    'fetch',
    vi
      .fn()
      .mockResolvedValue(
        response({ code: 'unauthenticated', message: 'Войдите' }, 401),
      ),
  );
  mount(
    <Routes>
      <Route
        path="/admin"
        element={
          <Protected admin>
            <AdminPage />
          </Protected>
        }
      />
      <Route path="/login" element={<h1>Вход</h1>} />
    </Routes>,
    '/admin',
  );
  expect(await screen.findByRole('heading', { name: 'Вход' })).toBeVisible();
});

test('regular user cannot mount admin data queries', async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    response({
      id: 'reader',
      email: 'reader@example.com',
      role: 'user',
      status: 'active',
      verified_at: 'now',
    }),
  );
  vi.stubGlobal('fetch', fetchMock);
  mount(
    <Protected admin>
      <AdminPage />
    </Protected>,
  );
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Недостаточно прав',
  );
  expect(fetchMock.mock.calls.every(([url]) => url === '/api/v1/me')).toBe(
    true,
  );
});

test('administrator can block a listed user', async () => {
  const fetchMock = vi.fn((url: string) =>
    Promise.resolve(
      url.endsWith('/csrf')
        ? csrf()
        : url === '/api/v1/me'
          ? response({
              id: 'admin',
              email: 'admin@example.com',
              role: 'admin',
              status: 'active',
              verified_at: 'now',
            })
          : url.includes('/admin/prompts')
            ? response({ items: [], total: 0 })
            : url.includes('?offset=')
              ? response({
                  items: [
                    {
                      id: 'reader',
                      email: 'reader@example.com',
                      role: 'user',
                      status: 'active',
                      verified_at: 'now',
                    },
                  ],
                  total: 1,
                })
              : response({ id: 'reader', status: 'blocked' }),
    ),
  );
  vi.stubGlobal('fetch', fetchMock);
  mount(
    <Protected admin>
      <AdminPage />
    </Protected>,
  );
  fireEvent.click(
    await screen.findByRole('button', {
      name: 'Заблокировать reader@example.com',
    }),
  );
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([url]) => url === '/api/v1/admin/users/reader',
      ),
    ).toBe(true),
  );
  const call = fetchMock.mock.calls.find(
    ([url]) => url === '/api/v1/admin/users/reader',
  ) as unknown as [string, RequestInit];
  expect(call[1].method).toBe('PATCH');
  expect(JSON.parse(String(call[1].body))).toEqual({ status: 'blocked' });
});

test('verification fragment survives StrictMode and is removed from the URL', async () => {
  const token = 't'.repeat(43);
  window.history.replaceState(null, '', `/verify-email#token=${token}`);
  const fetchMock = vi.fn((url: string) =>
    Promise.resolve(
      url.endsWith('/csrf')
        ? csrf()
        : response({ message: 'Email подтверждён' }),
    ),
  );
  vi.stubGlobal('fetch', fetchMock);
  mount(
    <StrictMode>
      <TokenPage />
    </StrictMode>,
  );
  expect(window.location.hash).toBe('');
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить email' }));
  expect(await screen.findByText('Email подтверждён')).toBeVisible();
  const call = fetchMock.mock.calls.find(([url]) =>
    url.endsWith('/verify-email'),
  ) as unknown as [string, RequestInit];
  expect(JSON.parse(String(call[1].body))).toEqual({ token });
});

test('navigation observes the same session after login and logout', async () => {
  let authenticated = false;
  const user = {
    id: 'user',
    email: 'reader@example.com',
    role: 'user',
    status: 'active',
    verified_at: '2026-01-01',
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url.endsWith('/csrf')) return csrf();
      if (url.endsWith('/login')) {
        authenticated = true;
        return response(user);
      }
      if (url.endsWith('/logout')) {
        authenticated = false;
        return response(undefined, 204);
      }
      if (url.endsWith('/me'))
        return authenticated ? response(user) : response({}, 401);
      return response({ items: [], total: 0 });
    }),
  );
  mount(<App />, '/login');
  fireEvent.change(screen.getByLabelText('Email'), {
    target: { value: user.email },
  });
  fireEvent.change(screen.getByLabelText('Пароль'), {
    target: { value: 'Correct horse battery 42' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
  await screen.findByRole('heading', { name: 'Мой профиль' });
  expect(
    await screen.findByRole('link', { name: 'Моя библиотека' }),
  ).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Выйти' }));
  await screen.findByRole('heading', { name: 'Вход' });
  expect(screen.queryByRole('link', { name: 'Моя библиотека' })).toBeNull();
  expect(screen.getByRole('link', { name: 'Войти' })).toBeVisible();
});

test('deleting the account lands on the guest home without a guard redirect', async () => {
  let authenticated = true;
  const user = {
    id: 'user',
    email: 'reader@example.com',
    role: 'user',
    status: 'active',
    verified_at: '2026-01-01',
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/csrf')) return csrf();
      if (url.endsWith('/me') && init?.method === 'DELETE') {
        authenticated = false;
        return response(undefined, 204);
      }
      if (url.endsWith('/me'))
        return authenticated ? response(user) : response({}, 401);
      if (url.endsWith('/health'))
        return response({ status: 'ok', service: 'knigoskop-api' });
      return response({ items: [], total: 0 });
    }),
  );
  mount(<App />, '/profile');
  await screen.findByRole('heading', { name: 'Мой профиль' });
  fireEvent.click(screen.getByText('Удалить аккаунт', { exact: true }));
  fireEvent.change(screen.getByLabelText('Текущий пароль'), {
    target: { value: 'Correct horse battery 42' },
  });
  fireEvent.change(screen.getByLabelText('Введите DELETE для подтверждения'), {
    target: { value: 'DELETE' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Удалить мой аккаунт' }));
  await screen.findByRole('heading', { name: 'Книгоскоп' });
  expect(screen.queryByRole('heading', { name: 'Вход' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Моя библиотека' })).toBeNull();
});
