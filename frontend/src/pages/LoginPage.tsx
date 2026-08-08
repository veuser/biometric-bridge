import { useState, type FormEvent } from 'react'
import { Alert, Button, Card, Form, Spinner } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { CurrentUser } from '../api/types'

/**
 * Doubles as first-run setup. The bridge ships with no credentials at all, so on a
 * fresh install this page asks the operator to create the administrator rather than
 * showing a sign-in form that no password could satisfy.
 */
export default function LoginPage() {
  const queryClient = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const { data: bootstrap } = useQuery({
    queryKey: ['bootstrap-status'],
    queryFn: () => api.get<{ bootstrap_required: boolean }>('/api/auth/bootstrap'),
  })
  const isSetup = bootstrap?.bootstrap_required === true

  const submit = useMutation({
    mutationFn: () =>
      api.post<CurrentUser>(isSetup ? '/api/auth/bootstrap' : '/api/auth/login', {
        username,
        password,
      }),
    onSuccess: () => queryClient.invalidateQueries(),
  })

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    submit.mutate()
  }

  return (
    <div className="d-flex align-items-center justify-content-center min-vh-100 p-3">
      <Card style={{ maxWidth: 420, width: '100%' }} className="shadow-sm">
        <Card.Body className="p-4">
          <h1 className="h4 mb-1">Biometric Bridge</h1>
          <p className="text-muted small">
            {isSetup
              ? 'No administrator exists yet. Create one to finish the installation.'
              : 'Sign in to manage attendance synchronization.'}
          </p>

          {submit.isError && <Alert variant="danger">{(submit.error as Error).message}</Alert>}

          <Form onSubmit={onSubmit}>
            <Form.Group className="mb-3">
              <Form.Label>Username</Form.Label>
              <Form.Control
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
              />
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Password</Form.Label>
              <Form.Control
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={isSetup ? 'new-password' : 'current-password'}
                required
              />
              {isSetup && (
                <Form.Text muted>At least 12 characters. A passphrase works well.</Form.Text>
              )}
            </Form.Group>
            <Button type="submit" className="w-100" disabled={submit.isPending}>
              {submit.isPending && <Spinner animation="border" size="sm" className="me-2" />}
              {isSetup ? 'Create administrator' : 'Sign in'}
            </Button>
          </Form>
        </Card.Body>
      </Card>
    </div>
  )
}
