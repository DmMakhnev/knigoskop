import { messages as translations } from '../app/messages';
import { Button } from '@radix-ui/themes';
import { Navigate, Link } from 'react-router-dom';
import { useRef } from 'react';
import type { ReactNode } from 'react';
import { useSession } from '../features/auth/session';
import { errorMessage } from '../api/auth';

const t = translations.ru.auth;

export function Protected({
  children,
  admin = false,
  next = '/profile',
}: {
  children: ReactNode;
  admin?: boolean;
  next?: '/profile' | '/library';
}) {
  const session = useSession();
  const hadSession = useRef(false);
  if (session.data) hadSession.current = true;
  if (session.isPending) return <p role="status">{t.checkingSession}</p>;
  if (session.isError)
    return (
      <section>
        <p role="alert">{errorMessage(session.error)}</p>
        <Button onClick={() => void session.refetch()}>{t.tryAgain}</Button>
      </section>
    );
  if (!session.data && hadSession.current)
    return (
      <section>
        <p>{t.sessionEnded}</p>
        <Link to={`/login?next=${admin ? '/admin' : next}`}>{t.signIn}</Link>
      </section>
    );
  if (!session.data)
    return <Navigate to={`/login?next=${admin ? '/admin' : next}`} replace />;
  if (admin && session.data.role !== 'admin')
    return <p role="alert">{t.forbidden}</p>;
  return children;
}
