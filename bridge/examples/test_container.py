import socket
import json
import time

HOST = '127.0.0.1'
PORT = 5555

def send_command(sock, command, params={}):
    request = {
        "command": command,
        "params": params,
        "id": str(int(time.time() * 1000))
    }
    sock.sendall((json.dumps(request) + "\n").encode('utf-8'))
    
    data = b""
    while True:
        chunk = sock.recv(4096)
        data += chunk
        if b"\n" in chunk:
            break
            
    return json.loads(data.decode('utf-8').strip())

def main():
    print(f"Connecting to {HOST}:{PORT}...")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((HOST, PORT))
            print("Connected.")
            
            # Get player position
            print("\nGetting player state...")
            state = send_command(s, "get_state")
            if state.get("status") != "ok":
                print("Error:", state.get("error"))
                return
                
            pos = state["data"]["block_position"]
            print(f"Player at: ({pos['x']}, {pos['y']}, {pos['z']})")
            
            # Try interacting with block directly in front (x+1)
            target_x = pos['x'] + 1
            target_y = pos['y']
            target_z = pos['z']
            
            print(f"\nInteracting with block at ({target_x}, {target_y}, {target_z})...")
            resp = send_command(s, "interact_block", {"x": target_x, "y": target_y, "z": target_z})
            print("Response:", resp)
            
            time.sleep(0.5)
            
            # Check if a screen opened
            print("\nChecking screen...")
            screen = send_command(s, "get_screen")
            if screen.get("status") == "ok":
                data = screen.get("data", {})
                print(f"Screen Type: {data.get('type')}")
                print(f"Sync ID: {data.get('sync_id')}")
                print(f"Total Slots: {data.get('total_slots')}")
                
                slots = data.get("slots", [])
                print("\nNon-empty slots:")
                for slot in slots:
                    if slot.get("count", 0) > 0:
                        print(f"  Slot {slot['slot']}: {slot['count']}x {slot['name']}")
            else:
                print("No screen open or error:", screen.get("data", {}).get("error"))
            
            # Close screen
            print("\nClosing screen...")
            close_resp = send_command(s, "close_screen")
            print("Closed:", close_resp.get("data", {}).get("closed"))
                
    except ConnectionRefusedError:
        print("Could not connect to Baritone Bridge. Is Minecraft running with the mod?")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
