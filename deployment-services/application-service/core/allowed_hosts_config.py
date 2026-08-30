"""
Dynamic ALLOWED_HOSTS configuration utility.
Reads from environment variables only, no hardcoded service names.
Supports wildcards, IP addresses, and domain patterns.
"""

import os


def get_allowed_hosts():
    """
    Get ALLOWED_HOSTS from environment variables.
    
    Format:
    - ALLOWED_HOSTS: comma-separated list (e.g., 'localhost,127.0.0.1,*.example.com,192.168.*')
    - ALLOWED_HOSTS_ENABLE_WILDCARD: 'true' to enable '*' wildcard for development
    
    Returns:
        list: List of allowed hosts
    """
    
    # Base hosts from environment
    hosts_env = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1')
    allowed_hosts = [host.strip() for host in hosts_env.split(',') if host.strip()]
    
    # Enable wildcard for development if explicitly set
    if os.environ.get('ALLOWED_HOSTS_ENABLE_WILDCARD', '').lower() == 'true' and '*' not in allowed_hosts:
        allowed_hosts.insert(0, '*')
    
    return allowed_hosts
