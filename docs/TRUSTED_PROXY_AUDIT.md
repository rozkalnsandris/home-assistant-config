# Trusted proxy audit

Issue #8 tracks narrowing Home Assistant `trusted_proxies` from broad private-network ranges to the actual immediate upstream proxy.

## Reviewed infrastructure evidence

The RPi5 infrastructure repository already records that:

- the shared Cloudflare Tunnel connector runs as a host-level `cloudflared.service`;
- the reviewed Home Assistant tunnel route targets the RPi5 LAN listener on port `8123`;
- Home Assistant LAN access is intentionally retained as a break-glass path.

The exact private host address is intentionally not repeated in this public repository. See the reviewed V18 Cloudflare LAN-origin contract in `rozkalnsandris/RPi5_main` for infrastructure ownership and route evidence.

Home Assistant requires `use_x_forwarded_for` to be paired with `trusted_proxies`, and only the immediate reverse proxy address/network should be trusted. A broad RFC1918 range is not required when the actual upstream is one host address.

## Read-only live gate

Run from the Home Assistant repository checkout on RPi5:

```bash
sudo python tools/audit_trusted_proxy_topology.py --stdout
```

The audit is deliberately read-only. It:

- detects the running Home Assistant container;
- verifies its Docker network mode;
- verifies `cloudflared.service` is active;
- obtains the host's primary IPv4 only in memory;
- verifies the kernel self-route uses that same source address;
- checks that the current cloudflared journal has observed the HA route to that host LAN address on port `8123`;
- checks TCP reachability to HA on the host LAN address;
- emits only classifications and booleans, never the exact address or raw journal lines.

The JSON is also written under ignored `exports/` for local evidence.

## Interpretation

`READY_FOR_PRIVATE_SINGLE_HOST_BINDING` means the observed topology supports a real local-only `private/http.yaml` candidate shaped as:

```yaml
use_x_forwarded_for: true
trusted_proxies:
  - <exact immediate RPi5 proxy address>
```

The real address stays outside Git. `NEEDS_REVIEW` is fail-closed: do not narrow the live configuration from that result.

A green audit does **not** authorize production mutation. Before apply, the private candidate still requires exact Home Assistant `2026.8.2` config validation, a backup/rollback gate, remote-path verification and LAN break-glass verification. Any restart or live `http` change requires separate explicit owner authorization.

**Production deploy/change: NO.**
