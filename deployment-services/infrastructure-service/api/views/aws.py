from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

AWS_REGIONS = [
    {"value": "us-east-1",      "label": "US East (N. Virginia)"},
    {"value": "us-east-2",      "label": "US East (Ohio)"},
    {"value": "us-west-1",      "label": "US West (N. California)"},
    {"value": "us-west-2",      "label": "US West (Oregon)"},
    {"value": "af-south-1",     "label": "Africa (Cape Town)"},
    {"value": "ap-east-1",      "label": "Asia Pacific (Hong Kong)"},
    {"value": "ap-south-1",     "label": "Asia Pacific (Mumbai)"},
    {"value": "ap-south-2",     "label": "Asia Pacific (Hyderabad)"},
    {"value": "ap-northeast-1", "label": "Asia Pacific (Tokyo)"},
    {"value": "ap-northeast-2", "label": "Asia Pacific (Seoul)"},
    {"value": "ap-northeast-3", "label": "Asia Pacific (Osaka)"},
    {"value": "ap-southeast-1", "label": "Asia Pacific (Singapore)"},
    {"value": "ap-southeast-2", "label": "Asia Pacific (Sydney)"},
    {"value": "ap-southeast-3", "label": "Asia Pacific (Jakarta)"},
    {"value": "ap-southeast-4", "label": "Asia Pacific (Melbourne)"},
    {"value": "ca-central-1",   "label": "Canada (Central)"},
    {"value": "ca-west-1",      "label": "Canada West (Calgary)"},
    {"value": "eu-central-1",   "label": "Europe (Frankfurt)"},
    {"value": "eu-central-2",   "label": "Europe (Zurich)"},
    {"value": "eu-west-1",      "label": "Europe (Ireland)"},
    {"value": "eu-west-2",      "label": "Europe (London)"},
    {"value": "eu-west-3",      "label": "Europe (Paris)"},
    {"value": "eu-north-1",     "label": "Europe (Stockholm)"},
    {"value": "eu-south-1",     "label": "Europe (Milan)"},
    {"value": "eu-south-2",     "label": "Europe (Spain)"},
    {"value": "il-central-1",   "label": "Israel (Tel Aviv)"},
    {"value": "me-central-1",   "label": "Middle East (UAE)"},
    {"value": "me-south-1",     "label": "Middle East (Bahrain)"},
    {"value": "sa-east-1",      "label": "South America (São Paulo)"},
]


@api_view(['GET'])
@permission_classes([AllowAny])
def list_aws_regions(request):
    return Response(AWS_REGIONS)
