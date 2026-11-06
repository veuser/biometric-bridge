import type { ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { Button, Nav } from 'react-bootstrap'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { CurrentUser } from '../api/types'

const LINKS = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/devices', label: 'Devices' },
  { to: '/hrms', label: 'HRMS Configuration' },
  { to: '/sync', label: 'Synchronization' },
  { to: '/enrollment', label: 'Employee Registration' },
  { to: '/history', label: 'Sync History' },
  { to: '/settings', label: 'Settings' },
  { to: '/users', label: 'Users' },
  { to: '/logs', label: 'Logs & About' },
]

export default function Layout({ user, children }: { user: CurrentUser; children: ReactNode }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const logout = useMutation({
    mutationFn: () => api.post<void>('/api/auth/logout'),
    onSuccess: () => {
      queryClient.clear()
      navigate('/login', { replace: true })
    },
  })

  return (
    <div className="d-flex">
      <aside className="bb-sidebar bg-dark text-white d-flex flex-column p-3 flex-shrink-0">
        <div className="mb-4">
          <div className="fw-semibold">Biometric Bridge</div>
          <div className="small text-white-50">Attendance sync</div>
        </div>

        <Nav className="flex-column gap-1 flex-grow-1">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                'nav-link px-2 py-2 rounded ' +
                (isActive ? 'bg-primary text-white' : 'text-white-50')
              }
            >
              {link.label}
            </NavLink>
          ))}
        </Nav>

        <div className="border-top border-secondary pt-3 mt-3">
          <div className="small text-white-50 mb-2">
            Signed in as <span className="text-white">{user.username}</span>
          </div>
          <Button
            size="sm"
            variant="outline-light"
            className="w-100"
            onClick={() => logout.mutate()}
            disabled={logout.isPending}
          >
            Sign out
          </Button>
        </div>
      </aside>

      <main className="bb-content flex-grow-1 p-4">
        <div className="container-fluid" style={{ maxWidth: 1200 }}>
          {children}
        </div>
      </main>
    </div>
  )
}
