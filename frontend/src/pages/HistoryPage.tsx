import { useState } from 'react'
import { Button, Card, Col, Form, Pagination, Row, Table } from 'react-bootstrap'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Device, SyncHistoryPage } from '../api/types'
import {
  ErrorAlert,
  Loading,
  PageHeader,
  StatusBadge,
  formatDateTime,
  formatDuration,
} from '../components/Ui'

const STATUSES = [
  'SUCCESS',
  'NO_RECORDS',
  'PARTIAL',
  'FAILED',
  'DEVICE_UNAVAILABLE',
  'DEVICE_CLOCK_REGRESSION',
  'DEVICE_CLOCK_JUMP',
  'HRMS_UNAVAILABLE',
  'HRMS_AUTHENTICATION_FAILED',
  'HRMS_BAD_REQUEST',
  'INVALID_RECORD',
  'TIMEOUT',
  'INTERRUPTED',
  'UNKNOWN_ERROR',
]

const TRIGGERS = ['SCHEDULED', 'CATCH_UP', 'MANUAL_RANGE', 'STARTUP']

export default function HistoryPage() {
  const [page, setPage] = useState(1)
  const [deviceId, setDeviceId] = useState('')
  const [status, setStatus] = useState('')
  const [trigger, setTrigger] = useState('')

  const devices = useQuery({
    queryKey: ['devices'],
    queryFn: () => api.get<Device[]>('/api/devices'),
  })

  const params = new URLSearchParams({ page: String(page), page_size: '25' })
  if (deviceId) params.set('device_id', deviceId)
  if (status) params.set('run_status', status)
  if (trigger) params.set('trigger', trigger)

  const history = useQuery({
    queryKey: ['sync-history', page, deviceId, status, trigger],
    queryFn: () => api.get<SyncHistoryPage>(`/api/sync/history?${params}`),
  })

  function resetTo(setter: (value: string) => void) {
    return (value: string) => {
      setter(value)
      setPage(1)
    }
  }

  const totalPages = history.data ? Math.max(1, Math.ceil(history.data.total / history.data.page_size)) : 1

  return (
    <>
      <PageHeader
        title="Sync History"
        subtitle="Execution records only — punch data is never stored by this bridge"
      />

      <ErrorAlert error={history.error} />

      <Card className="shadow-sm mb-3">
        <Card.Body>
          <Row className="g-3 align-items-end">
            <Col md={3}>
              <Form.Label className="small text-muted">Device</Form.Label>
              <Form.Select value={deviceId} onChange={(e) => resetTo(setDeviceId)(e.target.value)}>
                <option value="">All devices</option>
                {(devices.data ?? []).map((device) => (
                  <option key={device.id} value={device.id}>
                    {device.name}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={3}>
              <Form.Label className="small text-muted">Status</Form.Label>
              <Form.Select value={status} onChange={(e) => resetTo(setStatus)(e.target.value)}>
                <option value="">Any status</option>
                {STATUSES.map((value) => (
                  <option key={value} value={value}>
                    {value.replaceAll('_', ' ')}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={3}>
              <Form.Label className="small text-muted">Trigger</Form.Label>
              <Form.Select value={trigger} onChange={(e) => resetTo(setTrigger)(e.target.value)}>
                <option value="">Any trigger</option>
                {TRIGGERS.map((value) => (
                  <option key={value} value={value}>
                    {value.replaceAll('_', ' ')}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={3}>
              <Button
                variant="outline-secondary"
                onClick={() => {
                  setDeviceId('')
                  setStatus('')
                  setTrigger('')
                  setPage(1)
                }}
              >
                Clear filters
              </Button>
            </Col>
          </Row>
        </Card.Body>
      </Card>

      {history.isLoading ? (
        <Loading />
      ) : (
        <Card className="shadow-sm">
          <Table responsive hover className="mb-0 align-middle">
            <thead className="table-light">
              <tr>
                <th>Started</th>
                <th>Device</th>
                <th>Trigger</th>
                <th>Window</th>
                <th className="text-end">Buffer</th>
                <th className="text-end">In window</th>
                <th className="text-end">Sent</th>
                <th className="text-end">OK</th>
                <th className="text-end">Failed</th>
                <th>Duration</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(history.data?.items ?? []).map((run) => (
                <tr key={run.id}>
                  <td className="small">{formatDateTime(run.started_at)}</td>
                  <td>{run.device_name ?? run.device_id}</td>
                  <td className="small">{run.trigger.replaceAll('_', ' ')}</td>
                  <td className="small text-muted">
                    {run.from_time ? (
                      <>
                        {formatDateTime(run.from_time)}
                        <br />
                        {formatDateTime(run.to_time)}
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="text-end">{run.buffer_records}</td>
                  <td className="text-end">{run.window_records}</td>
                  <td className="text-end">{run.records_sent}</td>
                  <td className="text-end">{run.records_success}</td>
                  <td className="text-end">{run.records_failed}</td>
                  <td className="small">{formatDuration(run.duration_ms)}</td>
                  <td>
                    <StatusBadge status={run.status} />
                    {run.error_message && (
                      <div className="small text-danger">{run.error_message}</div>
                    )}
                  </td>
                </tr>
              ))}
              {(history.data?.items ?? []).length === 0 && (
                <tr>
                  <td colSpan={11} className="text-center text-muted py-4">
                    No synchronization runs match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </Table>
        </Card>
      )}

      {totalPages > 1 && (
        <Pagination className="mt-3">
          <Pagination.Prev disabled={page === 1} onClick={() => setPage(page - 1)} />
          <Pagination.Item disabled>
            Page {page} of {totalPages}
          </Pagination.Item>
          <Pagination.Next disabled={page >= totalPages} onClick={() => setPage(page + 1)} />
        </Pagination>
      )}
    </>
  )
}
