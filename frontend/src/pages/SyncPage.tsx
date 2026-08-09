import { useState } from 'react'
import { Alert, Button, Card, Col, Form, Row, Spinner, Table } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Device, SyncOutcome, SyncStatusResponse } from '../api/types'
import {
  ConfirmDialog,
  ErrorAlert,
  Loading,
  PageHeader,
  StatusBadge,
  formatDateTime,
} from '../components/Ui'

/** datetime-local gives naive text; the API requires an offset-aware value. */
function toAware(localValue: string): string {
  return new Date(localValue).toISOString()
}

export default function SyncPage() {
  const queryClient = useQueryClient()
  const [manualDevice, setManualDevice] = useState<number | ''>('')
  const [fromTime, setFromTime] = useState('')
  const [toTime, setToTime] = useState('')
  const [confirmManual, setConfirmManual] = useState(false)
  const [confirmCatchUp, setConfirmCatchUp] = useState(false)
  const [result, setResult] = useState<SyncOutcome[] | null>(null)

  const devices = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.get<Device[]>('/api/devices'),
  })
  const status = useQuery({
    queryKey: ['sync-status'],
    queryFn: () => api.get<SyncStatusResponse>('/api/sync/status'),
    refetchInterval: 10_000,
  })

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['sync-status'] })
    queryClient.invalidateQueries({ queryKey: ['sync-history'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard'] })
  }

  const catchUp = useMutation({
    mutationFn: () => api.post<SyncOutcome[]>('/api/sync/catch-up'),
    onSuccess: (data) => {
      setResult(data)
      setConfirmCatchUp(false)
      refresh()
    },
    onError: () => setConfirmCatchUp(false),
  })

  const manual = useMutation({
    mutationFn: () =>
      api.post<SyncOutcome>(`/api/sync/manual-range/${manualDevice}`, {
        from_time: toAware(fromTime),
        to_time: toAware(toTime),
      }),
    onSuccess: (data) => {
      setResult([data])
      setConfirmManual(false)
      refresh()
    },
    onError: () => setConfirmManual(false),
  })

  if (devices.isLoading) return <Loading />

  return (
    <>
      <PageHeader title="Synchronization" subtitle="Run a sync now, or recover a historical window" />

      <ErrorAlert error={catchUp.error} />
      <ErrorAlert error={manual.error} />

      {result && (
        <Card className="shadow-sm mb-4">
          <Card.Header className="bg-white fw-semibold">Result</Card.Header>
          <Table responsive className="mb-0 align-middle">
            <thead className="table-light">
              <tr>
                <th>Device</th>
                <th>Status</th>
                <th>Buffer</th>
                <th>In window</th>
                <th>Sent</th>
                <th>Succeeded</th>
                <th>Failed</th>
                <th>Suppressed</th>
              </tr>
            </thead>
            <tbody>
              {result.map((outcome) => (
                // Keyed by device, not run_id: a device that was skipped has no run,
                // and several such rows would all carry run_id 0 and collide.
                <tr key={outcome.device_id}>
                  <td>
                    {devices.data?.find((d) => d.id === outcome.device_id)?.name ??
                      outcome.device_id}
                  </td>
                  <td>
                    <StatusBadge status={outcome.status} />
                  </td>
                  {outcome.status === 'SKIPPED' ? (
                    // Counts would all read 0 and look like "read nothing, sent
                    // nothing" — a successful-looking idle run. Nothing was attempted,
                    // so say why instead of showing six zeroes.
                    <td colSpan={6} className="text-muted small">
                      {outcome.error_message ?? 'Not attempted.'}
                    </td>
                  ) : (
                    <>
                      <td>{outcome.buffer_records}</td>
                      <td>{outcome.window_records}</td>
                      <td>{outcome.records_sent}</td>
                      <td>{outcome.records_success}</td>
                      <td>{outcome.records_failed}</td>
                      <td>{outcome.records_suppressed}</td>
                    </>
                  )}
                </tr>
              ))}
              {result.length === 0 && (
                <tr>
                  <td colSpan={8} className="text-muted text-center py-3">
                    No enabled devices to sync.
                  </td>
                </tr>
              )}
            </tbody>
          </Table>
        </Card>
      )}

      <Row className="g-4">
        <Col lg={5}>
          <Card className="shadow-sm h-100">
            <Card.Header className="bg-white fw-semibold">Run now</Card.Header>
            <Card.Body>
              <p className="text-muted small">
                Runs the same window the scheduler would, for every enabled device, and advances
                each device&apos;s watermark on success.
              </p>
              <Button onClick={() => setConfirmCatchUp(true)} disabled={catchUp.isPending}>
                {catchUp.isPending && <Spinner animation="border" size="sm" className="me-2" />}
                Sync all devices now
              </Button>
            </Card.Body>
          </Card>
        </Col>

        <Col lg={7}>
          <Card className="shadow-sm h-100">
            <Card.Header className="bg-white fw-semibold">Historical range</Card.Header>
            <Card.Body>
              <Alert variant="secondary" className="small">
                A recovery operation for a specific window. It pushes whatever it finds but{' '}
                <strong>never changes automatic synchronization</strong> — the watermark is left
                exactly as it was.
              </Alert>
              <Row className="g-3">
                <Col md={12}>
                  <Form.Group>
                    <Form.Label>Device</Form.Label>
                    <Form.Select
                      value={manualDevice}
                      onChange={(e) =>
                        setManualDevice(e.target.value ? Number(e.target.value) : '')
                      }
                    >
                      <option value="">Select a device…</option>
                      {(devices.data ?? [])
                        .filter((d) => d.enabled)
                        .map((device) => (
                          <option key={device.id} value={device.id}>
                            {device.name}
                          </option>
                        ))}
                    </Form.Select>
                  </Form.Group>
                </Col>
                <Col md={6}>
                  <Form.Group>
                    <Form.Label>From</Form.Label>
                    <Form.Control
                      type="datetime-local"
                      value={fromTime}
                      onChange={(e) => setFromTime(e.target.value)}
                    />
                  </Form.Group>
                </Col>
                <Col md={6}>
                  <Form.Group>
                    <Form.Label>To</Form.Label>
                    <Form.Control
                      type="datetime-local"
                      value={toTime}
                      onChange={(e) => setToTime(e.target.value)}
                    />
                  </Form.Group>
                </Col>
              </Row>
              <Button
                className="mt-3"
                variant="outline-primary"
                disabled={!manualDevice || !fromTime || !toTime || manual.isPending}
                onClick={() => setConfirmManual(true)}
              >
                {manual.isPending && <Spinner animation="border" size="sm" className="me-2" />}
                Sync this range
              </Button>
            </Card.Body>
          </Card>
        </Col>
      </Row>

      <Card className="shadow-sm mt-4">
        <Card.Header className="bg-white fw-semibold">Per-device state</Card.Header>
        <Table responsive hover className="mb-0 align-middle">
          <thead className="table-light">
            <tr>
              <th>Device</th>
              <th>Status</th>
              <th>Last successful push</th>
              <th>Last completed</th>
              <th>Last error</th>
            </tr>
          </thead>
          <tbody>
            {(status.data?.devices ?? []).map((device) => (
              <tr key={device.device_id}>
                <td>{device.device_name}</td>
                <td>
                  <StatusBadge status={device.is_running ? 'RUNNING' : device.last_status} />
                </td>
                <td>{formatDateTime(device.last_successful_push_at)}</td>
                <td>{formatDateTime(device.last_sync_completed_at)}</td>
                <td className="small text-danger">{device.last_error ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>

      <ConfirmDialog
        show={confirmCatchUp}
        title="Sync all devices now"
        body="Every enabled terminal will be read and any new punches pushed to HRMS. Watermarks advance on success."
        confirmLabel="Run sync"
        variant="primary"
        busy={catchUp.isPending}
        onConfirm={() => catchUp.mutate()}
        onCancel={() => setConfirmCatchUp(false)}
      />

      <ConfirmDialog
        show={confirmManual}
        title="Sync a historical range"
        body={
          <>
            <p>Push every punch in the selected window to HRMS.</p>
            <p className="mb-0 text-muted small">
              Records HRMS has already received may be sent again. Automatic synchronization is
              unaffected.
            </p>
          </>
        }
        confirmLabel="Run range sync"
        variant="primary"
        busy={manual.isPending}
        onConfirm={() => manual.mutate()}
        onCancel={() => setConfirmManual(false)}
      />
    </>
  )
}
