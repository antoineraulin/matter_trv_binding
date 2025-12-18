import json
import time
import argparse
from websocket import create_connection

# --- CONFIGURATION ---
# The WebSocket URL for your Home Assistant Matter Server add-on.
MATTER_URL = "ws://YOUR_HA_IP:5580/ws"

# Temperature Measurement cluster ID (standard Matter cluster)
TEMPERATURE_CLUSTER_ID = 1026

# Sensor-to-TRV Mapping Configuration
# This structure allows multiple sensors (even on the same node) to bind to different TRVs
# Format: Each entry maps a sensor (node_id, endpoint_id) to a list of TRV node IDs
#
# Example for a Home-Assistant-Matter-Hub (node 9) with two sensors on different endpoints:
SENSOR_TRV_MAPPING = [
    {
        "sensor_node": 9,
        "sensor_endpoint": 359,
        "trv_nodes": [20, 6, 7],  # First sensor binds to these TRVs
        "description": "Living Room Sensor -> Living Room TRVs"
    },
    {
        "sensor_node": 9,
        "sensor_endpoint": 361,
        "trv_nodes": [8],
        "description": "Office Sensor -> Office TRV"
    }
    # Uncomment and configure additional mappings as needed:
    # {
    #     "sensor_node": 9,
    #     "sensor_endpoint": 360,  # Different endpoint on same hub
    #     "trv_nodes": [8, 9],      # Second sensor binds to different TRVs
    #     "description": "Bedroom Sensor -> Bedroom TRVs"
    # },
    # {
    #     "sensor_node": 10,         # Different node entirely
    #     "sensor_endpoint": 1,
    #     "trv_nodes": [10],
    #     "description": "Kitchen Sensor -> Kitchen TRV"
    # },
]

# Endpoints to Clean Up (Optional)
# List old/unused endpoints here to automatically remove their ACLs
# Format: {node_id: [list of endpoint IDs to remove]}
# This helps reduce ACL count when you've moved sensors to different endpoints
ENDPOINTS_TO_CLEANUP = {
    9: [358],  # Remove old ACL for endpoint 358 on node 9
    # Add more as needed:
    # 9: [358, 357],  # Remove multiple old endpoints
    # 10: [100],      # Cleanup on different node
}
# --- END CONFIGURATION ---


# --- SCRIPT LOGIC (No need to edit below this line) ---
class AnsiColors:
    SUCCESS, WARNING, FAIL, INFO, DEBUG, BOLD, ENDC = '\033[92m', '\033[93m', '\033[91m', '\033[94m', '\033[90m', '\033[1m', '\033[0m'

def print_info(message): print(f"{AnsiColors.INFO}INFO: {message}{AnsiColors.ENDC}")
def print_success(message): print(f"{AnsiColors.SUCCESS}SUCCESS: {message}{AnsiColors.ENDC}")
def print_fail(message): print(f"{AnsiColors.FAIL}FAILED: {message}{AnsiColors.ENDC}")
def print_header(message): print(f"\n{AnsiColors.BOLD}{'='*10} {message} {'='*10}{AnsiColors.ENDC}")
def print_debug(direction, data): print(f"{AnsiColors.DEBUG}[DEBUG - {direction}]{AnsiColors.ENDC}\n{json.dumps(data, indent=2)}")

class MatterClient:
    def __init__(self, url, debug=False):
        self.debug = debug
        self.ws = create_connection(url)
        self.message_id = 1
        server_info = self._receive_message()
        self.fabric_id = server_info.get("fabric_id")
        if not self.fabric_id: raise RuntimeError("Could not determine Fabric ID from server.")
        print_success(f"Connected to Matter Server. Fabric ID: {self.fabric_id}")

    def _receive_message(self):
        response_raw = self.ws.recv()
        response = json.loads(response_raw)
        if self.debug: print_debug("RECV", response)
        return response
    
    def send_command(self, command, **kwargs):
        message = {"message_id": str(self.message_id), "command": command, "args": kwargs}
        if self.debug: print_debug("SEND", message)
        self.ws.send(json.dumps(message))
        self.message_id += 1
        response = self._receive_message()

        if "error" in response: raise RuntimeError(f"Command failed: {response['error']['message']}")
        result_list = response.get("result")
        if isinstance(result_list, list):
            for item in result_list:
                if isinstance(item, dict) and item.get("Status", 0) != 0:
                    status_code = item.get("Status")
                    raise RuntimeError(f"Command failed with non-zero status: {status_code} on path {item.get('Path')}")
        return response.get("result")

    def close(self): self.ws.close()

