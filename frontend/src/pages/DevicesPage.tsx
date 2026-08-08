import { useState, type FormEvent } from 'react'
import { Alert, Button, Card, Col, Form, Modal, Row, Spinner, Table } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Device, DeviceTestResult } from '../api/types'
import {
  BoolBadge,
  ConfirmDialog,
  ErrorAlert,
  Loading,
  PageHeader,
  formatDateTime,
} from '../components/Ui'

type FormState = {
  name: string
  ip_address: string
  port: number
  location: string
  serial_number: string
  role: Device['role']
  timeout_seconds: number
  force_udp: boolean
  timezone: string
  enabled: boolean
  comm_key: string
}

const BLANK: FormState = {
  name: '',
  ip_address: '',
  port: 4370,
  location: '',
  serial_number: '',
  role: 'CHECK_IN',
  timeout_seconds: 10,
  force_udp: false,
  timezone: '',
  enabled: true,
  comm_key: '',
}

/** Empty strings mean "not set"; sending them would fail validation. */
function toPayload(form: FormState) {
  return {
    ...form,
    location: form.location || null,
    serial_number: form.serial_number || null,
    timezone: form.timezone || null,
    // Omitted rather than empty: an empty key must not wipe the stored one.
    comm_key: form.comm_key || null,
  }
}

