import os
import tempfile
import json

print("=== DryRun Sandbox Test ===")

# Check the execution identity
print("User ID:", os.getuid())

# Check current working directory
print("Working directory:", os.getcwd())

# Test temporary file creation
test_file = "/tmp/dryrun_test.txt"

with open(test_file, "w") as f:
    f.write("DryRun sandbox is working!")

print("Temporary file created:", os.path.exists(test_file))

# Produce structured output
result = {
    "status": "success",
    "user_id": os.getuid(),
    "temporary_file_created": True
}

print("RESULT_JSON:", json.dumps(result))
