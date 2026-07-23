# ArchAgent API Contract

This project uses FastAPI OpenAPI as the frontend contract.

- Live JSON contract: `GET /openapi.json`
- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- Checked-in contract artifact: [openapi.json](./openapi.json)

In Insomnia, import or render `GET /openapi.json`, not `GET /docs`. The `/docs` route is an HTML Swagger UI page for a browser; OpenAPI tools that expect a JSON/YAML definition will report it as missing the `openapi` version field.

Regenerate the checked-in contract after API changes:

```bash
python scripts/export_openapi.py
```

For TypeScript, use the generated OpenAPI schema instead of handwritten types:

```bash
npx openapi-typescript docs/openapi.json -o src/api/archagent.schema.d.ts
npx @hey-api/openapi-ts -i docs/openapi.json -o src/api/archagent
```

## Auth Contract

Browser endpoints use the sealed WorkOS session cookie `__Host-archagent-session` in hosted auth mode. Unsafe browser requests (`POST`, `PUT`, `PATCH`, `DELETE`) also require a CSRF token:

1. `GET /auth/csrf`
2. Read `csrf_token` from the response or `__Host-archagent-csrf` from the cookie.
3. Send the same value as `x-archagent-csrf` on unsafe requests.

Local development can select seeded users with `x-archagent-user` (`owner`, `viewer`, or `staff`) and organization context with `x-archagent-organization`. These headers are rejected in production.

`GET /auth/login` starts hosted WorkOS AuthKit. The frontend should navigate to this backend route and let WorkOS own the hosted login screen. The backend asks WorkOS for a fresh sign-in prompt so an old WorkOS browser session is not silently reused.

For logout initiated with `fetch`, call `POST /auth/logout?format=json` with the CSRF header, then navigate the browser to the returned `logout_url`. A top-level browser navigation is required for WorkOS to clear its upstream session; clearing only the ArchAgent cookie can make the next login reuse the previous WorkOS user.

Collector endpoints use `Authorization: Bearer <credential>` after `POST /collector/v1/register`.

## Endpoint Index

The full parameter, request-body, response-body, and security details are in `docs/openapi.json`. This table is the high-level contract map for frontend planning.

