import { Alert, Card, Col, Row, Table } from 'react-bootstrap'
import { useQuery } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Dashboard, SyncStatusResponse } from '../api/types'
import { BoolBadge, ErrorAlert, Loading, PageHeader, StatusBadge, formatDateTime } from '../components/Ui'

function Stat({ label, value, muted }: { label: string; value: string | number; muted?: boolean }) {
  return (
    <Card className="h-100 shadow-sm">
      <Card.Body>
        <div className="text-muted small text-uppercase">{label}</div>
        <div className={'h3 mb-0 ' + (muted ? 'text-muted' : '')}>{value}</div>
      </Card.Body>
    </Card>
  )
}

export default function DashboardPage() {
  const dashboard = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get<Dashboard>('/api/dashboard'),
    // Cheap query, and an operator watching a sync wants it to move.
    refetchInterval: 10_000,
  })

  const status = useQuery({
    queryKey: ['sync-status'],
    queryFn: () => api.get<SyncStatusResponse>('/api/sync/status'),
    refetchInterval: 10_000,
  })

  if (dashboard.isLoading) return <Loading />
  if (dashboard.error) return <ErrorAlert error={dashboard.error} />
  const data = dashboard.data!

  return (
    <>
      <PageHeader title="Dashboard" subtitle="Attendance synchronization health" />

      {data.warnings.map((warning) => (
        <Alert variant="warning" key={warning}>
          {warning}
        </Alert>
      ))}

      <Row className="g-3 mb-4">
        <Col md={3}>
          <Stat label="Devices" value={`${data.devices.enabled} / ${data.devices.total}`} />
        </Col>
        <Col md={3}>
          <Stat label="Runs (24h)" value={data.activity.runs} />
        </Col>
        <Col md={3}>
          <Stat label="Pushed (24h)" value={data.activity.records_pushed} />
        </Col>
        <Col md={3}>
          <Stat
            label="Failed runs (24h)"
            value={data.activity.failed_runs}
            muted={data.activity.failed_runs === 0}
          />
        </Col>
      </Row>

      <Row className="g-3 mb-4">
        <Col md={6}>
          <Card className="shadow-sm h-100">
            <Card.Header className="bg-white fw-semibold">HRMS</Card.Header>
            <Card.Body>
              <dl className="row mb-0 small">
                <dt className="col-6">Endpoint configured</dt>
                <dd className="col-6">
                  <BoolBadge value={data.hrms.profile_configured} on="Yes" off="Not set" />
                </dd>
                <dt className="col-6">API key stored</dt>
                <dd className="col-6">
                  <BoolBadge value={data.hrms.api_key_set} on="Yes" off="No" />
                </dd>
                <dt className="col-6">Sync enabled</dt>
                <dd className="col-6">
                  <BoolBadge value={data.hrms.enabled} on="Enabled" off="Disabled" />
                </dd>
                <dt className="col-6">Base URL</dt>
                <dd className="col-6 text-truncate">{data.hrms.base_url ?? '—'}</dd>
              </dl>
            </Card.Body>
          </Card>
        </Col>

        <Col md={6}>
          <Card className="shadow-sm h-100">
            <Card.Header className="bg-white fw-semibold">Scheduler</Card.Header>
            <Card.Body>
              <dl className="row mb-0 small">
                <dt className="col-6">Service</dt>
                <dd className="col-6">
                  <BoolBadge value={data.scheduler.running} on="Running" off="Stopped" />
                </dd>
                <dt className="col-6">Automatic sync</dt>
                <dd className="col-6">
                  <BoolBadge value={data.scheduler.auto_sync_enabled} on="On" off="Off" />
                </dd>
                <dt className="col-6">Interval</dt>
                <dd className="col-6">{data.scheduler.interval_minutes} min</dd>
                <dt className="col-6">Next run</dt>
                <dd className="col-6">{formatDateTime(data.scheduler.next_run_at)}</dd>
              </dl>
            </Card.Body>
          </Card>
        </Col>
      </Row>

      <Card className="shadow-sm mb-4">
        <Card.Header className="bg-white fw-semibold">Per-device synchronization state</Card.Header>
        <Table responsive hover className="mb-0 align-middle">
          <thead className="table-light">
            <tr>
              <th>Device</th>
              <th>Status</th>
              <th>Last successful push</th>
              <th>Clock offset</th>
              <th>Enabled</th>
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
                <td>
                  {device.clock_offset_seconds == null
                    ? '—'
                    : `${device.clock_offset_seconds.toFixed(0)} s`}
                  {device.clock_warning && (
                    <span className="badge bg-warning text-dark ms-2">drift</span>
                  )}
                </td>
                <td>
                  <BoolBadge value={device.enabled} on="Enabled" off="Disabled" />
                </td>
              </tr>
            ))}
            {(status.data?.devices ?? []).length === 0 && (
              <tr>
                <td colSpan={5} className="text-muted text-center py-4">
                  No devices configured yet.
                </td>
              </tr>
            )}
          </tbody>
        </Table>
      </Card>

      <Row className="g-3">
        <Col md={6}>
          <Card className="shadow-sm h-100">
            <Card.Body>
              <div className="text-muted small text-uppercase">Last successful sync</div>
              <div>{formatDateTime(data.last_successful_sync_at)}</div>
            </Card.Body>
          </Card>
        </Col>
        <Col md={6}>
          <Card className="shadow-sm h-100">
            <Card.Body>
              <div className="text-muted small text-uppercase">Last failure</div>
              <div>{formatDateTime(data.last_failed_sync_at)}</div>
              {data.last_error && <div className="text-danger small mt-1">{data.last_error}</div>}
            </Card.Body>
          </Card>
        </Col>
      </Row>
    </>
  )
}
