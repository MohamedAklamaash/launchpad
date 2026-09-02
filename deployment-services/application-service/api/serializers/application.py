import re

from django.conf import settings
from django.core.validators import RegexValidator, URLValidator
from rest_framework import serializers
from shared.enums.orchestrator import ComputeType

FARGATE_CPU_MEMORY = {
    0.25: (0.5, 2.0),
    0.5: (1.0, 4.0),
    1.0: (2.0, 8.0),
    2.0: (4.0, 16.0),
    4.0: (8.0, 30.0),
}

FARGATE_MAX_CPU = max(FARGATE_CPU_MEMORY)
FARGATE_MAX_MEMORY = max(hi for _lo, hi in FARGATE_CPU_MEMORY.values())

EKS_MAX_APP_CPU = settings.EKS_MAX_APP_CPU
EKS_MAX_APP_MEMORY = settings.EKS_MAX_APP_MEMORY

# DNS label validator (for application names)
dns_label_validator = RegexValidator(
    regex=r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$',
    message='Name must be lowercase alphanumeric with hyphens, start and end with alphanumeric'
)

class ApplicationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(
        max_length=63,
        validators=[dns_label_validator],
        help_text='Application name (DNS-compatible)'
    )
    description = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True
    )
    infrastructure_id = serializers.UUIDField(required=True)
    
    project_remote_url = serializers.URLField(
        max_length=255,
        validators=[URLValidator(schemes=['http', 'https'])],
        help_text='GitHub repository URL'
    )
    project_branch = serializers.CharField(max_length=255, default='main')
    project_commit_hash = serializers.CharField(max_length=40, required=False, allow_blank=True)
    
    dockerfile_path = serializers.CharField(max_length=255, default='Dockerfile')
    build_context = serializers.CharField(max_length=255, required=False, allow_blank=True, default='',
        help_text='Build context path relative to repo root. Defaults to Dockerfile directory. Use for monorepos.')
    port = serializers.IntegerField(min_value=1024, max_value=65535, default=8080)
    
    # Bounds here admit the widest compute type. The Fargate ladder (ECS) and the EKS
    # ceiling are applied in validate(), which knows the infrastructure's compute_type —
    # a 4.0/30.0 field bound would reject a legal Kubernetes request before it ran.
    alloted_cpu = serializers.FloatField(min_value=0.25, max_value=EKS_MAX_APP_CPU, default=0.25)
    alloted_memory = serializers.FloatField(min_value=0.5, max_value=EKS_MAX_APP_MEMORY, default=0.5)
    alloted_storage = serializers.FloatField(min_value=0.0, max_value=200.0, default=0.0)
    
    envs = serializers.JSONField(required=False, default=dict)
    
    def validate_name(self, value):
        """Validate application name"""
        if len(value) < 3:
            raise serializers.ValidationError("Name must be at least 3 characters")
        if len(value) > 63:
            raise serializers.ValidationError("Name must be at most 63 characters")
        return value.lower()
    
    def validate_project_remote_url(self, value):
        """Validate GitHub URL"""
        if 'github.com' not in value.lower():
            raise serializers.ValidationError("Only GitHub repositories are supported")
        return value
    
    def validate_envs(self, value):
        """Validate environment variables"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Environment variables must be a dictionary")
        
        # Check size
        import json
        if len(json.dumps(value)) > 4096:
            raise serializers.ValidationError("Environment variables too large (max 4KB)")
        
        # Check for reserved keys
        reserved = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'PORT']
        for key in value:
            if key in reserved:
                raise serializers.ValidationError(f"Cannot set reserved variable: {key}")
            
            # Validate key format
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
                raise serializers.ValidationError(f"Invalid environment variable name: {key}")
        
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        cpu = data.get('alloted_cpu', 0.25)
        memory = data.get('alloted_memory', 0.5)

        # Kubernetes accepts any positive request up to the platform's per-app ceiling;
        # only Fargate has a fixed CPU/memory matrix.
        if self.context.get('compute_type') == ComputeType.EKS:
            if cpu <= 0 or memory <= 0:
                raise serializers.ValidationError("CPU and memory must be greater than zero")
            return data

        if cpu > FARGATE_MAX_CPU or memory > FARGATE_MAX_MEMORY:
            raise serializers.ValidationError(
                f"ECS Fargate applications are limited to {FARGATE_MAX_CPU} vCPU and "
                f"{FARGATE_MAX_MEMORY}GB. Use a Kubernetes infrastructure for larger apps."
            )

        if cpu not in FARGATE_CPU_MEMORY:
            raise serializers.ValidationError(
                f"Invalid CPU value. Must be one of: {list(FARGATE_CPU_MEMORY.keys())}"
            )

        min_mem, max_mem = FARGATE_CPU_MEMORY[cpu]
        if not (min_mem <= memory <= max_mem):
            raise serializers.ValidationError(
                f"For {cpu} vCPU, memory must be between {min_mem}GB and {max_mem}GB"
            )

        return data


class ApplicationUpdateSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=500, required=False)
    envs = serializers.JSONField(required=False)
    
    def validate_envs(self, value):
        """Validate environment variables"""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Environment variables must be a dictionary")
        
        import json
        if len(json.dumps(value)) > 4096:
            raise serializers.ValidationError("Environment variables too large (max 4KB)")
        
        reserved = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN']
        for key in value:
            if key in reserved:
                raise serializers.ValidationError(f"Cannot set reserved variable: {key}")
        
        return value
