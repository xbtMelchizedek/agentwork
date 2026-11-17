# IMD fixtures

Public, unauthenticated read-only responses from the IMD control plane (`https://api.imd.fun`),
fetched Nov 17 2026 around 12:26 PM ET and trimmed (long free-text fields shortened, live-only
fields dropped). Routes: `GET /jobs?limit=5`, `GET /jobs/:id`, `/jobs/:id/result`,
`/jobs/:id/records`. Documented at https://imd.fun/docs/ ("Jobs", "Records").

They are test data only. Tests never fetch anything; text in them is untrusted.
