# Live portal smoke report — 2026-09-07

This is a point-in-time, opt-in probe of every portal selected by an enabled search
in the local test deployment. It is not part of the offline test suite and is not a
promise that a portal will remain reachable or structurally stable.

The probes ran from the Scrapy project directory with a 60-second per-portal bound:

```powershell
$env:SCRAPYREALESTATE_RUN_LIVE_SMOKE = "1"
python -m scrapyrealestate.live_smoke --timeout-seconds 60
```

The committed report contains no configured URLs, filters, listing data, or secrets.
Full crawl logs and feeds remain only in the ignored runtime data directory.

| Portal | UTC completion | Result | Items | Classification |
| --- | --- | ---: | ---: | --- |
| Fotocasa | 2026-09-07 08:48:47 | success, non-empty | 30 | — |
| Habitaclia | 2026-09-07 08:48:55 | success, non-empty | 15 | — |
| Pisos.com | 2026-09-07 08:49:06 | success, non-empty | 30 | — |
| Yaencontre | 2026-09-07 08:49:49 | parser failure | 0 | `site_change` |

Yaencontre loaded far enough for Playwright to run, but its configured
`article.real-estate-card` selector did not become visible within 30 seconds. The
failure is therefore classified as a likely response-structure/site change. No 403,
429, CAPTCHA, or other blocking marker was present in the retained log. Its fixture
tests remain the deterministic contract until the live selector is reassessed.

The first sandboxed attempt was excluded from the table because local filesystem and
socket policy prevented the crawl runtime from starting. The final results above came
from the explicitly approved unrestricted rerun.
