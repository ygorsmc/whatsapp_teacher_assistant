import yaml
import sys

with open('worker-llm-keda-patch.yaml', 'r') as f:
    data = yaml.safe_load(f)

# Add secret
secrets = data['properties']['configuration'].get('secrets', [])
secrets.append({
    'name': 'sb-connection-string',
    'value': 'Endpoint=sb://whatsapp-bot-sb-bdn4p6wsfyc3i.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=ux+ydsNIHejiDJDv1ECeVOZ3Ou7gOQSWo+ASbEat2CE='
})
data['properties']['configuration']['secrets'] = secrets

# Add env var
envs = data['properties']['template']['containers'][0].get('env', [])
envs.append({
    'name': 'SB_CONNECTION_STRING',
    'secretRef': 'sb-connection-string'
})
data['properties']['template']['containers'][0]['env'] = envs

# Update rules
rules = data['properties']['template']['scale'].get('rules', [])
for rule in rules:
    if rule['custom']['type'] == 'azure-servicebus':
        if 'identity' in rule['custom']:
            del rule['custom']['identity']
        
        # In Azure Container Apps, we MUST use 'auth' section for KEDA secrets
        auth = rule.get('auth', [])
        # Remove any existing connection auth just in case
        auth = [a for a in auth if a.get('triggerParameter') != 'connection']
        
        auth.append({
            'secretRef': 'sb-connection-string',
            'triggerParameter': 'connection'
        })
        rule['auth'] = auth

data['properties']['template']['scale']['rules'] = rules

with open('worker-llm-keda-patch.yaml', 'w') as f:
    yaml.dump(data, f)
