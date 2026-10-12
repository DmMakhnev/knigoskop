import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { LibraryPage } from './LibraryPage';

const item = {
  id: 'one',
  title: 'Маленькая книга',
  author: 'Автор',
  edition_label: 'Первое издание',
  translator: '',
  format: 'txt',
  favorite: false,
  folder_id: null,
};
function response(data: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => data };
}
function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <LibraryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.cookie = 'knigoskop_csrf=; Max-Age=0; path=/';
});
function mockApi() {
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/csrf')) {
      document.cookie = `knigoskop_csrf=${'x'.repeat(43)}; path=/`;
      return response({});
    }
    if (url.endsWith('/folders'))
      return response({ items: [{ id: 'folder', name: 'Прочитать' }] });
    if (url.endsWith('/public')) return response({ items: [] });
    if (init?.method && init.method !== 'GET') return response(item);
    return response({ items: [item], total: 1 });
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

test('library uploads multipart with CSRF without forcing JSON content type', async () => {
  const mock = mockApi();
  mount();
  await screen.findByRole('heading', { name: 'Маленькая книга' });
  fireEvent.click(screen.getByText('Загрузить книгу'));
  fireEvent.change(screen.getByLabelText('Файл книги'), {
    target: {
      files: [new File(['My book'], 'book.txt', { type: 'text/plain' })],
    },
  });
  fireEvent.change(screen.getByLabelText('Название книги'), {
    target: { value: 'Моя книга' },
  });
  fireEvent.submit(
    screen.getByRole('button', { name: 'Импортировать' }).closest('form')!,
  );
  await screen.findByText('Книга добавлена в библиотеку');
  const call = mock.mock.calls.find(([url]) => url.endsWith('/upload'));
  expect(call?.[1]?.body).toBeInstanceOf(FormData);
  expect(call?.[1]?.headers).toHaveProperty('X-CSRF-Token', 'x'.repeat(43));
  expect(call?.[1]?.headers).not.toHaveProperty('Content-Type');
});

test('library favorites and folders persist mutations and filters change queries', async () => {
  const mock = mockApi();
  mount();
  await screen.findByRole('heading', { name: 'Маленькая книга' });
  fireEvent.click(screen.getByRole('button', { name: 'В избранное' }));
  await screen.findByText('Изменения сохранены');
  expect(mock.mock.calls.find(([url]) => url.endsWith('/one'))?.[1]?.body).toBe(
    '{"favorite":true}',
  );
  fireEvent.click(screen.getByLabelText('Только избранное'));
  await waitFor(() =>
    expect(mock.mock.calls.some(([url]) => url.includes('favorite=true'))).toBe(
      true,
    ),
  );
  fireEvent.change(screen.getByLabelText('Сортировка'), {
    target: { value: 'title' },
  });
  await waitFor(() =>
    expect(mock.mock.calls.some(([url]) => url.includes('sort=title'))).toBe(
      true,
    ),
  );
});

test('library deletion requires opening confirmation and sends DELETE', async () => {
  const mock = mockApi();
  mount();
  await screen.findByRole('heading', { name: 'Маленькая книга' });
  fireEvent.click(screen.getByText('Удалить из библиотеки'));
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить удаление' }));
  await screen.findByText('Книга удалена из вашей библиотеки');
  expect(
    mock.mock.calls.find(([url]) => url.endsWith('/one'))?.[1]?.method,
  ).toBe('DELETE');
});

test('library shows a failed request and allows retry', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => response({ message: 'Хранилище недоступно' }, 503)),
  );
  mount();
  expect((await screen.findAllByRole('alert')).length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: 'Повторить' })).toBeVisible();
});
