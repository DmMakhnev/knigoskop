import { messages as translations } from '../app/messages';
import { Button } from '@radix-ui/themes';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { errorMessage } from '../api/auth';

const t = translations.ru.auth;

export function AuthForm({
  title,
  button,
  onSubmit,
  email = true,
  password = false,
  newPassword = false,
  children,
}: {
  title: string;
  button: string;
  onSubmit: (data: {
    email: string;
    password: string;
  }) => Promise<string | void>;
  email?: boolean;
  password?: boolean;
  newPassword?: boolean;
  children?: React.ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await onSubmit({
        email: String(data.get('email') ?? ''),
        password: String(data.get('password') ?? ''),
      });
      setMessage(result ?? t.done);
      form.reset();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="form-card">
      <h1>{title}</h1>
      <form onSubmit={(event) => void submit(event)} aria-busy={busy}>
        {email && (
          <label>
            Email
            <input
              name="email"
              type="email"
              autoComplete="email"
              required
              maxLength={320}
              disabled={busy}
            />
          </label>
        )}
        {password && (
          <label>
            {newPassword ? t.newPassword : t.password}
            <input
              name="password"
              type="password"
              autoComplete={newPassword ? 'new-password' : 'current-password'}
              required
              maxLength={128}
              disabled={busy}
            />
          </label>
        )}
        {newPassword && <p>{t.passwordHint}</p>}
        {error && <p role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
        <Button type="submit" disabled={busy}>
          {busy ? t.pleaseWait : button}
        </Button>
      </form>
      {children}
    </section>
  );
}
