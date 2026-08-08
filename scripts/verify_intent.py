import json
import os

from jsonschema import validate


def verify_intent():
    # Get the project root directory (one level up from scripts/)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base_path = os.path.join(project_root, "core", "intent")
    schema_path = os.path.join(base_path, "schema.json")
    
    with open(schema_path, 'r') as f:
        schema = json.load(f)
    
    files_to_verify = [
        "manifesto.json",
        "charter.json",
        "work_order.json"
    ]
    
    all_passed = True
    
    for file_name in files_to_verify:
        file_path = os.path.join(base_path, file_name)
        print(f"Verifying {file_name}...")
        
        try:
            with open(file_path, 'r') as f:
                instance = json.load(f)
            
            validate(instance=instance, schema=schema)
            print(f"  ✅ {file_name} is valid.")
        except Exception as e:
            print(f"  ❌ {file_name} is INVALID: {e}")
            all_passed = False
            
    if all_passed:
        print("\n✨ All intent instances are valid against the schema!")
        return True
    else:
        print("\n⚠️ Some instances failed validation.")
        return False

if __name__ == "__main__":
    if verify_intent():
        exit(0)
    else:
        exit(1)