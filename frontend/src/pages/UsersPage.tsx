import { useState, type FormEvent } from 'react'
import { Alert, Button, Card, Col, Form, Modal, Row, Spinner, Table } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { User } from '../api/types'
import { BoolBadge, ErrorAlert, Loading, PageHeader, formatDateTime } from '../components/Ui'

export default function UsersPage() {
  const queryClient = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('ADMIN')
  const [passwordFor, setPasswordFor] = useState<User | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [notice, setNotice] = useState<string | null>(null)

  const users = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<User[]>('/api/users'),
  })

  const create = useMutation({
    mutationFn: () => api.post<User>('/api/users', { username, password, role }),
    onSuccess: () => {
      setShowCreate(false)
      setUsername('')
      setPassword('')
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })

  const changePassword = useMutation({
    mutationFn: () =>
      api.put<void>(`/api/users/${passwordFor!.id}/password`, { new_password: newPassword }),
    onSuccess: () => {
      setPasswordFor(null)
      setNewPassword('')
      setNotice(
        'Password changed. Any sessions opened with the old password have been signed out.',
      )
      queryClient.invalidateQueries({ queryKey: ['users'] })
    },
  })

  const setActive = useMutation({
    mutationFn: ({ user, active }: { user: User; active: boolean }) =>
      api.put<User>(`/api/users/${user.id}/active?is_active=${active}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  if (users.isLoading) return <Loading />

  return (
    <>
      <PageHeader
        title="Users"
        subtitle="Portal accounts"
        actions={<Button onClick={() => setShowCreate(true)}>Add user</Button>}
      />

      {notice && (
        <Alert variant="success" dismissible onClose={() => setNotice(null)}>
          {notice}
        </Alert>
      )}
      <ErrorAlert error={users.error} />
      <ErrorAlert error={setActive.error} />

      <Card className="shadow-sm">
        <Table responsive hover className="mb-0 align-middle">
          <thead className="table-light">
            <tr>
              <th>Username</th>
              <th>Role</th>
              <th>Active</th>
              <th>Last sign-in</th>
              <th>Locked until</th>
              <th className="text-end">Actions</th>
            </tr>
          </thead>
          <tbody>
            {(users.data ?? []).map((user) => (
              <tr key={user.id}>
                <td className="fw-semibold">{user.username}</td>
                <td>{user.role}</td>
                <td>
                  <BoolBadge value={user.is_active} on="Active" off="Disabled" />
                </td>
                <td className="small">{formatDateTime(user.last_login_at)}</td>
                <td className="small">{formatDateTime(user.locked_until)}</td>
                <td className="text-end">
                  <div className="d-inline-flex gap-2">
                    <Button
                      size="sm"
                      variant="outline-primary"
                      onClick={() => setPasswordFor(user)}
                    >
                      Change password
                    </Button>
                    <Button
                      size="sm"
                      variant="outline-secondary"
                      onClick={() => setActive.mutate({ user, active: !user.is_active })}
                    >
                      {user.is_active ? 'Disable' : 'Enable'}
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>

      <Modal show={showCreate} onHide={() => setShowCreate(false)} centered>
        <Form
          onSubmit={(event: FormEvent) => {
            event.preventDefault()
            create.mutate()
          }}
        >
          <Modal.Header closeButton>
            <Modal.Title className="h5">Add user</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <ErrorAlert error={create.error} />
            <Row className="g-3">
              <Col md={12}>
                <Form.Group>
                  <Form.Label>Username</Form.Label>
                  <Form.Control
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </Form.Group>
              </Col>
              <Col md={12}>
                <Form.Group>
                  <Form.Label>Password</Form.Label>
                  <Form.Control
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <Form.Text muted>A passphrase is easiest to remember.</Form.Text>
                </Form.Group>
              </Col>
              <Col md={12}>
                <Form.Group>
                  <Form.Label>Role</Form.Label>
                  <Form.Select value={role} onChange={(e) => setRole(e.target.value)}>
                    <option value="ADMIN">Administrator — full control</option>
                    <option value="VIEWER">Viewer — read only</option>
                  </Form.Select>
                </Form.Group>
              </Col>
            </Row>
          </Modal.Body>
          <Modal.Footer>
            <Button variant="outline-secondary" onClick={() => setShowCreate(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending && <Spinner animation="border" size="sm" className="me-2" />}
              Create
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>

      <Modal show={passwordFor !== null} onHide={() => setPasswordFor(null)} centered>
        <Form
          onSubmit={(event: FormEvent) => {
            event.preventDefault()
            changePassword.mutate()
          }}
        >
          <Modal.Header closeButton>
            <Modal.Title className="h5">Change password</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <ErrorAlert error={changePassword.error} />
            <p className="text-muted small">
              Changing the password for <strong>{passwordFor?.username}</strong> signs out every
              session opened with the old one — including your own if this is your account.
            </p>
            <Form.Group>
              <Form.Label>New password</Form.Label>
              <Form.Control
                type="password"
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </Form.Group>
          </Modal.Body>
          <Modal.Footer>
            <Button variant="outline-secondary" onClick={() => setPasswordFor(null)}>
              Cancel
            </Button>
            <Button type="submit" disabled={changePassword.isPending}>
              {changePassword.isPending && (
                <Spinner animation="border" size="sm" className="me-2" />
              )}
              Change password
            </Button>
          </Modal.Footer>
        </Form>
      </Modal>
    </>
  )
}
