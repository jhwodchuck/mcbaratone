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
    
    # Receive response
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
            
            # 1. Get initial inventory
            print("Getting inventory...")
            resp = send_command(s, "get_inventory")
            inv = resp.get("data", {}).get("inventory", [])
            
            # Find first empty slot and first non-empty slot
            src_slot = -1
            dst_slot = -1
            
            # Simple test: Swap slot 9 (first storage) with slot 0 (first hotbar)
            # regardless of what's in them, just to test the click
            src_slot = 9
            dst_slot = 0
            
            print(f"Attempting to swap slot {src_slot} with slot {dst_slot}...")
            
            # Click source (pickup)
            print(f"Clicking source slot {src_slot}...")
            send_command(s, "inventory_click", {"slot": src_slot, "type": "PICKUP"})
            time.sleep(0.5)
            
            # Click dest (swap/place)
            print(f"Clicking dest slot {dst_slot}...")
            send_command(s, "inventory_click", {"slot": dst_slot, "type": "PICKUP"})
            time.sleep(0.5)
            
            # Verify?
            print("Done. Check in-game if items moved.")
            
    except ConnectionRefusedError:
        print("Could not connect to Baritone Bridge. Is Minecraft running with the mod?")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
