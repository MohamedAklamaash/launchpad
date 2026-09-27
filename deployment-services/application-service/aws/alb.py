import logging
import os
import threading

from botocore.exceptions import ClientError

from aws.tags import as_key_value_tags

logger = logging.getLogger(__name__)


class SniCertificateCapExceeded(RuntimeError):
    """The listener already carries as many SNI certificates as this platform will place
    on it. Raised both by ALBClient.add_listener_certificate's own AWS-side TooManyCertificates
    catch and by the caller's own pre-check (see custom_domains.attach_custom_domain) —
    either way, the caller must not have already committed anything to the database."""

    def __init__(self, listener_arn: str):
        super().__init__(f"SNI certificate cap reached on listener {listener_arn}")
        self.listener_arn = listener_arn


# Per-listener lock to prevent priority races under concurrent deploys
_priority_locks: dict = {}
_priority_locks_lock = threading.Lock()


def _get_listener_lock(listener_arn: str) -> threading.Lock:
    with _priority_locks_lock:
        if listener_arn not in _priority_locks:
            _priority_locks[listener_arn] = threading.Lock()
        return _priority_locks[listener_arn]

class ALBClient:
    def __init__(self, session):
        self.client = session.client('elbv2')
        self.health_check_interval = int(os.environ.get('ALB_HEALTH_CHECK_INTERVAL', '15'))
        self.health_check_timeout = int(os.environ.get('ALB_HEALTH_CHECK_TIMEOUT', '5'))
        self.healthy_threshold = int(os.environ.get('ALB_HEALTHY_THRESHOLD', '2'))
        self.unhealthy_threshold = int(os.environ.get('ALB_UNHEALTHY_THRESHOLD', '3'))
    
    def create_target_group(self, name, vpc_id, port=80, tags=None, health_check_path='/'):
        try:
            response = self.client.create_target_group(
                Name=name,
                Protocol='HTTP',
                Port=port,
                VpcId=vpc_id,
                TargetType='ip',
                HealthCheckEnabled=True,
                HealthCheckPath=health_check_path,
                HealthCheckIntervalSeconds=self.health_check_interval,
                HealthCheckTimeoutSeconds=self.health_check_timeout,
                HealthyThresholdCount=self.healthy_threshold,
                UnhealthyThresholdCount=self.unhealthy_threshold,
                Matcher={'HttpCode': '200-499'},
                **({'Tags': as_key_value_tags(tags)} if tags else {}),
            )
            return response['TargetGroups'][0]['TargetGroupArn']
        except self.client.exceptions.DuplicateTargetGroupNameException:
            logger.warning(f"Target group {name} already exists, fetching ARN")
            response = self.client.describe_target_groups(Names=[name])
            tg = response['TargetGroups'][0]
            # If the existing TG is in a different VPC, it cannot be reused — create with unique suffix
            if tg['VpcId'] != vpc_id:
                logger.warning(f"Existing TG {name} is in VPC {tg['VpcId']}, not {vpc_id} — creating with unique name")
                import time
                unique_name = f"{name[:24]}-{int(time.time()) % 10000}"
                return self.create_target_group(unique_name, vpc_id, port, tags=tags, health_check_path=health_check_path)
            return tg['TargetGroupArn']

    def modify_target_group(self, target_group_arn, health_check_path):
        """Update an already-created target group's health check path in place — used
        when an app's routing mode changes (path -> host) after its target group already
        exists, so the ALB health check moves in lockstep with the nginx sidecar's own
        health location (container_config.HOST_MODE_HEALTH_CHECK_PATH) rather than
        continuing to probe a path the new config no longer serves a canned response at.
        """
        self.client.modify_target_group(
            TargetGroupArn=target_group_arn,
            HealthCheckPath=health_check_path,
        )
        logger.info(f"Updated health check path for {target_group_arn} to {health_check_path}")

    def _create_rule_with_retry(self, listener_arn, conditions, actions, tags=None):
        """Shared priority-assignment + PriorityInUseException retry, per listener lock.
        Used by every rule-creation method below so a path rule, a host-forward rule, and
        a host-redirect rule racing on the same listener never collide on priority."""
        with _get_listener_lock(listener_arn):
            priority = self.get_next_priority(listener_arn)
            try:
                response = self.client.create_rule(
                    ListenerArn=listener_arn, Conditions=conditions, Actions=actions,
                    Priority=priority, **({'Tags': as_key_value_tags(tags)} if tags else {}),
                )
                logger.info(f"Created listener rule with priority {priority}")
            except self.client.exceptions.PriorityInUseException:
                priority = self.get_next_priority(listener_arn)
                response = self.client.create_rule(
                    ListenerArn=listener_arn, Conditions=conditions, Actions=actions,
                    Priority=priority, **({'Tags': as_key_value_tags(tags)} if tags else {}),
                )
                logger.info(f"Created listener rule with priority {priority} (retry)")
        return response['Rules'][0]['RuleArn']

    def create_listener_rule(self, listener_arn, target_group_arn, path_pattern, priority, tags=None):
        """`path_pattern` accepts either a single glob string (legacy callers) or a list
        of exact/prefix patterns — see the R1 note on `_configure_alb_routing`'s caller:
        a bare `/{slug}*` glob matches any OTHER slug sharing that prefix (`/a*` matches
        `/ab/x`), so the deploy flow now always passes `[f"/{slug}", f"/{slug}/*"]`
        instead. ALB OR-matches multiple Values on one path-pattern condition."""
        import time
        patterns = path_pattern if isinstance(path_pattern, list) else [path_pattern]
        rule_arn = self._create_rule_with_retry(
            listener_arn,
            conditions=[{'Field': 'path-pattern', 'Values': patterns}],
            actions=[{'Type': 'forward', 'TargetGroupArn': target_group_arn}],
            tags=tags,
        )

        propagation_delay = int(os.environ.get('ALB_RULE_PROPAGATION_DELAY', '5'))
        logger.info(f"Waiting {propagation_delay} seconds for listener rule to propagate...")
        time.sleep(propagation_delay)
        return rule_arn

    def create_host_forward_rule(self, listener_arn, target_group_arn, hostname, tags=None):
        """F1b part 2: the 443 counterpart to create_listener_rule's path-pattern rule —
        an exact host-header match forwarding straight to the app's target group. Never a
        path-pattern condition: on 443 the whole path space belongs to whichever app's
        Host matched, unlike the shared :80 listener's /{app_name}/ prefix scheme."""
        return self._create_rule_with_retry(
            listener_arn,
            conditions=[{'Field': 'host-header', 'Values': [hostname]}],
            actions=[{'Type': 'forward', 'TargetGroupArn': target_group_arn}],
            tags=tags,
        )

    def create_host_redirect_rule(self, listener_arn, hostname, tags=None):
        """The :80 counterpart: a host-header match for this app's own hostname redirects
        to https, and never forwards — per the pre-review, a plaintext request to an app's
        dedicated hostname must never reach the backend over HTTP."""
        return self._create_rule_with_retry(
            listener_arn,
            conditions=[{'Field': 'host-header', 'Values': [hostname]}],
            actions=[{
                'Type': 'redirect',
                'RedirectConfig': {
                    'Protocol': 'HTTPS', 'Port': '443', 'StatusCode': 'HTTP_301',
                    'Host': '#{host}', 'Path': '/#{path}', 'Query': '#{query}',
                },
            }],
            tags=tags,
        )

    def ensure_host_redirect_rule(self, listener_arn, dns_label, base_domain, tags=None):
        """One per-infra wildcard :80 redirect (`*.{dns_label}.{base_domain}` -> https),
        not a per-app rule. ALB evaluates rules lowest-priority-number-first, and the
        existing per-app :80 path rules (`create_listener_rule`) always allocate from the
        lowest free priority (`get_next_priority`) — a per-app host-redirect rule created
        the normal way would therefore usually sit at a HIGHER priority number than an
        app's own path rule and lose to it. A request for
        `Host: a.{label}.{base}` + path `/a/x` would match app A's `/a*` path rule first,
        forward plaintext to A's target group, and reach A's nginx host-mode block over
        HTTP — exactly the "never forwards an app hostname" violation this rule exists to
        prevent.

        Reserving priority 1 for this ONE wildcard rule sidesteps the whole ordering
        problem instead of trying to out-allocate every path rule: it matches every app's
        host-mode hostname on this infra, is created (and priority-swapped into place)
        once, and is a plain idempotent lookup on every later deploy. This also keeps :80
        rule consumption to one rule per infra instead of one per app (ALB caps a
        listener's rule count).
        """
        wildcard_host = f"*.{dns_label}.{base_domain}"
        with _get_listener_lock(listener_arn):
            existing_rules = self.client.describe_rules(ListenerArn=listener_arn).get('Rules', [])
            for rule in existing_rules:
                for condition in rule.get('Conditions', []):
                    if condition.get('Field') == 'host-header' and wildcard_host in condition.get('Values', []):
                        # R1: a rule found already at priority 1 is the common case and
                        # needs nothing further. One NOT at priority 1 — a previous
                        # set_rule_priorities call that never ran or failed partway, or a
                        # later path rule created before this repair runs — must be
                        # reclaimed on every call, not only at creation, or it stays
                        # outranked by a path rule forever (the plaintext-forward
                        # violation this rule exists to prevent).
                        if rule.get('Priority') != '1':
                            self._reprioritize_to_one(listener_arn, rule['RuleArn'], existing_rules)
                        return rule['RuleArn']

            priority = self.get_next_priority(listener_arn)
            response = self.client.create_rule(
                ListenerArn=listener_arn,
                Conditions=[{'Field': 'host-header', 'Values': [wildcard_host]}],
                Actions=[{
                    'Type': 'redirect',
                    'RedirectConfig': {
                        'Protocol': 'HTTPS', 'Port': '443', 'StatusCode': 'HTTP_301',
                        'Host': '#{host}', 'Path': '/#{path}', 'Query': '#{query}',
                    },
                }],
                Priority=priority,
                **({'Tags': as_key_value_tags(tags)} if tags else {}),
            )
            new_rule_arn = response['Rules'][0]['RuleArn']

            if priority != 1:
                self._reprioritize_to_one(listener_arn, new_rule_arn, existing_rules)

        return new_rule_arn

    def _reprioritize_to_one(self, listener_arn, rule_arn, existing_rules):
        """Swap `rule_arn` into priority 1, displacing whatever currently holds it (if
        anything, and if it isn't `rule_arn` itself) to a freshly-allocated free priority.
        Called both right after creating the redirect rule and every time
        ensure_host_redirect_rule finds it already existing but not at 1 — see R1's note
        above on why this must be idempotent and repeatable, not just a one-shot swap at
        creation time."""
        rule_at_1 = next(
            (r for r in existing_rules if r.get('Priority') == '1' and r['RuleArn'] != rule_arn), None,
        )
        priorities = [{'RuleArn': rule_arn, 'Priority': 1}]
        if rule_at_1 is not None:
            # An atomic priority swap: the rule currently at 1 moves to a priority
            # get_next_priority guarantees free right now, so there is never a moment
            # both rules claim the same priority nor a moment priority 1 is unclaimed.
            displaced_priority = self.get_next_priority(listener_arn)
            priorities.append({'RuleArn': rule_at_1['RuleArn'], 'Priority': displaced_priority})
        self.client.set_rule_priorities(RulePriorities=priorities)
        logger.info(f"Reseated host-redirect rule {rule_arn} to priority 1 on {listener_arn}")

    def verify_target_group_attached(self, target_group_arn, listener_arn, max_retries=None, delay=None):
        """Verify target group is attached via listener rule"""
        if max_retries is None:
            max_retries = int(os.environ.get('ALB_VERIFY_MAX_RETRIES', '10'))
        if delay is None:
            delay = int(os.environ.get('ALB_VERIFY_DELAY', '2'))
        import time
        for attempt in range(max_retries):
            try:
                response = self.client.describe_rules(ListenerArn=listener_arn)
                for rule in response.get('Rules', []):
                    for action in rule.get('Actions', []):
                        if action.get('TargetGroupArn') == target_group_arn:
                            logger.info(f"Target group {target_group_arn} is attached via listener rule")
                            return True
                
                logger.warning(f"Target group not in listener rules yet, attempt {attempt + 1}/{max_retries}")
                time.sleep(delay)
            except Exception as e:
                logger.error(f"Error verifying target group: {e}")
                time.sleep(delay)
        raise Exception(f"Target group {target_group_arn} not attached to listener after {max_retries} attempts")
    
    def get_listener_arn(self, alb_arn, port=80):
        """ARN of the listener on `port`, or None.

        Selects by port rather than taking describe_listeners()[0]: the response order is
        not guaranteed, so the moment an ALB carries a second listener the old code could
        silently return the wrong one and every per-app path rule would be attached to it.
        Terraform creates the :80 listener, which is why that is the default.

        Deliberately returns None instead of falling back to the first listener — a
        fallback is exactly the behaviour that made this wrong, and a caller asking for a
        port that does not exist needs to hear so.
        """
        response = self.client.describe_listeners(LoadBalancerArn=alb_arn)
        for listener in response.get('Listeners', []):
            if listener.get('Port') == port:
                return listener['ListenerArn']
        return None
    
    def get_next_priority(self, listener_arn):
        response = self.client.describe_rules(ListenerArn=listener_arn)
        priorities = [int(rule['Priority']) for rule in response['Rules'] if rule['Priority'] != 'default']
        # Find first gap starting from 1 to avoid races with sequential max+1
        used = set(priorities)
        priority = 1
        while priority in used:
            priority += 1
        return priority

    def delete_rule(self, rule_arn):
        """Idempotent: a rule already gone (previous attempt partially succeeded, or a
        concurrent cleanup already removed it) is not an error — teardown must never wedge
        on a rule that simply isn't there anymore."""
        try:
            self.client.delete_rule(RuleArn=rule_arn)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "RuleNotFound":
                raise
            logger.info(f"Listener rule {rule_arn} already gone, nothing to delete")

    # ── custom-domain SNI certificate attach/detach (F1b part 3b) ──────────────────
    #
    # A per-app host-forward/redirect rule (above) routes traffic once a Host header
    # matches; a custom domain also needs its own certificate presented over TLS for that
    # Host, which is a property of the *listener*, not a rule. AddListenerCertificates
    # attaches an additional SNI certificate to the existing 443 listener without
    # replacing its default certificate (the platform wildcard cert from part 2) — this is
    # deliberately boto3, not Terraform: the set of attached certificates changes on every
    # custom-domain claim, and Terraform doesn't track resources it didn't create.

    def count_listener_certificates(self, listener_arn) -> int:
        """Count of SNI certificates on this listener, excluding its default certificate
        (IsDefault=True) — the default is the platform wildcard cert from Terraform, not a
        custom-domain attachment, and doesn't count against the SNI cap."""
        count = 0
        paginator_token = None
        while True:
            kwargs = {"ListenerArn": listener_arn}
            if paginator_token:
                kwargs["Marker"] = paginator_token
            response = self.client.describe_listener_certificates(**kwargs)
            count += sum(1 for c in response.get("Certificates", []) if not c.get("IsDefault"))
            paginator_token = response.get("NextMarker")
            if not paginator_token:
                return count

    def has_listener_certificate(self, listener_arn, cert_arn) -> bool:
        paginator_token = None
        while True:
            kwargs = {"ListenerArn": listener_arn}
            if paginator_token:
                kwargs["Marker"] = paginator_token
            response = self.client.describe_listener_certificates(**kwargs)
            if any(c.get("CertificateArn") == cert_arn for c in response.get("Certificates", [])):
                return True
            paginator_token = response.get("NextMarker")
            if not paginator_token:
                return False

    def add_listener_certificate(self, listener_arn, cert_arn):
        """Idempotent — AWS itself no-ops re-adding a certificate already on the
        listener, so no existence check is needed before calling. TooManyCertificates is
        AWS's own hard SNI-cap enforcement (25/listener) — belt-and-suspenders behind this
        module's own count_listener_certificates check, surfaced as a distinct exception
        so a race that slips past the count check still fails cleanly."""
        try:
            self.client.add_listener_certificates(
                ListenerArn=listener_arn, Certificates=[{"CertificateArn": cert_arn}],
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "TooManyCertificates":
                raise SniCertificateCapExceeded(listener_arn) from e
            raise

    def remove_listener_certificate(self, listener_arn, cert_arn):
        """Idempotent: RemoveListenerCertificates doesn't error on a certificate that
        isn't attached (AWS silently no-ops), so the only ClientError worth swallowing
        here is the listener itself being gone (a teardown race, e.g. infra destroy
        already removed the ALB) — there's nothing left to detach either way."""
        try:
            self.client.remove_listener_certificates(
                ListenerArn=listener_arn, Certificates=[{"CertificateArn": cert_arn}],
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "ListenerNotFound":
                raise
            logger.info(f"Listener {listener_arn} already gone, nothing to detach {cert_arn} from")
