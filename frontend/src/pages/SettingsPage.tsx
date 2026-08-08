import { useEffect, useState, type FormEvent } from 'react'
import { Alert, Button, Card, Col, Form, Row, Spinner } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { SyncSettings } from '../api/types'
import { ErrorAlert, Loading, PageHeader } from '../components/Ui'

const INTERVALS = [1, 5, 10, 15, 30, 60]

export default function SettingsPage() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<SyncSettings | null>(null)
  const [saved, setSaved] = useState(false)

  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<SyncSettings>('/api/settings'),
  })

  useEffect(() => {
    if (settings.data) setForm(settings.data)
  }, [settings.data])

  const save = useMutation({
    mutationFn: () => {
      const { interval_warning: _ignored, ...payload } = form!
      return api.put<SyncSettings>('/api/settings', payload)
    },
    onSuccess: (data) => {
      setForm(data)
      setSaved(true)
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
      queryClient.invalidateQueries({ queryKey: ['sync-status'] })
    },
  })

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    setSaved(false)
    save.mutate()
  }

  if (settings.isLoading || !form) return <Loading />

  const update = <K extends keyof SyncSettings>(key: K, value: SyncSettings[K]) =>
    setForm({ ...form, [key]: value })

  return (
    <>
      <PageHeader title="Settings" subtitle="Synchronization behaviour" />

      <ErrorAlert error={save.error} />
      {saved && <Alert variant="success">Settings saved. The scheduler has been reloaded.</Alert>}
      {form.interval_warning && <Alert variant="warning">{form.interval_warning}</Alert>}

      <Form onSubmit={onSubmit}>
        <Card className="shadow-sm mb-4">
          <Card.Header className="bg-white fw-semibold">Automatic synchronization</Card.Header>
          <Card.Body>
            <Row className="g-3">
              <Col md={12}>
                <Form.Check
                  type="switch"
                  label="Run synchronization automatically"
                  checked={form.auto_sync_enabled}
                  onChange={(e) => update('auto_sync_enabled', e.target.checked)}
                />
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Interval</Form.Label>
                  <Form.Select
                    value={form.sync_interval_minutes}
                    onChange={(e) => update('sync_interval_minutes', Number(e.target.value))}
                  >
                    {INTERVALS.map((minutes) => (
                      <option key={minutes} value={minutes}>
                        {minutes} minute{minutes === 1 ? '' : 's'}
                      </option>
                    ))}
                  </Form.Select>
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Overlap (seconds)</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.sync_overlap_seconds}
                    onChange={(e) => update('sync_overlap_seconds', Number(e.target.value))}
                  />
                  <Form.Text muted>
                    Re-reads the end of the last window, catching punches written to the device
                    after a read but timestamped before it finished.
                  </Form.Text>
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>First-sync lookback (hours)</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.initial_lookback_hours}
                    onChange={(e) => update('initial_lookback_hours', Number(e.target.value))}
                  />
                  <Form.Text muted>
                    How far back a brand-new device reaches. Keeps a terminal holding years of
                    history from pushing all of it on install.
                  </Form.Text>
                </Form.Group>
              </Col>
            </Row>
          </Card.Body>
        </Card>

        <Card className="shadow-sm mb-4">
          <Card.Header className="bg-white fw-semibold">Time and device clocks</Card.Header>
          <Card.Body>
            <Row className="g-3">
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Timezone</Form.Label>
                  <Form.Control
                    value={form.timezone}
                    onChange={(e) => update('timezone', e.target.value)}
                  />
                  <Form.Text muted>How device-local timestamps are interpreted.</Form.Text>
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Drift warning (seconds)</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.clock_drift_warning_seconds}
                    onChange={(e) => update('clock_drift_warning_seconds', Number(e.target.value))}
                  />
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Max clock movement (seconds)</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.max_clock_offset_delta_seconds}
                    onChange={(e) =>
                      update('max_clock_offset_delta_seconds', Number(e.target.value))
                    }
                  />
                  <Form.Text muted>
                    Sync is refused if a device clock moves further than this between runs, rather
                    than silently skipping the records in between.
                  </Form.Text>
                </Form.Group>
              </Col>
            </Row>
          </Card.Body>
        </Card>

        <Card className="shadow-sm mb-4">
          <Card.Header className="bg-white fw-semibold">Limits and retention</Card.Header>
          <Card.Body>
            <Row className="g-3">
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Max manual window (days)</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.manual_max_span_days}
                    onChange={(e) => update('manual_max_span_days', Number(e.target.value))}
                  />
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Stale lock timeout (minutes)</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.stale_lock_minutes}
                    onChange={(e) => update('stale_lock_minutes', Number(e.target.value))}
                  />
                  <Form.Text muted>
                    How long before a lock left by a crashed process is reclaimed.
                  </Form.Text>
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>History retention (days)</Form.Label>
                  <Form.Control
                    type="number"
                    value={form.sync_run_retention_days}
                    onChange={(e) => update('sync_run_retention_days', Number(e.target.value))}
                  />
                </Form.Group>
              </Col>
            </Row>
          </Card.Body>
        </Card>

        <Button type="submit" disabled={save.isPending}>
          {save.isPending && <Spinner animation="border" size="sm" className="me-2" />}
          Save settings
        </Button>
      </Form>
    </>
  )
}
