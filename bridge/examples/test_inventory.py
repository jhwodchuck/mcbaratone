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

def print_item(item):
    if item['count'] == 0:
        return
    dmg = f" (dmg: {item['damage']}/{item['max_damage']})" if item['max_damage'] > 0 else ""
    print(f"  Slot {item['slot']}: {item['count']}x {item['name']} [{item['id']}]{dmg}")

def main():
    print(f"Connecting to {HOST}:{PORT}...")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((HOST, PORT))
            print("Connected.")
            
            print("\nRequesting inventory...")
            response = send_command(s, "get_inventory")
            
            if response.get("status") == "ok":
                data = response.get("data", {})
                
                print(f"\nSelected Slot: {data.get('selected_slot')}")
                
                print("\n--- Armor ---")
                for item in data.get('armor', []):
                    print_item(item)
                    
                print("\n--- Offhand ---")
                for item in data.get('offhand', []):
                    print_item(item)
                    
                print("\n--- Main Inventory ---")
                # Group by hotbar vs storage
                inv = data.get('inventory', [])
                print("Hotbar:")
                for i in range(9):
                    if i < len(inv): print_item(inv[i])
                
                print("Storage:")
                for i in range(9, len(inv)):
                    print_item(inv[i])
                    
            else:
                print("Error:", response.get("error"))
                
    except ConnectionRefusedError:
        print("Could not connect to Baritone Bridge. Is Minecraft running with the mod?")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
