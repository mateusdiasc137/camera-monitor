import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { AuthGate } from './features/auth/AuthGate'
import { CamerasPage } from './features/cameras/CamerasPage'
import { SystemStatus } from './features/system/SystemStatus'

const TABS = [
  { id: 'cameras', label: 'Câmeras' },
  { id: 'system', label: 'Sistema' },
] as const

type TabId = (typeof TABS)[number]['id']

export default function App() {
  const [queryClient] = useState(() => new QueryClient())
  const [tab, setTab] = useState<TabId>('cameras')

  return (
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        {(user) => (
          <>
            <nav className="tabs" aria-label="Seções">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  className={tab === t.id ? 'tab tab--active' : 'tab'}
                  aria-current={tab === t.id ? 'page' : undefined}
                  onClick={() => setTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </nav>
            {tab === 'cameras' && <CamerasPage isAdmin={user.role === 'admin'} />}
            {tab === 'system' && <SystemStatus />}
          </>
        )}
      </AuthGate>
    </QueryClientProvider>
  )
}
