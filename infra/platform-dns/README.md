# Platform DNS zone — `launchpad.aklamaash.me`

**This is the first platform-owned infrastructure in the repo.** Everything under
`deployment-services/infrastructure-service/infra/aws/` provisions the *customer's*
account; this provisions Launchpad's own. `CLAUDE.md` said nothing provisioned platform
infrastructure — that stops being true here, which is worth knowing before you apply it.

It exists because TLS needs somewhere to write ACM validation records and the `edge.`
indirection. It is the first shared, all-tenant asset in a product sold on "nothing of
yours lives with us", so the blast radius is designed down rather than assumed small.

## Why a delegated subdomain and not the root

`aklamaash.me` stays exactly where it is, with its own nameservers and its own records.
Only `launchpad.aklamaash.me` is delegated into this zone.

That is not cosmetic. Your mail, and anything else on the root, are **not in this zone at
all** — so the DNS writer below cannot touch them even if its IAM condition were wrong.
Delegating the apex instead would have put every existing `aklamaash.me` record behind this
account and required recreating them here before delegation, or they would stop resolving.

App URLs become `{slug}.{dns_label}.launchpad.aklamaash.me`.

## Apply it in a dedicated AWS account

Not the platform account that holds the AssumeRole principal, and not a customer account.
A credential leaked from here then grants DNS for one delegated subdomain and nothing else.

```bash
cd infra/platform-dns
terraform init
terraform apply          # platform_base_domain defaults to launchpad.aklamaash.me
```

## Delegate — an NS record, not a nameserver change

This is the step that differs from delegating a root domain. You are **not** touching
`aklamaash.me`'s nameservers at the registrar. You are adding one record *inside* the
existing `aklamaash.me` zone, wherever that is hosted today:

```bash
terraform output name_servers
```

Then in the `aklamaash.me` zone:

| Name | Type | Value |
|---|---|---|
| `launchpad` | `NS` | the four nameservers from that output |

Verify:

```bash
dig NS launchpad.aklamaash.me +short     # must return the four above
```

Propagation is usually minutes for a fresh subdomain, but allow hours. **Do this as soon
as the account exists** — it is wall-clock, not work, and it gates every end-to-end test of
the TLS work.

## Create the access key out of band

Deliberately not a terraform resource. An `aws_iam_access_key` would put the secret in
state, and state is a file people copy.

```bash
aws iam create-access-key --user-name launchpad-platform-dns-writer
```

Wire the result into the service that writes records:

```
PLATFORM_BASE_DOMAIN=launchpad.aklamaash.me
PLATFORM_DNS_ZONE_ID=<hosted_zone_id output>
PLATFORM_DNS_ACCESS_KEY_ID=...
PLATFORM_DNS_SECRET_ACCESS_KEY=...
```

These must **not** be read by the provisioning worker. That process already holds
`JWT_SECRET`, `INTERNAL_API_TOKEN`, database credentials and the platform AWS keys; adding
authoritative DNS to it is the concentration this design exists to prevent. The writer
belongs behind its own process or internal endpoint.

## What the policy allows, and why that shape

| | |
|---|---|
| Read | `GetHostedZone`, `ListResourceRecordSets` on this zone only |
| Poll | `GetChange` on `*` — the API takes an opaque change id and AWS publishes no ARN for it |
| Write | `ChangeResourceRecordSets` on this zone, **only** for names two or more labels below the zone apex |

Every record Launchpad writes lives at `<something>.<dns_label>.launchpad.aklamaash.me`:
`edge.<label>....`, the wildcard `*.<label>....`, and the ACM validation CNAME. All are two
labels below the zone apex.

So `launchpad.aklamaash.me` itself, and any single-label record under it, are unreachable
from this credential. Combined with the root living in a different zone entirely, a bug in
hostname construction cannot take your mail offline, cannot touch the root, and cannot
repoint another infrastructure's `edge.` record at a different load balancer.

`ForAllValues` rather than `ForAnyValue` means a batch containing a single disallowed name
is rejected whole rather than partially applied.

## Verify the guard before relying on it

The write restriction depends on the `route53:ChangeResourceRecordSetsNormalizedRecordNames`
condition key — specifically on it being populated for every change and normalised the way
the pattern assumes. **I have not verified that against current AWS documentation.** Test
it before treating it as a control rather than defence in depth:

```bash
ZONE=<hosted_zone_id output>

# Must SUCCEED — two labels below the apex
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" \
  --change-batch '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"edge.0123456789abcdef.launchpad.aklamaash.me","Type":"CNAME","TTL":60,"ResourceRecords":[{"Value":"example.com"}]}}]}'

# Must be DENIED — the zone apex
aws route53 change-resource-record-sets --hosted-zone-id "$ZONE" \
  --change-batch '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"launchpad.aklamaash.me","Type":"TXT","TTL":60,"ResourceRecords":[{"Value":"\"should-not-apply\""}]}}]}'
```

If the second succeeds, the condition is not doing what this file claims and the guard
needs rethinking before any TLS work proceeds. Clean up the first record afterwards.

## Also required, not built here

CloudTrail alerting on out-of-pattern change attempts. The policy denies them; nobody is
told they happened. An attacker probing the boundary should be visible.

## Teardown

Deleting this zone orphans every `edge.` record pointing at a live ALB and every ACM
validation record. A dangling `edge.` CNAME to a deleted load balancer is textbook
subdomain takeover, and a retained validation record permanently authorises whichever
account holds it to issue certificates for a Launchpad hostname. Tear down tenant records
first, then the zone, then remove the `launchpad` NS record from `aklamaash.me`.
