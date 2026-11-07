# Security

DryRun AI is a bounded pre-execution review tool, not a safety certification.
Its security claims are limited to the controls and evidence sources described
below.

## Security boundaries

- The FastAPI service, Nginx edge, SQLite ledger, and Docker Engine run on the
  Vultr Cloud Compute VM.
- Vultr Serverless Inference receives the source-analysis and planning
  requests. The model does not receive authority to change Docker flags,
  policy decisions, rate limits, or evidence trust.
- The shared execution gate limits concurrent sandbox admission.
- Submitted Python is executed only after server-side validation and policy
  approval.
- The production sandbox has no network, a read-only root filesystem,
  non-root identity, dropped capabilities, no-new-privileges, bounded
  resources, a 15-second timeout, and bounded output capture.

## Evidence integrity

Docker Engine metadata is trusted only for the configuration and lifecycle
facts it reports. Host-runner facts such as exit status, timeout, cleanup, and
captured output are similarly narrow.

Output, event records, or evidence markers printed by the submitted program
are untrusted. They cannot create host-confirmed evidence. A prediction with
no trusted observation remains `predicted_not_observed`; that status does not
mean the operation was impossible.

The nine Docker checks verify configuration metadata. They do not prove that
each individual file, network, or process operation was blocked. The separate
eight-probe Containment Lab provides bounded observations and is not run
automatically during every normal analysis.

## Secret handling

Keep `.env`, API keys, access codes, database files, and logs out of source
control. Use [.env.example](./.env.example) as a variable-name reference only.
Do not paste secrets into public issues, demo source files, reports, or
support requests.

The application reads `VULTR_INFERENCE_API_KEY`, `DRYRUN_API_KEY`,
`DRYRUN_DB_PATH`, `DEMO_MODE`, and `ACCESS_CODE` from environment
configuration. Public demo results are not persisted to shared history.

## Supported environment

The runner currently supports Python source files and uses the
`python:3.12-slim` Docker image. Docker Engine must be available to the
service account. The application is designed for deployment on a Vultr Cloud
Compute VM with Nginx and HTTPS at the edge.

## Known limitations

- DryRun AI does not prove that submitted code is safe.
- It does not provide complete operation-level telemetry for every file,
  process, or network action.
- One normal submitted Python file receives one bounded sandbox run; planned
  test cases are not separate executions.
- Docker shares the host kernel and cannot establish protection against every
  kernel-level escape.
- Configuration checks verify Docker-reported values rather than universal
  runtime behavior.
- The production runner supports Python only and disables network access.
- A bounded successful run is not a security certification.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue. Contact the
repository owner privately through the contact method available on the
repository profile or project page. Do not assume that a security email
address or GitHub private vulnerability reporting is enabled.

Include:

1. A concise description and impact.
2. A safe minimal reproducer, if available.
3. Affected file, endpoint, commit, or deployment context.
4. Reproduction steps and expected versus actual behavior.
5. Any relevant logs with secrets and personal data removed.

Never include API keys, access codes, bearer tokens, `.env` contents, private
source, or other credentials in a report. Allow reasonable time for
investigation and remediation before public disclosure.
