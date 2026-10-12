import { messages as translations } from '../app/messages';
import { Button } from '@radix-ui/themes';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { request, errorMessage } from '../api/auth';
import { useSession, replaceSession } from '../features/auth/session';

const t = translations.ru.auth;

export function ProfilePage({
  onAccountDeleted,
  onLoggedOut,
}: {
  onAccountDeleted: () => void;
  onLoggedOut: () => void;
}) {
  const session = useSession();
  const queryClient = useQueryClient();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function endSession() {
    setBusy(true);
    setError('');
    try {
      await request('/auth/logout', 'POST');
      await replaceSession(queryClient, null);
      onLoggedOut();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  async function removeAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError('');
    try {
      await request('/me', 'DELETE', {
        password: form.get('password'),
        confirmation: form.get('confirmation'),
      });
      await replaceSession(queryClient, null);
      // The app owns this navigation even if the guard has unmounted the profile.
      onAccountDeleted();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="form-card">
      <h1>{t.profile}</h1>
      <p>{session.data?.email}</p>
      {session.data?.role === 'admin' && (
        <p>
          <Link to="/admin">{t.administration}</Link>
        </p>
      )}
      <Button onClick={() => void endSession()} disabled={busy}>
        {t.logout}
      </Button>
      {error && <p role="alert">{error}</p>}
      <details>
        <summary>{t.deleteAccount}</summary>
        <p>
          Аккаунт и действующие сессии будут удалены. Это действие нельзя
          отменить.
        </p>
        <form onSubmit={(event) => void removeAccount(event)}>
          <label>
            {t.currentPassword}
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              required
              maxLength={128}
              disabled={busy}
            />
          </label>
          <label>
            {t.deleteConfirmation}
            <input
              name="confirmation"
              required
              pattern="DELETE"
              disabled={busy}
            />
          </label>
          <Button type="submit" color="red" disabled={busy}>
            {t.deleteMyAccount}
          </Button>
        </form>
      </details>
    </section>
  );
}
