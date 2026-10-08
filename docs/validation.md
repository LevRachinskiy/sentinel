# Initial verification

The local build was verified with Python 3.12 and Node 24. CI targets Python 3.12 and Node 22.

| Check | Local result |
| --- | --- |
| Python suite | 44 passed; 1 PostgreSQL integration test skipped because no PostgreSQL service was available |
| Ruff | Passed |
| TypeScript and frontend production build | Passed |
| Real HTTP smoke | Pool exhaustion and memory regression resolved after approval; dependency timeout escalated; audit chain valid |
| Playwright browser flow | Passed scenario creation, exact-plan approval, recovery, immutable incident evidence, timeline, audit, evaluations, and mobile navigation |
| Synthetic replay | 100 cases; 100% baseline diagnosis and policy-plan accuracy; 0 unsafe policy proposals |
| Docker Compose | Configuration supplied; not executed locally because Docker was unavailable |
| Kubernetes | Adapter API-mock tests passed; no live cluster test |
| OpenAI / Anthropic | Request/response and failure mocks passed; no paid API call or model benchmark |

The Starlette TestClient emitted a deprecation warning about its future HTTP client dependency. It did not affect the test results. Tested package versions are recorded in `requirements-lock.txt` and the frontend npm lockfile.

Synthetic fixtures and the deterministic classifier use the same failure taxonomy. The replay score is a regression result, not independent evidence of production diagnosis accuracy. No customer adoption, scale, real MTTR reduction, or dollar cost savings is claimed.
