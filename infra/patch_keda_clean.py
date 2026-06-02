import yaml

with open('worker-llm-keda-patch.yaml', 'r') as f:
    data = yaml.safe_load(f)

# Change polling interval to 15
data['properties']['template']['scale']['pollingInterval'] = 15

with open('worker-llm-keda-patch.yaml', 'w') as f:
    yaml.dump(data, f)
