# Index Recovery

## Stale Work

An index job uses an opaque owner token and UTC lease expiry; PID is not trusted. The expiry covers the configured operation timeout plus a recovery lease buffer, so a valid operation cannot expire before its own deadline. `index mark-stale --before <ISO-8601>` marks matching pending/running/validating generations failed and removes expired leases. Active and superseded generations are never modified by stale recovery.

## Rollback

`index rollback <repository-id> <generation-id>` accepts only a superseded generation belonging to the repository. One transaction supersedes the current active generation and restores the selected generation.

## Retention

Failed diagnostics contain only a code, safe message, counters, and timestamps. Automatic generation garbage collection is not implemented. Operators must not delete active rows directly; no arbitrary SQL CLI is provided.
