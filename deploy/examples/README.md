# Deployment examples

Concrete, copy-adaptable deployment recipes on top of the base
[`deploy/README.md`](../README.md) walkthrough. Start with `minimal-lan.md`
if this is your first install; the others are variations for specific
situations.

| Example | For |
|---|---|
| [`minimal-lan.md`](minimal-lan.md) | The smallest working setup: `vault-core` + `vault-api`, using a DNS server you already run (AdGuard Home, Pi-hole, dnsmasq, Unbound) instead of the bundled `vault-dns` container. Start here. |
| [`tuned-setup.md`](tuned-setup.md) | Growing past the defaults: a dedicated cache disk/dataset (`VAULT_CACHE_PATH`), scheduler cadence, and the garbage-collection grace window — including an honest note on which of these `deploy/compose.yaml` wires through today and which need a small Compose override. |
| [`truenas-scale-dockge.md`](truenas-scale-dockge.md) | TrueNAS SCALE + Dockge specifically: a dedicated ZFS dataset for the cache (with the `recordsize`/`atime`/`compression` reasoning for ~1 MiB Steam chunks), the Traefik/port-80 conflict SCALE's own Apps ingress commonly creates, and using AdGuard Home as a SCALE app for DNS redirection. |

Every example assumes you've read
[`deploy/README.md`](../README.md) — these pages only add what's specific to
their situation, not a full restatement of it.

Have a deployment pattern that isn't covered here (a different NAS OS, a
Kubernetes/Nomad setup, a reverse-proxy-fronted public-domain profile)? See
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) — a new example following the
same "real, tested-shape config only" standard as the ones above is a
welcome contribution.
