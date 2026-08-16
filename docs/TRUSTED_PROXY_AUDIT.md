# Trusted proxy audit

Issue #8 tracks narrowing Home Assistant `trusted_proxies` from broad private-network ranges to the actual immediate upstream proxy.

## Reviewed infrastructure evidence

The RPi5 infrastructure repository records that:

- the shared Cloudflare Tunnel connector runs as a host-level `cloudflared.service`;
- the reviewed Home Assistant tunnel route targets the RPi5 listener on port `8123`;
- Home Assistant LAN access is intentionally retained as a break-glass path.

The exact private host address is intentionally not repeated in this public repository. See the reviewed Cloudflare LAN-origin contract in `rozkalnsandris/RPi5_main` for infrastructure ownership and route evidence.

## Why the live gate evolved

Verifier v1 used the current cloudflared journal as runtime route evidence. The first RPi5 execution correctly failed closed because the current journal no longer contained the HA ingress update line, even though all other host/network checks passed.

Verifier v2 added direct observation of cloudflared-owned ESTABLISHED IPv4 sockets in `/proc/net/tcp` while making short unauthenticated public requests. The public hostname responded, but no matching IPv4 origin socket was captured. That still did not justify narrowing `trusted_proxies`.

Verifier v3 widens the read-only observation without widening the trust decision:

- it inspects both `/proc/net/tcp` and `/proc/net/tcp6`;
- IPv4-mapped IPv6 endpoints are normalized in memory;
- it attempts an unauthenticated WebSocket upgrade to `/api/websocket` and briefly holds the connection if accepted, creating a larger observation window than a short `GET /`;
- it emits only safe source/destination classes, never exact private addresses or raw `/proc` rows.

Cloudflare documents that cloudflared can keep idle HTTP connections to an origin and exposes Prometheus tunnel metrics, but those metrics do not identify the exact immediate source address Home Assistant sees. For this gate, the observed cloudflared-owned origin socket is therefore the direct evidence used to choose the minimum `trusted_proxies` binding.

## Read-only live gate

Run from the Home Assistant repository checkout on RPi5:

```bash
sudo python tools/audit_trusted_proxy_topology.py --stdout
```

The audit is deliberately read-only. It:

- detects the running Home Assistant container;
- verifies its Docker network mode;
- verifies `cloudflared.service` is active and reads only its systemd `MainPID`;
- obtains the host primary IPv4 only in memory;
- verifies the kernel self-route uses that same source address;
- checks whether the current cloudflared journal still contains the reviewed HA route as supplemental evidence;
- checks TCP reachability to HA on the host LAN address;
- inspects only cloudflared-owned socket inodes in `/proc/net/tcp` and `/proc/net/tcp6` for a live ESTABLISHED origin connection to port `8123`;
- attempts an unauthenticated Home Assistant WebSocket upgrade and, if accepted, holds it for a few seconds while the socket table is sampled;
- falls back to a small unauthenticated `GET /` only if the WebSocket probe receives no HTTP response;
- emits only classifications and booleans, never the exact address, raw `/proc` endpoints, credentials or journal lines.

The probes send no Home Assistant or Cloudflare credentials. The WebSocket probe does not send a Home Assistant authentication message.

The JSON is also written under ignored `exports/` for local evidence.

## Safe source classes

A live origin socket source/destination is reduced to one of these public-safe classes:

- `primary-ipv4` — the current host primary private IPv4;
- `loopback-ipv4`;
- `loopback-ipv6`;
- `other-private-ipv4` / `other-private-ipv6`;
- `other`;
- `unobserved`.

IPv4-mapped IPv6 sockets are normalized to their IPv4 value before classification.

## Interpretation

`READY_FOR_PRIVATE_SINGLE_HOST_BINDING` requires direct live evidence that an ESTABLISHED socket owned by the running cloudflared process targets port `8123` and has one of these exact single-host source/destination shapes:

- primary host IPv4 → primary host IPv4;
- IPv4 loopback → IPv4 loopback;
- IPv6 loopback → IPv6 loopback.

The emitted candidate scope is correspondingly one of:

- `single-host-primary-ipv4`;
- `loopback-ipv4`;
- `loopback-ipv6`.

Any other observed private address remains `NEEDS_REVIEW` because the verifier cannot prove from that class alone that the address is the intended immediate proxy peer.

The first live execution made while real authorized ADMIN traffic was flowing through Cloudflare Access produced `READY_FOR_PRIVATE_SINGLE_HOST_BINDING` with `single-host-primary-ipv4`. The exact address remains local and is not committed or emitted.

A READY result supports a local-only `private/http.yaml` candidate shaped as:

```yaml
use_x_forwarded_for: true
trusted_proxies:
  - <exact immediate proxy source address>
```

The real private address stays outside Git. Historical journal evidence or a successful public probe alone can never produce READY.

## Exact-version candidate validation gate

Before any production apply, validate the private single-host candidate against the already-running Home Assistant image:

```bash
sudo python tools/validate_trusted_proxy_candidate.py --stdout
```

The validator:

- derives the same host primary IPv4 in memory and never prints it;
- requires the running Home Assistant version to match `home-assistant-version.txt` exactly;
- reuses the running container's immutable image identity instead of pulling an image;
- creates only a temporary minimal candidate configuration outside the repository;
- launches an ephemeral validation container with `--pull=never` and `--network=none`;
- mounts only that temporary candidate config, never the production `/config` tree;
- runs Home Assistant `check_config --fail-on-warnings`;
- discards the candidate directory and ephemeral container afterward;
- emits only sanitized pass/fail metadata.

`VALIDATED_FOR_PREPRODUCTION` means the exact single-host candidate shape is accepted by the exact running Home Assistant version. It does **not** prove remote/local access behavior and does not authorize modifying the live `http` configuration.

A green audit or candidate validation does **not** authorize production mutation. Before apply, the full private production candidate still requires backup/rollback preparation, exact Git revision binding, remote-path verification and LAN break-glass verification. Any restart or live `http` change requires separate explicit owner authorization.

**Production deploy/change: NO.**
