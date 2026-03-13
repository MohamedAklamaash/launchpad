from rest_framework import serializers
from django.core.validators import URLValidator, RegexValidator
import re

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
    port = serializers.IntegerField(min_value=1024, max_value=65535, default=8080)
    
    alloted_cpu = serializers.FloatField(min_value=0.25, max_value=4.0, default=0.25)
    alloted_memory = serializers.FloatField(min_value=0.5, max_value=30.0, default=0.5)
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
            if not re.match(r'^[A-Z_][A-Z0-9_]*$', key):
                raise serializers.ValidationError(f"Invalid environment variable name: {key}")
        
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        cpu = data.get('alloted_cpu', 0.25)
        memory = data.get('alloted_memory', 0.5)
        
        # Validate Fargate CPU/Memory combinations
        valid_combinations = {
            0.25: (0.5, 2.0),
            0.5: (1.0, 4.0),
            1.0: (2.0, 8.0),
            2.0: (4.0, 16.0),
            4.0: (8.0, 30.0)
        }
        
        if cpu not in valid_combinations:
            raise serializers.ValidationError(
                f"Invalid CPU value. Must be one of: {list(valid_combinations.keys())}"
            )
        
        min_mem, max_mem = valid_combinations[cpu]
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
