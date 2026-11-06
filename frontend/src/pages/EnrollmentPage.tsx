import { useState } from 'react'
import { Alert, Badge, Button, Card, Table } from 'react-bootstrap'
import { useMutation } from '@tanstack/react-query'

import { api } from '../api/client'
import type { DeviceEnrollment, EnrollmentResult } from '../api/types'
import { ConfirmDialog, ErrorAlert, PageHeader } from '../components/Ui'

/**
 * Local rather than the shared StatusBadge: that one maps sync statuses and would paint
 * a healthy "OK" red, because it does not know the word.
 */
function DeviceStatusBadge({ status }: { status: DeviceEnrollment['status'] }) {
  const variant = status === 'OK' ? 'success' : status === 'FAILED' ? 'danger' : 'warning'
  return <Badge bg={variant}>{status}</Badge>
}

export default function EnrollmentPage() {
  const [result, setResult] = useState<EnrollmentResult | null>(null)
  const [confirm, setConfirm] = useState(false)

  // The preview is the default and the only thing the main button does. Writing to the
  // terminals is a second, separately confirmed step.
  const preview = useMutation({
    mutationFn: () => api.post<EnrollmentResult>('/api/enrollment/sync?dry_run=true'),
    onSuccess: setResult,
  })

  const register = useMutation({
    mutationFn: () => api.post<EnrollmentResult>('/api/enrollment/sync?dry_run=false'),
    onSuccess: (data) => {
      setResult(data)
      setConfirm(false)
    },
    onError: () => setConfirm(false),
  })

  const toAdd = result?.dry_run
    ? result.devices.reduce((total, device) => total + device.added.length, 0)
    : 0

  return (
    <>
      <PageHeader
        title="Employee registration"
        subtitle="Add employees from HRMS to the biometric terminals"
        actions={
          <Button
            onClick={() => preview.mutate()}
            disabled={preview.isPending || register.isPending}
          >
            {preview.isPending ? 'Checking…' : 'Check for new employees'}
          </Button>
        }
      />

      <Alert variant="info" className="small">
        Compares the employees HRMS has switched on for biometric with the users on each
        terminal, and adds the ones that are missing. It only <strong>adds</strong>: users
        already on a terminal are never changed or removed. Fingerprints cannot be captured
        from here — an employee who needs one scans their finger on the terminal afterwards.
      </Alert>

      <ErrorAlert error={preview.error} />
      <ErrorAlert error={register.error} />

      {result && (
        <>
          <p className="text-muted">
            HRMS lists <strong>{result.employees_in_hrms}</strong> employee
            {result.employees_in_hrms === 1 ? '' : 's'} for biometric.
            {result.dry_run && ' This is a preview — nothing has been written to any terminal.'}
          </p>

          {result.devices.length === 0 && (
            <Alert variant="warning">There are no enabled devices to register employees on.</Alert>
          )}

          {result.devices.map((device) => {
            const notAdded = [...device.failed, ...device.skipped]
            return (
              <Card className="shadow-sm mb-4" key={device.device_id}>
                <Card.Header className="bg-white d-flex justify-content-between align-items-center">
                  <span className="fw-semibold">{device.device_name}</span>
                  <DeviceStatusBadge status={device.status} />
                </Card.Header>

                {device.error_message && (
                  <Alert variant="danger" className="m-3 mb-0">
                    {device.error_message}
                  </Alert>
                )}

                <Card.Body className="small text-muted">
                  {device.already_present} already on this terminal · {device.added.length}{' '}
                  {result.dry_run ? 'to add' : 'added'}
                  {device.failed.length > 0 && <> · {device.failed.length} failed</>}
                  {device.skipped.length > 0 && <> · {device.skipped.length} skipped</>}
                </Card.Body>

                {device.added.length > 0 && (
                  <Table responsive size="sm" className="mb-0 align-middle">
                    <thead className="table-light">
                      <tr>
                        <th>Employee code</th>
                        <th>Name</th>
                        <th>Card</th>
                        <th>Notes</th>
                      </tr>
                    </thead>
                    <tbody>
                      {device.added.map((user) => (
                        <tr key={user.employee_code}>
                          <td>{user.employee_code}</td>
                          <td>{user.name}</td>
                          <td>{user.card_set ? 'Set' : '—'}</td>
                          <td className="small text-warning-emphasis">
                            {user.warnings.join(' ')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}

                {notAdded.length > 0 && (
                  <Table responsive size="sm" className="mb-0 align-middle">
                    <thead className="table-light">
                      <tr>
                        <th>Not added</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {notAdded.map((user) => (
                        <tr key={user.employee_code}>
                          <td>{user.employee_code}</td>
                          <td className="small">{user.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                )}
              </Card>
            )
          })}

          {result.dry_run && toAdd > 0 && (
            <Button
              variant="success"
              onClick={() => setConfirm(true)}
              disabled={register.isPending}
            >
              Register {toAdd} user{toAdd === 1 ? '' : 's'} on the terminals
            </Button>
          )}

          {result.dry_run && toAdd === 0 && result.devices.length > 0 && (
            <Alert variant="success">
              Every employee HRMS lists is already on every terminal. Nothing to add.
            </Alert>
          )}
        </>
      )}

      <ConfirmDialog
        show={confirm}
        title="Register employees on the terminals?"
        variant="success"
        confirmLabel="Register"
        busy={register.isPending}
        body={
          <>
            This writes {toAdd} new user{toAdd === 1 ? '' : 's'} to the live terminals. Existing
            users are not changed. The bridge reads each terminal back afterwards to confirm.
          </>
        }
        onConfirm={() => register.mutate()}
        onCancel={() => setConfirm(false)}
      />
    </>
  )
}
