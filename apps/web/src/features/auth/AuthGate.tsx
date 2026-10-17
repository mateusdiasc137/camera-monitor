import type { ReactNode } from 'react'
import { LoginForm } from './LoginForm'
import { useLogout, useSession } from './session'

/** Mostra o login enquanto não houver sessão; depois, a aplicação com o usuário no topo. */
export function AuthGate({ children }: { children: ReactNode }) {
  const session = useSession()
  const logout = useLogout()

  if (session.isPending) return <p className="content muted">Carregando…</p>
  if (session.isError) {
    return (
      <p role="alert" className="content error">
        Servidor indisponível: {session.error.message}
      </p>
    )
  }

  const user = session.data
  return (
    <>
      <header className="topbar">
        <h1>Monitor de câmeras</h1>
        {user && (
          <div className="topbar__user">
            <span>
              {user.username}
              {user.role === 'admin' && <span className="muted"> (admin)</span>}
            </span>
            <button type="button" className="secondary" onClick={() => logout.mutate()}>
              Sair
            </button>
          </div>
        )}
      </header>
      <main className="content">{user ? children : <LoginForm />}</main>
    </>
  )
}
