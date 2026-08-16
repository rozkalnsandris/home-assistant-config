# Trusted proxy audit

Issue #8 tracks narrowing Home Assistant `trusted_proxies` from broad private-network ranges to the actual immediate upstream proxy.

## Reviewed infrastructure evidence

The RPi5 infrastructure repository records that:

- the shared Cloudflare Tunnel connector runs as a host-level `cloudflared.service`;
- the reviewed Home Assistant tunnel route targets the RPi5 LAN listener on port `8123`;
- Home Assistant LAN access is intentionally retained as a break-glass path.

The exact private host address is intentionally not repeated in this public repository. See the reviewed V18 Cloudflare LAN-origin contract in `rozkalnsandris/RPi5_main` for infrastructure ownership and route evidence.

## Why the live gate has two evidence paths

Verifier v1 used the current cloudflared journal as runtime route evidence. The first production read-only execution was correctly fail-closed because the current journal no longer contained the HA ingress update line, even though all other host/network checks passed.

Verifier v2 therefore keeps journal evidence as supplemental information and adds a bounded live socket observation that does not depend on historical log retention.

## Read-only live gate

Run from the Home Assistant repository checkout on RPi5:

```bash
sudo python tools/audit_trusted_proxy_topology.py --stdout
```

The audit is deliberately read-only. It:

- detects the running Home Assistant container;
- verifies its Docker network mode;
- verifies `cloudflared.service` is active and reads only its systemd `MainPID`;
- obtains the host's primary IPv4 only in memory;
- verifies the kernel self-route uses that same source address;
- checks whether the current cloudflared journal still contains the reviewed HA route as supplemental evidence;
- checks TCP reachability to HA on the host LAN address;
- inspects only the cloudflared process socket inodes plus `/proc/net/tcp` for a live established origin connection to port `8123`;
- makes a small unauthenticated `GET /` request through the public HA hostname to increase the chance of observing a fresh origin socket;
- emits only classifications and booleans, never the exact address, raw `/proc` endpoints, credentials or journal lines.

The public probe sends no Home Assistant or Cloudflare credentials. Failure to obtain a probe response does not itself authorize or reject a candidate; the decision depends on direct live socket evidence.

The JSON is also written under ignored `exports/` for local evidence.

## Interpretation

`READY_FOR_PRIVATE_SINGLE_HOST_BINDING` now requires direct live evidence that an established socket owned by the running cloudflared process:

- targets port `8123`;
- uses the current host primary private IPv4 as the origin destination; and
- uses that same host primary IPv4 as the source presented to Home Assistant.

Together with host-network mode, active cloudflared, self-route identity and LAN reachability, that supports a local-only `private/http.yaml` candidate shaped as:

```yaml
use_x_forwarded_for: true
trusted_proxies:
  - <exact immediate RPi5 proxy address>
```

The real address stays outside Git. Historical journal evidence alone is no longer enough for a READY decision. `NEEDS_REVIEW` is fail-closed: do not narrow the live configuration from that result.

A green audit does **not** authorize production mutation. Before apply, the private candidate still requires exact Home Assistant `2026.8.2` config validation, a backup/rollback gate, remote-path verification and LAN break-glass verification. Any restart or live `http` change requires separate explicit owner authorization.

**Production deploy/change: NO.**