def main(debug_mode):
    client = MatterClient(MATTER_URL, debug=debug_mode)
    try:
        # Validate configuration
        if not SENSOR_TRV_MAPPING:
            raise ValueError("SENSOR_TRV_MAPPING is empty. Please configure at least one sensor-to-TRV mapping.")

        # Print configuration summary
        print_header("Configuration Summary")
        for idx, mapping in enumerate(SENSOR_TRV_MAPPING, 1):
            desc = mapping.get("description", "No description")
            print_info(f"Mapping {idx}: {desc}")
            print_info(f"  Sensor: Node {mapping['sensor_node']}, Endpoint {mapping['sensor_endpoint']}")
            print_info(f"  TRVs: {mapping['trv_nodes']}")

        # Group mappings by sensor node to handle multiple endpoints on same node
        sensor_nodes_to_process = {}
        for mapping in SENSOR_TRV_MAPPING:
            node_id = mapping["sensor_node"]
            if node_id not in sensor_nodes_to_process:
                sensor_nodes_to_process[node_id] = []
            sensor_nodes_to_process[node_id].append(mapping)

        # Step 1: Rebuild Sensor ACLs (per node, preserving all endpoints)
        print_header("Step 1: Rebuilding Sensor ACLs")

        for sensor_node, mappings in sensor_nodes_to_process.items():
            print_info(f"Processing Sensor Node {sensor_node} with {len(mappings)} endpoint(s)...")

            # Read existing ACLs
            existing_acls = client.send_command("read_attribute", node_id=sensor_node, attribute_path="0/31/0").get("0/31/0", [])
            print_info(f"Found {len(existing_acls)} existing ACL entries on node {sensor_node}")

            # Build list of all endpoints to filter out (managed + cleanup)
            managed_endpoints = {m["sensor_endpoint"] for m in mappings}
            cleanup_endpoints = set(ENDPOINTS_TO_CLEANUP.get(sensor_node, []))
            all_endpoints_to_remove = managed_endpoints | cleanup_endpoints

            if cleanup_endpoints:
                print_info(f"Will clean up old endpoints: {sorted(cleanup_endpoints)}")

            # Filter out ACLs for:
            # 1. Managed endpoints (will be recreated with current config)
            # 2. Cleanup endpoints (old/unused, explicitly marked for removal)
            filtered_acls = []

            for acl in existing_acls:
                if "4" not in acl:
                    continue

                targets = acl.get("4")
                should_remove_acl = False

                if isinstance(targets, list):
                    for t in targets:
                        if isinstance(t, dict):
                            # Check if this ACL targets any endpoint we're removing
                            if (t.get("0") == TEMPERATURE_CLUSTER_ID and
                                t.get("1") in all_endpoints_to_remove):
                                should_remove_acl = True
                                break

                # Keep ACLs that don't target endpoints we're managing or cleaning up
                if not should_remove_acl:
                    filtered_acls.append(acl)

            removed_count = len(existing_acls) - len(filtered_acls) - len([acl for acl in existing_acls if "4" not in acl])
            print_info(f"Preserved {len(filtered_acls)} ACL entries, removed {removed_count} old temperature sensor ACLs")

            # Group mappings by TRV nodes to consolidate ACL entries
            # This reduces ACL count by combining endpoints with the same TRV list
            trv_to_endpoints = {}
            for mapping in mappings:
                endpoint = mapping["sensor_endpoint"]
                trv_tuple = tuple(sorted(mapping["trv_nodes"]))  # Use tuple as dict key

                if trv_tuple not in trv_to_endpoints:
                    trv_to_endpoints[trv_tuple] = {
                        "trv_nodes": mapping["trv_nodes"],
                        "endpoints": [],
                        "descriptions": []
                    }

                trv_to_endpoints[trv_tuple]["endpoints"].append(endpoint)
                trv_to_endpoints[trv_tuple]["descriptions"].append(mapping.get("description", f"Endpoint {endpoint}"))

            # Create consolidated ACL entries
            new_acl_entries = []
            for trv_tuple, group_data in trv_to_endpoints.items():
                trv_nodes = group_data["trv_nodes"]
                endpoints = group_data["endpoints"]
                descriptions = group_data["descriptions"]

                # Create targets list for all endpoints in this group
                targets = [{"endpoint": ep, "cluster": TEMPERATURE_CLUSTER_ID} for ep in endpoints]

                desc_str = ", ".join(descriptions)
                print_info(f"Creating consolidated ACL: TRVs {trv_nodes} -> Endpoints {endpoints}")
                print_info(f"  Covers: {desc_str}")

                acl_entry = {
                    "authMode": 2,  # CASE
                    "privilege": 1,  # VIEW
                    "subjects": trv_nodes,
                    "targets": targets,
                    "fabricIndex": client.fabric_id,
                }
                new_acl_entries.append(acl_entry)

            # Combine: preserved ACLs + new ACLs for our managed endpoints
            final_acls_to_write = filtered_acls + new_acl_entries

            print_info(f"Writing {len(final_acls_to_write)} total ACL entries to node {sensor_node}...")
            print_info(f"  Breakdown: {len(filtered_acls)} preserved + {len(new_acl_entries)} new consolidated entries")

            # Check if we might exceed ACL limits (typically 3-4 entries per fabric)
            if len(final_acls_to_write) > 4:
                print(f"{AnsiColors.WARNING}WARNING: Writing {len(final_acls_to_write)} ACL entries. Many devices limit ACLs to 3-4 per fabric.{AnsiColors.ENDC}")
                print(f"{AnsiColors.WARNING}If this fails, you may need to further consolidate by using same TRVs for multiple sensors.{AnsiColors.ENDC}")

            client.send_command("write_attribute", node_id=sensor_node, attribute_path="0/31/0", value=final_acls_to_write)
            print_success(f"✓ ACL updated for sensor node {sensor_node}")

        # Step 2: Configure TRVs
        print_header("Step 2: Configuring TRVs")

        # Build list of all unique TRVs and their corresponding sensor
        trv_to_sensor = {}
        for mapping in SENSOR_TRV_MAPPING:
            for trv_id in mapping["trv_nodes"]:
                if trv_id in trv_to_sensor:
                    print(f"{AnsiColors.WARNING}WARNING: TRV {trv_id} appears in multiple mappings. Using last mapping.{AnsiColors.ENDC}")
                trv_to_sensor[trv_id] = {
                    "sensor_node": mapping["sensor_node"],
                    "sensor_endpoint": mapping["sensor_endpoint"],
                    "description": mapping.get("description", "")
                }

        for trv_id, sensor_info in trv_to_sensor.items():
            sensor_node = sensor_info["sensor_node"]
            sensor_endpoint = sensor_info["sensor_endpoint"]
            desc = sensor_info["description"]

            print_info(f"--- Processing TRV {trv_id} ({desc}) ---")

            # Create binding to sensor
            binding_value = [{
                "node": sensor_node,
                "endpoint": sensor_endpoint,
                "cluster": TEMPERATURE_CLUSTER_ID,
                "fabricIndex": client.fabric_id
            }]

            print_info(f"Binding TRV {trv_id} to Sensor Node {sensor_node}, Endpoint {sensor_endpoint}...")
            client.send_command("write_attribute", node_id=trv_id, attribute_path="1/30/0", value=binding_value)
            print_success("Binding command sent.")

            # Enable remote sensing
            remote_sense_attr = "1/513/26"
            current_sensing = client.send_command("read_attribute", node_id=trv_id, attribute_path=remote_sense_attr).get(remote_sense_attr, 0)
            new_sensing_value = current_sensing | 1
            print_info(f"Enabling remote sensing on TRV {trv_id} (current: {current_sensing}, new: {new_sensing_value})...")
            client.send_command("write_attribute", node_id=trv_id, attribute_path=remote_sense_attr, value=new_sensing_value)
            print_success("Remote sensing command sent.")

        # Step 3: Wait and Verify
        wait_time = 30
        print_header(f"Step 3: Waiting for {wait_time} seconds")
        for i in range(wait_time, 0, -1): print(f"...{i}   ", end="\r", flush=True); time.sleep(1)
        print("\nWait complete. Starting verification.")

        # Step 4: Verification Report
        print_header("Step 4: Verification Report")
        verification_results = {"success": True, "issues": [], "warnings": []}

        # Verify Sensor ACL Configuration for each sensor node
        print_info("Verifying Sensor ACL configurations...")
        for sensor_node, mappings in sensor_nodes_to_process.items():
            print_info(f"Checking Sensor Node {sensor_node}...")
            try:
                sensor_acls = client.send_command("read_attribute", node_id=sensor_node, attribute_path="0/31/0").get("0/31/0", [])

                # Matter Compliance Check: ACL must exist on endpoint 0
                if not sensor_acls:
                    verification_results["success"] = False
                    verification_results["issues"].append(f"CRITICAL: No ACLs found on sensor node {sensor_node}")
                    continue

                print_success(f"Found {len(sensor_acls)} ACL entries on node {sensor_node}")

                # Verify each mapping has a corresponding ACL
                for mapping in mappings:
                    endpoint = mapping["sensor_endpoint"]
                    expected_trvs = mapping["trv_nodes"]
                    desc = mapping.get("description", f"Endpoint {endpoint}")

                    # Matter ACL format: "1"=privilege, "2"=authMode, "3"=subjects, "4"=targets
                    acl_found = False
                    for acl in sensor_acls:
                        subjects = acl.get("3", []) if isinstance(acl.get("3"), list) else []
                        targets = acl.get("4", []) if isinstance(acl.get("4"), list) else []

                        # Check if this ACL targets our specific endpoint
                        targets_our_endpoint = any(
                            t.get("0") == TEMPERATURE_CLUSTER_ID and t.get("1") == endpoint
                            for t in targets if isinstance(t, dict)
                        )

                        # Check if all expected TRVs are in subjects
                        has_all_trvs = all(tid in subjects for tid in expected_trvs)

                        if targets_our_endpoint and has_all_trvs:
                            acl_found = True
                            # Matter Compliance: Verify privilege level (1 = VIEW)
                            privilege = acl.get("1", 0)
                            if privilege != 1:
                                verification_results["warnings"].append(f"Node {sensor_node} Ep {endpoint}: ACL privilege is {privilege}, expected 1 (VIEW)")

                            # Matter Compliance: Verify auth mode (2 = CASE)
                            auth_mode = acl.get("2", 0)
                            if auth_mode != 2:
                                verification_results["warnings"].append(f"Node {sensor_node} Ep {endpoint}: ACL authMode is {auth_mode}, expected 2 (CASE)")

                            print_success(f"✓ {desc}: ACL verified (TRVs {subjects}, privilege: {privilege}, authMode: {auth_mode})")
                            break

                    if not acl_found:
                        verification_results["success"] = False
                        verification_results["issues"].append(f"CRITICAL: ACL not found for {desc} (Node {sensor_node}, Ep {endpoint})")

            except Exception as e:
                verification_results["success"] = False
                verification_results["issues"].append(f"Failed to verify sensor node {sensor_node} ACL: {e}")

        # Verify TRV Bindings and Remote Sensing
        print_info("Verifying TRV configurations...")
        for trv_id, sensor_info in trv_to_sensor.items():
            expected_sensor_node = sensor_info["sensor_node"]
            expected_sensor_endpoint = sensor_info["sensor_endpoint"]
            desc = sensor_info["description"]

            print_info(f"Checking TRV {trv_id} ({desc})...")

            try:
                # Verify Binding Cluster (0x001E)
                # Matter Binding format: "1"=node, "3"=endpoint, "4"=cluster, "254"=fabricIndex
                binding_path = "1/30/0"
                bindings = client.send_command("read_attribute", node_id=trv_id, attribute_path=binding_path).get(binding_path, [])

                # Matter Compliance: Binding cluster must be on endpoint 1 for thermostats
                if not bindings:
                    verification_results["success"] = False
                    verification_results["issues"].append(f"TRV {trv_id}: No bindings found")
                else:
                    # Check for correct binding entry
                    binding_found = False
                    for binding in bindings:
                        if (binding.get("1") == expected_sensor_node and
                            binding.get("3") == expected_sensor_endpoint and
                            binding.get("4") == TEMPERATURE_CLUSTER_ID):
                            binding_found = True
                            print_success(f"✓ TRV {trv_id}: Binding verified (node={binding.get('1')}, ep={binding.get('3')}, cluster={binding.get('4')})")
                            break

                    if not binding_found:
                        verification_results["success"] = False
                        verification_results["issues"].append(f"TRV {trv_id}: Expected binding to node {expected_sensor_node}, ep {expected_sensor_endpoint} not found")

                # Verify Remote Sensing Configuration (Thermostat Cluster 0x0201, Attribute 0x001A)
                remote_sense_attr = "1/513/26"
                remote_sensing = client.send_command("read_attribute", node_id=trv_id, attribute_path=remote_sense_attr).get(remote_sense_attr, 0)

                # Matter Compliance: Bit 0 must be set for remote temperature sensing
                if remote_sensing & 1:
                    print_success(f"✓ TRV {trv_id}: Remote sensing enabled (value: {remote_sensing})")
                else:
                    verification_results["success"] = False
                    verification_results["issues"].append(f"TRV {trv_id}: Remote sensing NOT enabled (value: {remote_sensing})")

                # Matter Compliance: Check LocalTemperature attribute for updates
                local_temp_attr = "1/513/0"
                local_temp = client.send_command("read_attribute", node_id=trv_id, attribute_path=local_temp_attr).get(local_temp_attr)
                if local_temp is not None:
                    print_success(f"✓ TRV {trv_id}: LocalTemperature reads {local_temp/100:.1f}°C")
                else:
                    verification_results["warnings"].append(f"TRV {trv_id}: LocalTemperature is null (may update soon)")

            except Exception as e:
                verification_results["success"] = False
                verification_results["issues"].append(f"TRV {trv_id}: Verification failed - {e}")

        # Persistence Verification
        print_info("Verifying cluster modification persistence...")
        try:
            # Re-read sensor ACLs to confirm persistence for each sensor node
            for sensor_node, mappings in sensor_nodes_to_process.items():
                sensor_acls_check = client.send_command("read_attribute", node_id=sensor_node, attribute_path="0/31/0").get("0/31/0", [])
                if sensor_acls_check:
                    print_success(f"✓ Sensor Node {sensor_node}: ACL modifications persisted ({len(sensor_acls_check)} entries)")
                else:
                    verification_results["warnings"].append(f"Sensor Node {sensor_node}: ACL appears empty on re-read")

            # Re-read TRV bindings to confirm persistence
            for trv_id in trv_to_sensor.keys():
                binding_path = "1/30/0"
                bindings_check = client.send_command("read_attribute", node_id=trv_id, attribute_path=binding_path).get(binding_path, [])
                if bindings_check:
                    print_success(f"✓ TRV {trv_id}: Binding modifications persisted")
                else:
                    verification_results["success"] = False
                    verification_results["issues"].append(f"TRV {trv_id}: Binding modifications NOT persisted")

        except Exception as e:
            verification_results["warnings"].append(f"Persistence verification incomplete: {e}")

        # Final Report
        print_header("Verification Summary")
        if verification_results["success"]:
            print_success("✓ ALL CHECKS PASSED - Configuration is Matter compliant and persistent")
        else:
            print_fail("✗ VERIFICATION FAILED - Issues detected")

        if verification_results["issues"]:
            print("\nCRITICAL ISSUES:")
            for issue in verification_results["issues"]:
                print_fail(f"  • {issue}")

        if verification_results["warnings"]:
            print("\nWARNINGS:")
            for warning in verification_results["warnings"]:
                print(f"{AnsiColors.WARNING}  • {warning}{AnsiColors.ENDC}")

        if verification_results["success"] and not verification_results["warnings"]:
            print_success("\nConfiguration is fully Matter-compliant:")
            print_success("  • ACL uses CASE authentication (authMode=2)")
            print_success("  • ACL grants VIEW privilege (privilege=1)")
            print_success("  • Bindings target correct cluster (0x0402)")
            print_success("  • Remote sensing enabled (bit 0 set)")
            print_success("  • All modifications persisted successfully")

    except Exception as e:
        print_fail(f"An error occurred: {e}")
    finally:
        client.close()
        print_header("Script Finished")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bind Matter thermostats to a temperature sensor.")
    parser.add_argument("--debug", action="store_true", help="Enable detailed WebSocket message tracing.")
    args = parser.parse_args()
    main(debug_mode=args.debug)
