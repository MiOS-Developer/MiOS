<!-- AI-hint: Operator guide for joining a Raspberry Pi / edge node to a MiOS council over the single outbound-dial port (agent-pipe, port key `agent_pipe`, MIOS_PORTS_AGENT_PIPE), using the three-layer mios.toml overlay and the [agents.<name>] remote-http template. Covers the optional federated pgvector path ([pgvector].listen_loopback=false) and the least-privilege posture for untrusted edge peers. Fulfils roadmap task_72 (WS-D2); depends on the D1 remote/edge agent template ([agents._defaults]).
     AI-related: ../../mios/mios.toml ([agents], [pgvector], [security]), ../reference/api.md (the `agent_pipe`-port /v1 + /a2a surface), ./edge-node-join.md -->

# Joining a Pi / Edge Node to a MiOS Council

This guide gets a small or headless node — a Raspberry Pi, a second MiOS box, a
laptop — talking to an existing MiOS **hub** as a first-class council peer, using
**one outbound port** and a **TOML overlay only**. No source edits, no bespoke
per-node code.

The design contract: an edge node **dials out** to the hub's unified endpoint;
the hub never has to reach back into the edge node. That is what makes this work
across NAT, tailnets, and firewalled home LANs.

---

## 0. What "join" means here

A MiOS hub exposes its whole brain on a **single port** — the agent-pipe,
port key `agent_pipe` (`MIOS_PORTS_AGENT_PIPE`). Three surfaces live there:

| Path | Purpose |
|---|---|
| `/v1/*` | OpenAI-compatible chat/embeddings (the inference council) |
| `/a2a` | Agent-to-Agent federation (peer delegation) |
| `/health`, `/v1/cluster/health` | liveness + council status |

"Joining" is just: the edge node is configured with **one `[agents.<name>]`
entry** that points at the hub's `agent_pipe` port and presents a credential. Any
reachable OpenAI `/v1` endpoint + credential becomes a council peer — that is the
open-federation keystone (`[agents._defaults].auth`). You do **not** edit
`server.py` or ship code to add a peer.

Prerequisites:

- The hub is reachable from the edge node on the `agent_pipe` port (LAN IP,
  tailnet address, or reverse-tunnel). Verify from the edge node:
  ```bash
  curl -fsS http://<HUB>:${MIOS_PORTS_AGENT_PIPE}/health && echo OK
  ```
- If the hub has `[security].require_auth = true` (recommended once it binds
  anything other than loopback), you have a **caller key** for the edge node
  (issued on the hub — see §4).

---

## 1. The overlay you edit (never the vendor file)

Every operator-tunable value flows from `mios.toml` with a **three-layer
override**, highest wins:

```
~/.config/mios/mios.toml     # per-user   (edit THIS on the edge node)
/etc/mios/mios.toml          # host/admin (written by bootstrap)
/usr/share/mios/mios.toml     # vendor defaults (immutable, in the image)
```

On the edge node, put everything below in `~/.config/mios/mios.toml`. This file
is an **overlay** — it only needs the keys you override, not a full copy. Create
it directly, or seed a full template first with `just init-user-space`:

```bash
mkdir -p ~/.config/mios
just init-user-space        # optional: seed the vendor template into ~/.config/mios/mios.toml
${EDITOR:-nano} ~/.config/mios/mios.toml
```

After editing, refresh the derived shell/systemd bridge so services pick it up:

```bash
mios-sync-env               # regenerates /etc/mios/install.env from mios.toml
```

---

## 2. The join block (edge node → hub)

Add ONE `[agents.<name>]` entry. It inherits everything from
`[agents._defaults]` and overrides only what differs. This is the whole join:

```toml
# ~/.config/mios/mios.toml  (on the EDGE node)

[agents.hub]
kind            = "remote-http"      # dial an off-box MiOS/OpenAI /v1 endpoint
endpoint        = "http://<HUB>:<PORT>/v1" # the hub's agent-pipe port (see §0)
api             = "openai"           # OpenAI-compatible surface
fanout          = true               # let the council route work to it
health_gate     = true              # skip this peer when it is unreachable
                                     # (degrade-open: a dead hub never wedges the node)

[agents.hub.auth]
scheme          = "bearer"
# Env-resolved at load (same render as MCP headers). Put the secret in the
# environment, NOT in the TOML, so the file is safe to sync/commit.
header_template = "Authorization: Bearer ${MIOS_AGENT_HUB_KEY}"
```

