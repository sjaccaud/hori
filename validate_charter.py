import json
from jsonschema import validate
from jsonschema.exceptions import ValidationError

# Load the schema
with open('core/intent/schema.json', 'r') as schema_file:
    schema = json.load(schema_file)

# Load the charter JSON file
with open('core/intent/charter.json', 'r') as charter_file:
    charter = json.load(charter_file)

# Validate the charter against the schema
try:
    validate(instance=charter, schema=schema)
    print("Validation successful: charter.json is valid against schema.json")
except ValidationError as ve:
    print("Validation failed:", ve)