| Method | Path | Params | Request Schema | Response Schema | Description |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/healthz` | none | none | `HealthResponse` | Liveness probe |
| `GET` | `/readyz` | none | none | `ReadinessResponse` | Readiness probe |
| `GET` | `/auth/login` | `return_to` query optional | none | redirect | Start WorkOS AuthKit login |
| `GET` | `/auth/callback` | `code` query, `state` query optional | none | redirect | Complete WorkOS login and set session cookie |
| `GET` | `/auth/csrf` | none | none | `CsrfTokenResponse` | Issue CSRF token |
| `POST` | `/auth/logout` | `return_to`, `format` query optional | none | redirect or `LogoutResponse` | Clear session and CSRF cookies |
| `GET` | `/auth/signed-out` | none | none | `SignedOutResponse` | Signed-out confirmation payload |
| `GET` | `/v1/session` | none | none | `SessionResponse` | Current user, organization, role, permissions, and org switch list |
| `POST` | `/v1/session/organization` | none | `OrganizationSwitch` | `OrganizationSwitchResponse` | Switch active WorkOS organization |
| `GET` | `/v1/organizations` | none | none | `OrganizationMembershipResponse[]` | Organizations available to the user |
| `GET` | `/v1/team` | none | none | `TeamResponse` | Active organization members and invitations |
| `POST` | `/v1/team/invitations` | none | `InvitationCreate` | `InvitationResponse` | Invite a team member |
| `DELETE` | `/v1/team/invitations/{invitation_id}` | `invitation_id` path | none | `204` | Revoke an invitation |
| `PATCH` | `/v1/team/members/{membership_id}` | `membership_id` path | `MembershipUpdate` | `MembershipResponse` | Update a member role |
| `DELETE` | `/v1/team/members/{membership_id}` | `membership_id` path | none | `204` | Deactivate a member |
| `GET` | `/v1/clusters` | none | none | `ClusterResponse[]` | List organization clusters |
| `POST` | `/v1/clusters` | none | `ClusterCreate` | `ClusterResponse` | Create a Helm-connected cluster |
| `POST` | `/v1/clusters/{cluster_id}/registration-token` | `cluster_id` path | none | `CollectorRegistrationTokenResponse` | Create a one-time collector registration token |
| `GET` | `/v1/clusters/{cluster_id}/namespaces` | `cluster_id` path | none | `NamespaceResponse[]` | List namespace monitoring choices |
| `PUT` | `/v1/clusters/{cluster_id}/namespaces` | `cluster_id` path | `NamespaceUpdate` | `NamespaceResponse[]` | Replace namespace monitoring choices |
| `GET` | `/v1/knowledge/documents` | none | none | `DocumentResponse[]` | List organization knowledge documents |
| `POST` | `/v1/knowledge/documents/uploads` | none | `DocumentUploadCreate` | `DocumentUploadResponse` | Create document metadata and upload URL |
| `PUT` | `/v1/knowledge/documents/{document_id}/content` | `document_id` path | raw bytes | `204` | Upload document bytes |
| `POST` | `/v1/knowledge/documents/{document_id}/complete` | `document_id` path | none | `DocumentResponse` | Queue document processing |
| `PATCH` | `/v1/knowledge/documents/{document_id}` | `document_id` path | `DocumentUpdate` | `DocumentResponse` | Enable or disable a document |
| `GET` | `/v1/knowledge/documents/{document_id}/download` | `document_id` path | none | binary | Download document bytes |
| `DELETE` | `/v1/knowledge/documents/{document_id}` | `document_id` path | none | `204` | Delete a document |
| `GET` | `/v1/audit-log` | none | none | `AuditEventResponse[]` | Latest organization audit events |
| `POST` | `/v1/analysis-runs` | none | `AnalysisRunCreate` | `AnalysisRunResponse` | Queue or run analysis |
| `GET` | `/v1/analysis-runs` | `cluster_id` query optional | none | `AnalysisRunResponse[]` | List analysis runs |
| `GET` | `/v1/analysis-runs/{run_id}` | `run_id` path | none | `AnalysisRunResponse` | Get one analysis run |
| `GET` | `/v1/topology` | `run_id` query optional, `cluster_id` query optional | none | `TopologyResponse` | UI-ready topology graph |
| `POST` | `/v1/recommendations` | `run_id` query optional, `cluster_id` query optional | `RecommendationRequest` | `RecommendationResponse` | Run recommendation pipeline |
| `POST` | `/collector/v1/register` | none | `RegistrationExchange` | `CollectorRegistrationResponse` | Exchange one-time registration token |
| `POST` | `/collector/v1/heartbeat` | none | `Heartbeat` | `CollectorHeartbeatResponse` | Record collector heartbeat |
| `POST` | `/collector/v1/snapshots` | none | `SnapshotUpload` | `CollectorSnapshotResponse` | Upload hosted collector snapshot |
| `POST` | `/collector/v1/credentials/rotate` | none | none | `CollectorCredentialRotationResponse` | Rotate collector credential |
| `GET` | `/internal/v1/accounts` | none | none | `AccountSummaryResponse[]` | Staff account summaries |
| `GET` | `/internal/v1/accounts/{account_id}` | `account_id` path | none | `AccountDetailsResponse` | Staff account detail |
| `GET` | `/internal/v1/users` | none | none | `UserResponse[]` | Staff user list |
| `GET` | `/internal/v1/knowledge/documents` | none | none | `DocumentResponse[]` | Staff global knowledge documents |
| `POST` | `/internal/v1/knowledge/documents/uploads` | none | `GlobalDocumentUploadCreate` | `DocumentUploadResponse` | Create global document upload |
| `PUT` | `/internal/v1/knowledge/documents/{document_id}/content` | `document_id` path | raw bytes | `204` | Upload global document bytes |
| `POST` | `/internal/v1/knowledge/documents/{document_id}/complete` | `document_id` path | none | `DocumentResponse` | Queue global document processing |
| `POST` | `/internal/v1/knowledge/documents/{document_id}/publish` | `document_id` path | none | `DocumentResponse` | Publish global knowledge document |
| `POST` | `/internal/v1/knowledge/documents/{document_id}/archive` | `document_id` path | none | `DocumentResponse` | Archive global knowledge document |
| `POST` | `/internal/v1/knowledge/documents/{document_id}/rollback` | `document_id` path | none | `DocumentResponse` | Republish previous global document version |
| `POST` | `/webhooks/workos/{path_token}` | `path_token` path | signed WorkOS body | `WorkOSWebhookResponse` | Receive WorkOS lifecycle webhook |
| `POST` | `/internal/tasks/{task_name}` | `task_name` path | `TaskPayload` | `TaskRunResponse` | Private Cloud Tasks handler |

## Error Shape

FastAPI validation and application errors use the standard shape:

```json
{
  "detail": "Human-readable error message or validation details"
}
```

Common statuses are `400` for bad requests, `401` for missing/invalid authentication, `403` for missing authorization or CSRF, `404` for missing resources, `409` for conflicting resource state, and `503` for unavailable snapshot dependencies.