export default function DevicesPage() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<Device | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>(BLANK)
  const [testResult, setTestResult] = useState<DeviceTestResult | null>(null)
  const [deleting, setDeleting] = useState<Device | null>(null)

  const devices = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.get<Device[]>('/api/devices'),
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['devices'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    queryClient.invalidateQueries({ queryKey: ['sync-status'] })
  }

  const save = useMutation({
    mutationFn: () =>
      editing
        ? api.put<Device>(`/api/devices/${editing.id}`, toPayload(form))
        : api.post<Device>('/api/devices', toPayload(form)),
    onSuccess: () => {
      refresh()
      setShowForm(false)
    },
  })

  const remove = useMutation({
    mutationFn: (device: Device) => api.del<void>(`/api/devices/${device.id}`),
    onSuccess: () => {
      refresh()
      setDeleting(null)
    },
  })

  const toggle = useMutation({
    mutationFn: ({ device, enabled }: { device: Device; enabled: boolean }) =>
      api.put<Device>(`/api/devices/${device.id}/enabled?enabled=${enabled}`),
    onSuccess: refresh,
  })

  /** Probes the values currently in the form, before anything is saved. */
  const test = useMutation({
    mutationFn: () =>
      api.post<DeviceTestResult>('/api/devices/test-connection', {
        ip_address: form.ip_address,
        port: form.port,
        timeout_seconds: form.timeout_seconds,
        comm_key: form.comm_key || null,
        force_udp: form.force_udp,
        name: form.name || 'device',
      }),
    onSuccess: setTestResult,
  })

  const testSaved = useMutation({
    mutationFn: (device: Device) =>
      api.post<DeviceTestResult>(`/api/devices/${device.id}/test-connection`),
    onSuccess: setTestResult,
  })

  function openCreate() {
    setEditing(null)
    setForm(BLANK)
    setTestResult(null)
    setShowForm(true)
  }

  function openEdit(device: Device) {
    setEditing(device)
    setForm({
      name: device.name,
      ip_address: device.ip_address,
      port: device.port,
      location: device.location ?? '',
      serial_number: device.serial_number ?? '',
      role: device.role,
      timeout_seconds: device.timeout_seconds,
      force_udp: device.force_udp,
      timezone: device.timezone ?? '',
      enabled: device.enabled,
      // Never pre-filled: the API does not return it, by design.
      comm_key: '',
    })
    setTestResult(null)
    setShowForm(true)
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    save.mutate()
  }

  if (devices.isLoading) return <Loading />

  return (
    <>
      <PageHeader
        title="Devices"
        subtitle="Biometric terminals this bridge reads from"
        actions={<Button onClick={openCreate}>Add device</Button>}
      />

      <ErrorAlert error={devices.error} />
      {testSaved.isError && <ErrorAlert error={testSaved.error} />}

      {testResult && !showForm && (
        <Alert variant={testResult.reachable ? 'success' : 'danger'} dismissible onClose={() => setTestResult(null)}>
          <div>{testResult.message}</div>
          {testResult.info && (
            <div className="small mt-1">
              Serial {testResult.info.serial_number ?? '—'} · firmware{' '}
              {testResult.info.firmware_version ?? '—'} · {testResult.info.user_count ?? '—'} users ·{' '}
              {testResult.info.record_count ?? '—'} buffered records
            </div>
          )}
          {testResult.clock_warning && (
            <div className="small mt-1 fw-semibold">{testResult.clock_warning}</div>
          )}
        </Alert>
      )}

      <Card className="shadow-sm">
        <Table responsive hover className="mb-0 align-middle">
          <thead className="table-light">
            <tr>
              <th>Name</th>
              <th>Address</th>
              <th>Role</th>
              <th>Location</th>
              <th>Enabled</th>
              <th>Updated</th>
              <th className="text-end">Actions</th>
            </tr>
          </thead>
          <tbody>
            {(devices.data ?? []).map((device) => (
              <tr key={device.id}>
                <td className="fw-semibold">{device.name}</td>
                <td className="bb-mono">
                  {device.ip_address}:{device.port}
                </td>
                <td>{device.role.replaceAll('_', ' ')}</td>
                <td>{device.location ?? '—'}</td>
                <td>
                  <BoolBadge value={device.enabled} on="Enabled" off="Disabled" />
                </td>
                <td className="small text-muted">{formatDateTime(device.updated_at)}</td>
                <td className="text-end">
                  <div className="d-inline-flex gap-2">
                    <Button
                      size="sm"
                      variant="outline-secondary"
                      onClick={() => testSaved.mutate(device)}
                      disabled={testSaved.isPending}
                    >
                      Test
                    </Button>
                    <Button size="sm" variant="outline-primary" onClick={() => openEdit(device)}>
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="outline-secondary"
                      onClick={() => toggle.mutate({ device, enabled: !device.enabled })}
                    >
                      {device.enabled ? 'Disable' : 'Enable'}
                    </Button>
                    <Button size="sm" variant="outline-danger" onClick={() => setDeleting(device)}>
                      Delete
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
            {(devices.data ?? []).length === 0 && (
              <tr>
                <td colSpan={7} className="text-center text-muted py-4">
                  No devices yet. Add the first terminal to get started.
                </td>
              </tr>
            )}
          </tbody>
        </Table>
      </Card>

      <Modal show={showForm} onHide={() => setShowForm(false)} size="lg" centered>
        <Form onSubmit={onSubmit}>
          <Modal.Header closeButton>
            <Modal.Title className="h5">{editing ? 'Edit device' : 'Add device'}</Modal.Title>
          </Modal.Header>
          <Modal.Body>
            <ErrorAlert error={save.error} />
            {testResult && (
              <Alert variant={testResult.reachable ? 'success' : 'danger'}>
                <div>{testResult.message}</div>
                {testResult.info && (
                  <div className="small mt-1">
                    Serial {testResult.info.serial_number ?? '—'} · firmware{' '}
                    {testResult.info.firmware_version ?? '—'} ·{' '}
                    {testResult.info.record_count ?? '—'} buffered records
                  </div>
                )}
                {testResult.clock_warning && (
                  <div className="small mt-1 fw-semibold">{testResult.clock_warning}</div>
                )}
              </Alert>
            )}

            <Row className="g-3">
              <Col md={6}>
                <Form.Group>
                  <Form.Label>Device name</Form.Label>
                  <Form.Control
                    required
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="Hyderabad Entry"
                  />
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group>
                  <Form.Label>Location</Form.Label>
                  <Form.Control
                    value={form.location}
                    onChange={(e) => setForm({ ...form, location: e.target.value })}
                  />
                </Form.Group>
              </Col>
              <Col md={5}>
                <Form.Group>
                  <Form.Label>IP address</Form.Label>
                  <Form.Control
                    required
                    className="bb-mono"
                    value={form.ip_address}
                    onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
                    placeholder="172.20.200.26"
                  />
                </Form.Group>
              </Col>
              <Col md={3}>
                <Form.Group>
                  <Form.Label>Port</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.port}
                    onChange={(e) => setForm({ ...form, port: Number(e.target.value) })}
                  />
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Timeout (seconds)</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.timeout_seconds}
                    onChange={(e) => setForm({ ...form, timeout_seconds: Number(e.target.value) })}
                  />
                </Form.Group>
              </Col>

              <Col md={6}>
                <Form.Group>
                  <Form.Label>Punch type</Form.Label>
                  <Form.Select
                    value={form.role}
                    onChange={(e) => setForm({ ...form, role: e.target.value as Device['role'] })}
                  >
                    <option value="CHECK_IN">Entry terminal — all punches are Check In</option>
                    <option value="CHECK_OUT">Exit terminal — all punches are Check Out</option>
                    <option value="FROM_DEVICE">Mixed — use the value the device reports</option>
                  </Form.Select>
                  <Form.Text muted>
                    On a dedicated entry or exit terminal, the device it came from decides the
                    punch type.
                  </Form.Text>
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group>
                  <Form.Label>Serial number</Form.Label>
                  <Form.Control
                    className="bb-mono"
                    value={form.serial_number}
                    onChange={(e) => setForm({ ...form, serial_number: e.target.value })}
                  />
                </Form.Group>
              </Col>

              <Col md={6}>
                <Form.Group>
                  <Form.Label>Communication key</Form.Label>
                  <Form.Control
                    type="password"
                    value={form.comm_key}
                    onChange={(e) => setForm({ ...form, comm_key: e.target.value })}
                    placeholder={editing?.comm_key_set ? 'Stored — leave blank to keep' : 'Optional'}
                  />
                  <Form.Text muted>Numeric. Leave blank to keep the existing key.</Form.Text>
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group>
                  <Form.Label>Timezone override</Form.Label>
                  <Form.Control
                    value={form.timezone}
                    onChange={(e) => setForm({ ...form, timezone: e.target.value })}
                    placeholder="Defaults to the application timezone"
                  />
                </Form.Group>
              </Col>

              <Col md={12} className="d-flex gap-4">
                <Form.Check
                  type="switch"
                  label="Enabled"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                />
                <Form.Check
                  type="switch"
                  label="Force UDP"
                  checked={form.force_udp}
                  onChange={(e) => setForm({ ...form, force_udp: e.target.checked })}
                />
              </Col>
            </Row>
          </Modal.Body>
          <Modal.Footer className="justify-content-between">
            <Button
              variant="outline-secondary"
              onClick={() => test.mutate()}
              disabled={!form.ip_address || test.isPending}
            >
              {test.isPending && <Spinner animation="border" size="sm" className="me-2" />}
              Test connection
            </Button>
            <div className="d-flex gap-2">
              <Button variant="outline-secondary" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={save.isPending}>
                {save.isPending && <Spinner animation="border" size="sm" className="me-2" />}
                Save
              </Button>
            </div>
          </Modal.Footer>
        </Form>
      </Modal>

      <ConfirmDialog
        show={deleting !== null}
        title="Delete device"
        body={
          <>
            <p>
              Delete <strong>{deleting?.name}</strong>?
            </p>
            <p className="mb-0 text-muted small">
              Its synchronization state is removed too, so re-adding it later will start from the
              initial lookback window rather than where it left off. Nothing on the terminal itself
              is changed.
            </p>
          </>
        }
        confirmLabel="Delete"
        busy={remove.isPending}
        onConfirm={() => deleting && remove.mutate(deleting)}
        onCancel={() => setDeleting(null)}
      />
    </>
  )
}
