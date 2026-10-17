import { useState, type FormEvent } from 'react'
import { useLogin } from './session'

export function LoginForm() {
  const login = useLogin()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    login.mutate({ username, password })
  }

  return (
    <form className="card login" onSubmit={submit} aria-labelledby="login-title">
      <h2 id="login-title">Entrar</h2>
      <label>
        Usuário
        <input
          name="username"
          autoComplete="username"
          autoCapitalize="none"
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </label>
      <label>
        Senha
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {login.isError && (
        <p role="alert" className="error">
          {login.error.message}
        </p>
      )}
      <button type="submit" disabled={login.isPending}>
        {login.isPending ? 'Entrando…' : 'Entrar'}
      </button>
    </form>
  )
}
