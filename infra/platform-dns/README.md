# Platform DNS zone

**This is the first platform-owned infrastructure in the repo.** Everything under
`deployment-services/infrastructure-service/infra/aws/` provisions the *customer's*
account; this provisions Launchpad's own. `CLAUDE.md` said nothing provisioned platform
infrastructure — that stops being true here, which is worth knowing before you apply it.

It exists because TLS needs somewhere to write DNS validation records and the `edge.`
indirection. It is the first shared, all-tenant asset in a product sold on "nothing of
yours lives with us", so the blast radius is designed down rather than assumed small.

## Apply it in a dedicated AWS account

Not the platform account that holds the AssumeRole principal, and not a customer account.
A credential leaked from here then grants DNS for one zone and nothing else.

```bash
cd infra/platform-dns
terraform init
terraform apply -var="platform_base_domain=<your-domain>"
```

Then delegate at your registrar:

```bash
terraform output name_servers        # set these as the domain's NS records
dig NS <your-domain> +short          # must return them before TLS work can be tested
```

Propagation is hours, sometimes longer. **Start this before writing any TLS code** — it is
wall-clock, not work, and it gates every end-to-end test.

## Create the access key out of band

Deliberately not a terraform resource. An `aws_iam_access_key` would put the secret in
state, and state is a file people copy.

```bash
aws iam create-access-key --user-name launchpad-platform-dns-writer
```

Wire the result into the service that writes records:

```
PLATFORM_BASE_DOMAIN=<your-domain>
PLATFORM_DNS_ZONE_ID=<hosted_zone_id output>
PLATFORM_DNS_ACCESS_KEY_ID=...
PLATFORM_DNS_SECRET_ACCESS_KEY=...
```

These must **not** be read by the provisioning worker. That process already holds
`JWT_SECRET`, `INTERNAL_API_TOKEN`, database credentials and the platform AWS keys; adding
authoritative DNS to it is the concentration H1 exists to prevent. The writer belongs
behind its own process or internal endpoint.

## What the policy allows, and why that shape

| | |
|---|---|
| Read | `GetHostedZone`, `ListResourceRecordSets` on this zone only |
| Poll | `GetChange` on `*` — the API takes an opaque change id and AWS publishes no ARN for it |
| Write | `ChangeResourceRecordSets` on this zone, **only** for names two or more labels below the apex |

Every record Launchpad writes lives at `<something>.<dns_label>.<domain>`:
`edge.<label>.<domain>`, the wildcard `*.<label>.<domain>`, and the ACM validation CNAME.
All are two labels below the apex.

The apex itself, `www`, and MX/SPF/DKIM/DMARC records are all one label up and therefore
**unreachable from this credential**. That is the point: a bug in hostname construction
cannot take the platform's own mail or website offline, and cannot rewrite another
infrastructure's `edge.` record into a different ALB.

`ForAllValues` rather than `ForAnyValue` means a batch containing a single disallowed name
is rejected whole rather than partially applied.

## Verify before relying on the guard

The write restriction depends on the `route53:ChangeResourceRecordSetsNormalizedRecordNames`
condition key — specifically on it being populated for every change and normalised the way
the pattern assumes. **Confirm that against current AWS documentation, and test it**, before
treating it as a control rather than a defence in depth:

```bash
# Should succeed
aws route53 change-resource-record-sets --hosted-zone-id <id> \
  --change-batch '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"edge.0123456789abcdef.<domain>","Type":"CNAME","TTL":60,"ResourceRecords":[{"Value":"example.com"}]}}]}'

# Should be denied
aws route53 change-resource-record-sets --hosted-zone-id <id> \
  --change-batch '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{"Name":"<domain>","Type":"TXT","TTL":60,"ResourceRecords":[{"Value":"\"should-not-apply\""}]}}]}'
```

If the second one succeeds, the condition is not doing what this file claims and the guard
needs rethinking before any TLS work proceeds.

## Also required, not built here

CloudTrail alerting on out-of-pattern change attempts. The policy denies them; nobody is
told they happened. An attacker probing the boundary should be visible.

## Teardown

Deleting the zone orphans every `edge.` record pointing at a live ALB and every ACM
validation record. A dangling `edge.` CNAME to a deleted load balancer is textbook
subdomain takeover, and a retained validation record permanently authorises whichever
account holds it to issue certificates for a Launchpad hostname. Tear down tenant records
first — see `plan/F1b-tls-activation.md`.
