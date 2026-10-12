import { AudioAdmin } from "../features/audio/AudioAdmin";
import { PromptAdmin } from "../features/summaries/PromptAdmin";
import { AIInfrastructure } from "../features/ai/AIInfrastructure";
import { messages as translations } from "../app/messages";
import { Button } from "@radix-ui/themes";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { errorMessage, request } from "../api/auth";
import type { User, UsersPage } from "../api/auth";

const t = translations.ru.auth;

export function AdminPage() {
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const queryClient = useQueryClient();
  const users = useQuery({
    queryKey: ["admin-users", offset],
    queryFn: () => request<UsersPage>(`/admin/users?offset=${offset}&limit=20`),
    retry: false,
  });
  async function changeStatus(user: User) {
    setBusy(true);
    setError("");
    try {
      await request(`/admin/users/${user.id}`, "PATCH", {
        status: user.status === "active" ? "blocked" : "active",
      });
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h1>{t.administration}</h1>
      <AIInfrastructure />
      <PromptAdmin />
      <AudioAdmin />
      <h2>{t.users}</h2>
      {users.isPending && <p role="status">{t.loadingUsers}</p>}
      {(users.isError || error) && (
        <p role="alert">{error || errorMessage(users.error)}</p>
      )}
      {users.isError && (
        <Button onClick={() => void users.refetch()}>{t.tryAgain}</Button>
      )}
      <ul className="user-list">
        {users.data?.items.map((user) => (
          <li key={user.id}>
            <div>
              <strong>{user.email}</strong>
              <p>
                {user.role === "admin" ? t.adminRole : t.userRole} ·{" "}
                {user.status === "active" ? t.active : t.blocked}
              </p>
            </div>
            {user.role !== "admin" && (
              <Button
                color={user.status === "active" ? "red" : "green"}
                disabled={busy}
                onClick={() => void changeStatus(user)}
              >
                {user.status === "active" ? t.block : t.unblock}
                <span className="sr-only"> {user.email}</span>
              </Button>
            )}
          </li>
        ))}
      </ul>
      <nav aria-label={t.userPagination} className="pagination">
        <Button
          disabled={offset === 0 || busy}
          onClick={() => setOffset(Math.max(0, offset - 20))}
        >
          {t.previous}
        </Button>
        <Button
          disabled={!users.data || offset + 20 >= users.data.total || busy}
          onClick={() => setOffset(offset + 20)}
        >
          {t.next}
        </Button>
      </nav>
    </section>
  );
}
