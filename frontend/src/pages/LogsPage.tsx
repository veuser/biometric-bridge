import { useState } from 'react'
import { Badge, Button, Card, Col, Form, Row } from 'react-bootstrap'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { LogPage } from '../api/types'
import { ErrorAlert, Loading, PageHeader, formatDateTime } from '../components/Ui'

const LEVELS = ['ERROR', 'WARNING', 'INFO', 'DEBUG']

function levelVariant(level: string | null): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'danger'
  if (level === 'WARNING') return 'warning'
  if (level === 'DEBUG') return 'secondary'
  return 'info'
}

export default function LogsPage() {
  const [level, setLevel] = useState('')

  const logs = useQuery({
    queryKey: ['logs', level],
    queryFn: () => api.get<LogPage>(`/api/logs?limit=300${level ? `&level=${level}` : ''}`),
    refetchInterval: 15_000,
  })

  return (
    <>
      <PageHeader
        title="Logs & About"
        subtitle="Application log, newest first"
        actions={
          <Button variant="outline-secondary" onClick={() => logs.refetch()}>
            Refresh
          </Button>
        }
      />

      <ErrorAlert error={logs.error} />

      <Card className="shadow-sm mb-4">
        <Card.Body>
          <Row className="g-3 align-items-end">
            <Col md={3}>
              <Form.Label className="small text-muted">Level</Form.Label>
              <Form.Select value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="">All levels</option>
                {LEVELS.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={9}>
              <div className="small text-muted">
                Reading <span className="bb-mono">{logs.data?.file ?? '—'}</span>
                {logs.data?.truncated && ' (tail only)'}
              </div>
              <div className="small text-muted">
                Credentials are removed before anything is written, so nothing sensitive can be
                read back here.
              </div>
            </Col>
          </Row>
        </Card.Body>
      </Card>

      {logs.isLoading ? (
        <Loading />
      ) : (
        <Card className="shadow-sm mb-4">
          <Card.Body className="bb-mono" style={{ maxHeight: '60vh', overflowY: 'auto' }}>
            {(logs.data?.entries ?? []).map((entry, index) => (
              <div key={index} className="bb-log-line border-bottom py-1">
                <span className="text-muted">{formatDateTime(entry.timestamp)}</span>{' '}
                <Badge bg={levelVariant(entry.level)}>{entry.level ?? 'LOG'}</Badge>{' '}
                <span className="text-muted">{entry.logger}</span> {entry.message}
                {Object.keys(entry.extra).length > 0 && (
                  <span className="text-muted"> {JSON.stringify(entry.extra)}</span>
                )}
              </div>
            ))}
            {(logs.data?.entries ?? []).length === 0 && (
              <div className="text-muted py-3">Nothing logged at this level yet.</div>
            )}
          </Card.Body>
        </Card>
      )}

      <Card className="shadow-sm">
        <Card.Header className="bg-white fw-semibold">About</Card.Header>
        <Card.Body>
          <p className="mb-2">
            <strong>Biometric Bridge</strong> reads attendance punches from eSSL/ZK terminals on
            this network and pushes them to your existing HRMS API.
          </p>
          <ul className="small text-muted mb-0">
            <li>Punch records are never stored on this machine — they exist only during a sync.</li>
            <li>The bridge is read-only against terminals; it cannot alter or clear their data.</li>
            <li>
              The HRMS API contract is configuration, not code. The bridge adapts to your API and
              never changes it.
            </li>
          </ul>
        </Card.Body>
      </Card>
    </>
  )
}
