# Container fixture soak report — 2026-09-07

The opt-in soak ran successfully in a newly built `scrapyrealestate:local` image
with an isolated temporary Docker volume. The workload uses deterministic fixture
items and never contacts a property portal or notification provider.

Command:

```powershell
$env:SCRAPYREALESTATE_RUN_DOCKER_SOAK = "1"
python -m pytest tests/test_container_soak.py -m soak
```

Results:

| Check | Result |
| --- | ---: |
| Fixture searches | 3 |
| Scheduler cycles | 20 |
| Scheduled runs by search | 20 / 10 / 6 |
| Deliberate same-search overlap rejections | 1 |
| Maximum simultaneous attempts per search | 1 |
| SQLite `PRAGMA integrity_check` | `ok` |
| Notification events | 3 |
| Successful delivery attempts | 3 |
| Duplicate successful event/channel pairs | 0 |
| Active fixture attempts after completion | 0 |
| Container running after command exit | no |

The harness retains its machine-readable `soak-report.json` only in the temporary
test volume, verifies it from a separate container, then removes both the stopped
workload container and volume. The regular offline test invokes the same workload
with six cycles so its scheduling, locking, persistence, and delivery assertions
remain deterministic without Docker.
