import os
import docker

def execute_in_sandbox(script_name: str):
    client = docker.from_env()
    abs_path = os.path.abspath(script_name)
    image_name = "python:3.10-slim"

    # Ensure the base image exists locally before running
    try:
        client.images.get(image_name)
    except docker.errors.ImageNotFound:
        print(f"Image {image_name} not found locally. Pulling image...")
        client.images.pull(image_name)

    env_vars = {
        "SOURCE_DB_URL": "postgresql://postgres:devpass@host.docker.internal:5433/testdb",
        "DEST_DB_URL": "postgresql://postgres:devpass@host.docker.internal:5433/testdb",
    }

    print(f"Spinning up ephemeral sandbox for {script_name}...")
    
    setup_and_run_cmd = (
        '/bin/bash -c "pip install --quiet --disable-pip-version-check --no-warn-script-location '
        'dlt psycopg2-binary sqlalchemy && python /app/pipeline.py"'
    )

    try:
        container = client.containers.run(
            image=image_name,
            command=setup_and_run_cmd,
            volumes={abs_path: {'bind': '/app/pipeline.py', 'mode': 'ro'}},
            environment=env_vars,
            extra_hosts={"host.docker.internal": "host-gateway"},
            detach=True
        )

        result = container.wait()
        logs = container.logs().decode('utf-8')
        container.remove()

        if result['StatusCode'] == 0:
            print("\nSandbox Execution Successful!")
            print(logs.strip())
        else:
            print(f"\nSandbox Execution Failed (Exit Code: {result['StatusCode']})")
            print("--- Stderr Logs for Auto-Healing ---")
            print(logs.strip())

    except Exception as e:
        print(f"Sandbox container error: {e}")

if __name__ == "__main__":
    execute_in_sandbox("generated_pipeline.py")