#!/usr/bin/env python3
"""
Matter TRV Remote Sensing Monitor
Checks if TRVs still have remote temperature sensing enabled.
When a sensor becomes unavailable, TRVs may revert to internal sensing and "forget" bindings.
This script detects such issues and can optionally auto-repair them.
"""

import json
import sys
import argparse
from datetime import datetime
from websocket import create_connection

# --- CONFIGURATION ---
# Import from the main binding script
MATTER_URL = "ws://YOUR_HA_IP:5580/ws"
TEMPERATURE_CLUSTER_ID = 1026

# Same mapping structure as new_binding.py
SENSOR_TRV_MAPPING = [
    {
        "sensor_node": 9,
        "sensor_endpoint": 359,
        "trv_nodes": [20, 6, 7],
        "description": "Living Room Sensor -> Living Room TRVs"
    },
    { 
        "sensor_node": 9,
        "sensor_endpoint": 361,
        "trv_nodes": [8],
        "description": "Office Sensor -> Office TRVs"
    }
    # Add your other mappings here...
]
# --- END CONFIGURATION ---


class MatterClient:
    def __init__(self, url):
        self.ws = create_connection(url)
        self.message_id = 1
        server_info = self._receive_message()
        self.fabric_id = server_info.get("fabric_id")
        if not self.fabric_id:
            raise RuntimeError("Could not determine Fabric ID from server.")

    def _receive_message(self):
        response_raw = self.ws.recv()
        return json.loads(response_raw)

    def send_command(self, command, **kwargs):
        message = {"message_id": str(self.message_id), "command": command, "args": kwargs}
        self.ws.send(json.dumps(message))
        self.message_id += 1
        response = self._receive_message()

        if "error" in response:
            raise RuntimeError(f"Command failed: {response['error']['message']}")
        return response.get("result")

    def close(self):
        self.ws.close()


def check_trv_remote_sensing(client, trv_id):
    """Check if a TRV has remote sensing enabled (bit 0 of attribute 0x001A)."""
    try:
        remote_sense_attr = "1/513/26"  # Thermostat cluster 0x0201, attribute 0x001A
        result = client.send_command("read_attribute", node_id=trv_id, attribute_path=remote_sense_attr)
        remote_sensing = result.get(remote_sense_attr, 0)
        return {
            "trv_id": trv_id,
            "remote_sensing_value": remote_sensing,
            "remote_enabled": bool(remote_sensing & 1),
            "error": None
        }
    except Exception as e:
        return {
            "trv_id": trv_id,
            "remote_sensing_value": None,
            "remote_enabled": False,
            "error": str(e)
        }


def check_binding_exists(client, trv_id, expected_sensor_node, expected_sensor_endpoint):
    """Check if a TRV still has the expected binding."""
    try:
        binding_path = "1/30/0"
        result = client.send_command("read_attribute", node_id=trv_id, attribute_path=binding_path)
        bindings = result.get(binding_path, [])

        # Matter Binding format: "1"=node, "3"=endpoint, "4"=cluster
        for binding in bindings:
            if (binding.get("1") == expected_sensor_node and
                binding.get("3") == expected_sensor_endpoint and
                binding.get("4") == TEMPERATURE_CLUSTER_ID):
                return True
        return False
    except Exception:
        return False


