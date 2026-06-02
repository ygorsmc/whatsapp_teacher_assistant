import yaml

with open('worker-llm-keda-patch.yaml', 'r') as f:
    data = yaml.safe_load(f)

rules = data['properties']['template']['scale'].get('rules', [])
for rule in rules:
    if 'auth' in rule:
        auth_array = rule.pop('auth')
        if 'custom' in rule:
            rule['custom']['auth'] = auth_array

data['properties']['template']['scale']['rules'] = rules

with open('worker-llm-keda-patch2.yaml', 'w') as f:
    yaml.dump(data, f)
