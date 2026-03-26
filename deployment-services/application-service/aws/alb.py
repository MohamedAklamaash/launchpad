import logging
import os

logger = logging.getLogger(__name__)

class ALBClient:
    def __init__(self, session):
        self.client = session.client('elbv2')
        self.health_check_interval = int(os.environ.get('ALB_HEALTH_CHECK_INTERVAL', '30'))
        self.health_check_timeout = int(os.environ.get('ALB_HEALTH_CHECK_TIMEOUT', '10'))
        self.healthy_threshold = int(os.environ.get('ALB_HEALTHY_THRESHOLD', '2'))
        self.unhealthy_threshold = int(os.environ.get('ALB_UNHEALTHY_THRESHOLD', '5'))
    
    def create_target_group(self, name, vpc_id, port=80):
        try:
            response = self.client.create_target_group(
                Name=name,
                Protocol='HTTP',
                Port=port,
                VpcId=vpc_id,
                TargetType='ip',
                HealthCheckEnabled=True,
                HealthCheckPath='/',
                HealthCheckIntervalSeconds=self.health_check_interval,
                HealthCheckTimeoutSeconds=self.health_check_timeout,
                HealthyThresholdCount=self.healthy_threshold,
                UnhealthyThresholdCount=self.unhealthy_threshold,
                Matcher={'HttpCode': '200-499'}
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
                return self.create_target_group(unique_name, vpc_id, port)
            return tg['TargetGroupArn']
    
    def create_listener_rule(self, listener_arn, target_group_arn, path_pattern, priority):
        import time
        try:
            response = self.client.create_rule(
                ListenerArn=listener_arn,
                Conditions=[{
                    'Field': 'path-pattern',
                    'Values': [path_pattern]
                }],
                Actions=[{
                    'Type': 'forward',
                    'TargetGroupArn': target_group_arn
                }],
                Priority=priority
            )
            logger.info(f"Created listener rule with priority {priority}")
            rule_arn = response['Rules'][0]['RuleArn']
        except self.client.exceptions.PriorityInUseException:
            logger.warning(f"Priority {priority} already in use, checking for existing rule")
            response = self.client.describe_rules(ListenerArn=listener_arn)
            for rule in response['Rules']:
                if rule.get('Priority') == str(priority):
                    rule_arn = rule['RuleArn']
                    self.client.modify_rule(
                        RuleArn=rule_arn,
                        Actions=[{'Type': 'forward', 'TargetGroupArn': target_group_arn}]
                    )
                    logger.info(f"Updated existing listener rule {rule_arn}")
                    break
            else:
                new_priority = self.get_next_priority(listener_arn)
                return self.create_listener_rule(listener_arn, target_group_arn, path_pattern, new_priority)
        
        propagation_delay = int(os.environ.get('ALB_RULE_PROPAGATION_DELAY', '5'))
        logger.info(f"Waiting {propagation_delay} seconds for listener rule to propagate...")
        time.sleep(propagation_delay)
        
        return rule_arn
    
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
    
    def get_listener_arn(self, alb_arn):
        response = self.client.describe_listeners(LoadBalancerArn=alb_arn)
        if response['Listeners']:
            return response['Listeners'][0]['ListenerArn']
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