def repair_trv(client, trv_id, sensor_node, sensor_endpoint, description):
    """Re-enable remote sensing and restore binding for a TRV."""
    try:
        print(f"  Repairing TRV {trv_id} ({description})...")

        # Restore binding
        binding_value = [{
            "node": sensor_node,
            "endpoint": sensor_endpoint,
            "cluster": TEMPERATURE_CLUSTER_ID,
            "fabricIndex": client.fabric_id
        }]
        client.send_command("write_attribute", node_id=trv_id, attribute_path="1/30/0", value=binding_value)

        # Re-enable remote sensing
        remote_sense_attr = "1/513/26"
        current_sensing = client.send_command("read_attribute", node_id=trv_id, attribute_path=remote_sense_attr).get(remote_sense_attr, 0)
        new_sensing_value = current_sensing | 1
        client.send_command("write_attribute", node_id=trv_id, attribute_path=remote_sense_attr, value=new_sensing_value)

        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Monitor Matter TRV remote sensing status")
    parser.add_argument("--auto-repair", action="store_true",
                       help="Automatically repair TRVs that lost remote sensing")
    parser.add_argument("--json", action="store_true",
                       help="Output results as JSON for Home Assistant parsing")
    parser.add_argument("--exit-code", action="store_true",
                       help="Exit with code 1 if any issues found (useful for HA binary sensors)")
    parser.add_argument("--output-file", type=str,
                       help="Write JSON results to file (for Home Assistant file sensor)")
    args = parser.parse_args()

    try:
        client = MatterClient(MATTER_URL)

        # Build TRV to sensor mapping
        trv_to_sensor = {}
        for mapping in SENSOR_TRV_MAPPING:
            for trv_id in mapping["trv_nodes"]:
                trv_to_sensor[trv_id] = {
                    "sensor_node": mapping["sensor_node"],
                    "sensor_endpoint": mapping["sensor_endpoint"],
                    "description": mapping.get("description", "")
                }

        results = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "total_trvs": len(trv_to_sensor),
            "healthy_trvs": 0,
            "unhealthy_trvs": 0,
            "repaired_trvs": 0,
            "failed_repairs": 0,
            "details": []
        }

        for trv_id, sensor_info in trv_to_sensor.items():
            sensor_node = sensor_info["sensor_node"]
            sensor_endpoint = sensor_info["sensor_endpoint"]
            desc = sensor_info["description"]

            # Check remote sensing status
            sensing_status = check_trv_remote_sensing(client, trv_id)
            binding_exists = check_binding_exists(client, trv_id, sensor_node, sensor_endpoint)

            is_healthy = sensing_status["remote_enabled"] and binding_exists

            trv_result = {
                "trv_id": trv_id,
                "description": desc,
                "remote_sensing_enabled": sensing_status["remote_enabled"],
                "remote_sensing_value": sensing_status["remote_sensing_value"],
                "binding_exists": binding_exists,
                "healthy": is_healthy,
                "error": sensing_status["error"]
            }

            # Auto-repair if requested and unhealthy
            if args.auto_repair and not is_healthy and not sensing_status["error"]:
                repair_result = repair_trv(client, trv_id, sensor_node, sensor_endpoint, desc)
                trv_result["repair_attempted"] = True
                trv_result["repair_success"] = repair_result["success"]
                trv_result["repair_error"] = repair_result["error"]

                if repair_result["success"]:
                    results["repaired_trvs"] += 1
                    # Re-check after repair
                    sensing_status = check_trv_remote_sensing(client, trv_id)
                    trv_result["healthy"] = sensing_status["remote_enabled"]
                else:
                    results["failed_repairs"] += 1

            if trv_result["healthy"]:
                results["healthy_trvs"] += 1
            else:
                results["unhealthy_trvs"] += 1

            results["details"].append(trv_result)

        client.close()

        # Write to file if requested (for Home Assistant)
        # HA file sensor only reads last line, so output as single-line JSON
        if args.output_file:
            try:
                with open(args.output_file, 'w') as f:
                    json.dump(results, f, separators=(',', ':'))  # Compact JSON, no whitespace
            except Exception as e:
                print(f"ERROR writing to file: {e}", file=sys.stderr)

        # Output results to stdout
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(f"=== Matter TRV Remote Sensing Monitor ===")
            print(f"Time: {results['timestamp']}")
            print(f"Total TRVs: {results['total_trvs']}")
            print(f"Healthy: {results['healthy_trvs']}")
            print(f"Unhealthy: {results['unhealthy_trvs']}")

            if args.auto_repair:
                print(f"Repaired: {results['repaired_trvs']}")
                print(f"Failed repairs: {results['failed_repairs']}")

            print("\nDetails:")
            for trv in results["details"]:
                status = "✓ OK" if trv["healthy"] else "✗ ISSUE"
                print(f"  TRV {trv['trv_id']}: {status}")
                if not trv["healthy"]:
                    print(f"    Remote sensing: {trv['remote_sensing_enabled']}")
                    print(f"    Binding exists: {trv['binding_exists']}")
                    if trv.get("repair_attempted"):
                        print(f"    Repair: {'Success' if trv['repair_success'] else 'Failed'}")

        # Exit with appropriate code
        if args.exit_code and results["unhealthy_trvs"] > 0:
            sys.exit(1)
        else:
            sys.exit(0)

    except Exception as e:
        error_result = {
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        if args.json:
            print(json.dumps(error_result))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
