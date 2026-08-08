import { useEffect, useState, type FormEvent } from 'react'
import { Alert, Button, Card, Col, Form, Row, Spinner } from 'react-bootstrap'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../api/client'
import type { HrmsConfig } from '../api/types'
import { ErrorAlert, Loading, PageHeader } from '../components/Ui'

export default function HrmsPage() {
  const queryClient = useQueryClient()
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [tenant, setTenant] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [allowHttp, setAllowHttp] = useState(false)
  const [connectTimeout, setConnectTimeout] = useState(10)
  const [requestTimeout, setRequestTimeout] = useState(30)
  const [profileText, setProfileText] = useState('{}')
  const [profileError, setProfileError] = useState<string | null>(null)

  const config = useQuery({
    queryKey: ['hrms-config'],
    queryFn: async () => {
      try {
        return await api.get<HrmsConfig>('/api/hrms/config')
      } catch (error) {
        // 404 simply means nothing has been configured yet.
        if (error instanceof ApiError && error.status === 404) return null
        throw error
      }
    },
    retry: false,
  })

  useEffect(() => {
    const data = config.data
    if (!data) return
    setBaseUrl(data.base_url)
    setTenant(data.tenant_identifier ?? '')
    setEnabled(data.enabled)
    setConnectTimeout(data.connect_timeout_seconds)
    setRequestTimeout(data.request_timeout_seconds)
    setProfileText(JSON.stringify(data.profile, null, 2))
  }, [config.data])

  const save = useMutation({
    mutationFn: () => {
      let profile: unknown
      try {
        profile = JSON.parse(profileText)
      } catch {
        throw new Error('The HRMS profile is not valid JSON.')
      }
      return api.put<HrmsConfig>('/api/hrms/config', {
        base_url: baseUrl,
        // Omitted when blank so an unrelated edit never wipes the stored key.
        api_key: apiKey || null,
        tenant_identifier: tenant || null,
        profile,
        connect_timeout_seconds: connectTimeout,
        request_timeout_seconds: requestTimeout,
        enabled,
        allow_insecure_http: allowHttp,
      })
    },
    onSuccess: () => {
      setApiKey('')
      queryClient.invalidateQueries({ queryKey: ['hrms-config'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })

  const test = useMutation({
    mutationFn: () => api.post<{ reachable: boolean; message: string }>('/api/hrms/test-connection'),
  })

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    try {
      JSON.parse(profileText)
      setProfileError(null)
    } catch {
      setProfileError('The HRMS profile is not valid JSON.')
      return
    }
    save.mutate()
  }

  if (config.isLoading) return <Loading />

  return (
    <>
      <PageHeader
        title="HRMS Configuration"
        subtitle="How this bridge talks to your existing HRMS attendance API"
      />

      <Alert variant="info">
        The bridge adapts to your API — it never changes it. Everything contract-shaped lives in
        the profile below: endpoint, method, auth, field names, batching and response parsing.
        See <code>docs/HRMS-PROFILE.md</code> for each key with a worked example.
      </Alert>

      {config.data && !config.data.profile_configured && (
        <Alert variant="warning">
          The attendance endpoint path is still the shipped placeholder. Synchronization cannot be
          enabled until it is replaced — the bridge will not guess your API contract.
        </Alert>
      )}

      <ErrorAlert error={save.error} />
      {test.data && (
        <Alert variant={test.data.reachable ? 'success' : 'warning'}>{test.data.message}</Alert>
      )}

      <Form onSubmit={onSubmit}>
        <Card className="shadow-sm mb-4">
          <Card.Header className="bg-white fw-semibold">Connection</Card.Header>
          <Card.Body>
            <Row className="g-3">
              <Col md={8}>
                <Form.Group>
                  <Form.Label>HRMS base URL</Form.Label>
                  <Form.Control
                    required
                    className="bb-mono"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://hrms.example.com"
                  />
                </Form.Group>
              </Col>
              <Col md={4}>
                <Form.Group>
                  <Form.Label>Tenant / company id</Form.Label>
                  <Form.Control value={tenant} onChange={(e) => setTenant(e.target.value)} />
                </Form.Group>
              </Col>

              <Col md={8}>
                <Form.Group>
                  <Form.Label>API key</Form.Label>
                  <Form.Control
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={
                      config.data?.api_key_set
                        ? `Stored (${config.data.api_key_masked}) — leave blank to keep`
                        : 'Paste the key issued by your HRMS'
                    }
                  />
                  <Form.Text muted>
                    Write-only. It is never returned by the API, never rendered here, and is
                    scrubbed from the log file.
                  </Form.Text>
                </Form.Group>
              </Col>
              <Col md={2}>
                <Form.Group>
                  <Form.Label>Connect (s)</Form.Label>
                  <Form.Control
                    type="number"
                    value={connectTimeout}
                    onChange={(e) => setConnectTimeout(Number(e.target.value))}
                  />
                </Form.Group>
              </Col>
              <Col md={2}>
                <Form.Group>
                  <Form.Label>Request (s)</Form.Label>
                  <Form.Control
                    type="number"
                    value={requestTimeout}
                    onChange={(e) => setRequestTimeout(Number(e.target.value))}
                  />
                </Form.Group>
              </Col>

              <Col md={12} className="d-flex flex-column gap-2">
                <Form.Check
                  type="switch"
                  label="Enable synchronization to HRMS"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
                <Form.Check
                  type="checkbox"
                  label="Allow plain HTTP (lab environments only — the API key would cross the network in clear text)"
                  checked={allowHttp}
                  onChange={(e) => setAllowHttp(e.target.checked)}
                />
              </Col>
            </Row>
          </Card.Body>
        </Card>

        <Card className="shadow-sm mb-4">
          <Card.Header className="bg-white fw-semibold">HRMS profile</Card.Header>
          <Card.Body>
            {profileError && <Alert variant="danger">{profileError}</Alert>}
            <Form.Control
              as="textarea"
              rows={18}
              className="bb-mono"
              value={profileText}
              onChange={(e) => setProfileText(e.target.value)}
              spellCheck={false}
            />
            <Form.Text muted>
              Fields mapped to <code>null</code> are omitted from the payload entirely, so the
              bridge never sends something your API did not ask for.
            </Form.Text>
          </Card.Body>
        </Card>

        <div className="d-flex gap-2">
          <Button type="submit" disabled={save.isPending}>
            {save.isPending && <Spinner animation="border" size="sm" className="me-2" />}
            Save configuration
          </Button>
          <Button
            variant="outline-secondary"
            onClick={() => test.mutate()}
            disabled={test.isPending || !config.data}
          >
            {test.isPending && <Spinner animation="border" size="sm" className="me-2" />}
            Test HRMS connection
          </Button>
        </div>
      </Form>
    </>
  )
}
