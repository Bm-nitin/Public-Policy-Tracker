import json
import os

_cached_policies = None

def load_policies():
    global _cached_policies

    if _cached_policies is not None:
        return _cached_policies

    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.normpath(os.path.join(base_dir, '..', 'data'))

        if not os.path.exists(data_dir):
            print("Data folder not found:", data_dir)
            return []

        all_policies = []

        for filename in os.listdir(data_dir):
            if filename.endswith('.json'):
                file_path = os.path.join(data_dir, filename)

                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception as e:
                    print(f"Error reading {filename}: {e}")
                    continue

                if not isinstance(data, list):
                    continue

                # 🔥 IMPORTANT: assign sector from filename
                sector_name = filename.replace(".json", "").lower()

                for policy in data:
                    policy.setdefault('name', 'Unknown Policy')
                    policy.setdefault('category', 'Unknown')
                    policy.setdefault('sub_category', 'General')
                    policy.setdefault('change', 'No change info')
                    policy.setdefault('impact', 'No impact info')

                    policy["sector"] = sector_name   # ✅ KEY FIX

                all_policies.extend(data)

        print(f"Loaded {len(all_policies)} policies")

        _cached_policies = all_policies
        return all_policies

    except Exception as e:
        print("Error loading policies:", str(e))
        return []