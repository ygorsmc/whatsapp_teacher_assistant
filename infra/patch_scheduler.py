import yaml

with open('worker-llm.yaml', 'r') as f:
    llm_data = yaml.safe_load(f)

with open('worker-scheduler.yaml', 'r') as f:
    sched_data = yaml.safe_load(f)

# Copy identity
sched_data['identity'] = {
    'type': 'SystemAssigned'
}

# Copy envs (replace WORKER_TYPE)
envs = []
for env in llm_data['properties']['template']['containers'][0]['env']:
    if env['name'] == 'WORKER_TYPE':
        envs.append({'name': 'WORKER_TYPE', 'value': 'scheduler'})
    else:
        envs.append(env)

sched_data['properties']['template']['containers'][0]['env'] = envs

with open('worker-scheduler-patched.yaml', 'w') as f:
    yaml.dump(sched_data, f)