Provide the credential out-of-band (systemd `Environment=`, a shell profile, or
`/etc/mios/install.env`), e.g.:

```bash
export MIOS_AGENT_HUB_KEY='<caller-key-issued-by-the-hub>'
```

A **local** endpoint (loopback) keeps using the shared backend key automatically;
a **non-local** endpoint with no `header_template` simply gets no header and
degrades open. Bearer is the normal edge posture.

Verify the edge node now sees the hub as a peer:

```bash
mios-sync-env
curl -fsS http://<HUB>:${MIOS_PORTS_AGENT_PIPE}/v1/models  # should list the hub's models
# then exercise a real hop:
echo "hello from the pi" | mios              # routes through the council incl. the hub
```

---

## 3. Least-privilege for an *untrusted* edge node

If the edge node is guest/portable hardware, cap what a peer dialing IT can do,
and cap what IT can ask of the hub. Both live on the same entry:

```toml
[agents.hub]
# ... as above ...
max_permission  = "read"             # "" = no ceiling; "read" = no state-changing verbs
denied_verbs    = ["shell", "write_file"]   # hard blocklist for this peer
allowed_verbs   = []                 # empty = allow all not denied

[agents.hub.trust]
min_reputation           = 0.0       # drop the peer below this reputation score
require_signed_principal = false     # true = require a verified Ed25519 principal
mtls                     = false     # true = require mTLS for this peer
```

On the **hub** side, the front-door gate that makes any of this meaningful is
`[security].require_auth`. Keep it `false` (degrade-open) only while the hub is
loopback-bound; set it `true` before the hub binds a LAN/tailnet address, and
issue the edge node a scoped caller key (§4).

---

## 4. Issuing the edge node a caller key (on the hub)

When the hub runs `[security].require_auth = true`, unauthenticated `/v1` and
`/a2a` calls return `401`. Mint a per-node key on the **hub**:

- Add the node's key to the runtime overlay `/etc/mios/ai/v1/caller-keys.json`
  (this file is a per-host overlay, never baked into the vendor image).
- Hand that key to the edge node as `MIOS_AGENT_HUB_KEY` (§2).
- Revoke later with the admin surface: `POST /v1/admin/keys/revoke` (CRL
  hot-reloads; no restart).

The hub injects a scoped identity (permission ceiling + RBAC + reputation) on
every valid credential, so a compromised edge key cannot exceed its tier.

---

## 5. Optional: federated pgvector (shared memory)

By default the hub's Postgres/pgvector listener is **loopback-only** — the
council federates over `/a2a` on the `agent_pipe` port, which is enough for most
deployments and keeps the datastore off the network. If you specifically want
edge nodes to read the **shared agent memory/knowledge store** directly, expose
it on the **hub**:

```toml
# /usr/share/mios/mios.toml  ->  override in ~/.config/mios/mios.toml on the HUB
[pgvector]
listen_loopback = false              # default true (127.0.0.1). false -> bind 0.0.0.0
                                     # maps to MIOS_PG_BIND_ADDR, rendered by the quadlet
```

Then `mios-sync-env` and restart the pgvector unit on the hub. Off-box exposure
is a deliberate federated-deployment choice — pair it with a firewall scope
(loopback + tailnet `100.64.0.0/10` + local WSL gateway `172.16.0.0/12`) and,
for multi-user nodes, `[pgvector].rls_mode = "enforce"` so a peer only recalls
rows it owns (plus shared/legacy rows). Most edge fleets should **leave this off**
and federate over `/a2a` only.

---

## 6. Checklist — a Pi joins by following this doc alone

1. `curl -fsS http://<HUB>:${MIOS_PORTS_AGENT_PIPE}/health` from the Pi → `OK`.
2. Create/seed `~/.config/mios/mios.toml` (`just init-user-space` optional) → edit it.
3. Add the `[agents.hub]` remote-http block (§2); set `MIOS_AGENT_HUB_KEY` if the
   hub requires auth.
4. `mios-sync-env`.
5. `curl -fsS http://<HUB>:${MIOS_PORTS_AGENT_PIPE}/v1/models` lists the hub's models.
6. `echo hi | mios` routes through the council including the hub.
7. (Optional) enable federated pgvector on the hub only if you need shared memory.

No source reading required. Everything above is `mios.toml` overlay + one
outbound port.
