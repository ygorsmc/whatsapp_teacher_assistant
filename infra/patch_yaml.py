import yaml

with open('worker-llm.yaml', 'r') as f:
    data = yaml.safe_load(f)

data['properties']['template']['scale']['minReplicas'] = 0
data['properties']['template']['scale']['maxReplicas'] = 10
data['properties']['template']['scale']['rules'] = [
    {
        "name": "inbound-messages-rule",
        "custom": {
            "type": "azure-servicebus",
            "metadata": {
                "queueName": "inbound_messages",
                "messageCount": "1",
                "namespace": "whatsapp-bot-sb-bdn4p6wsfyc3i"
            },
            "identity": "system"
        }
    },
    {
        "name": "command-messages-rule",
        "custom": {
            "type": "azure-servicebus",
            "metadata": {
                "queueName": "command_messages",
                "messageCount": "1",
                "namespace": "whatsapp-bot-sb-bdn4p6wsfyc3i"
            },
            "identity": "system"
        }
    }
]

with open('worker-llm-patched.yaml', 'w') as f:
    yaml.dump(data, f)
