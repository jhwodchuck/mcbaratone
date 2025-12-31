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
            
            # Test select_slot
            print("\nSelecting hotbar slot 0...")
            resp = send_command(s, "select_slot", {"slot": 0})
            print("Response:", resp.get("data"))
            
            # Get inventory to see what's in hand
            print("\nGetting inventory...")
            inv = send_command(s, "get_inventory")
            if inv.get("status") == "ok":
                selected = inv["data"].get("selected_slot", 0)
                hotbar = inv["data"].get("inventory", [])
                if hotbar and len(hotbar) > selected:
                    item = hotbar[selected]
                    if item.get("count", 0) > 0:
                        print(f"Holding: {item['name']} ({item['id']})")
                    else:
                        print("Holding: Nothing (empty slot)")
            
            # Test use_item (if holding food, will eat)
            print("\nTrying to use item (hold for 2 seconds if food)...")
            resp = send_command(s, "use_item", {"duration_ms": 2000})
            print("Response:", resp.get("data"))
            
            # Test get_entities and attack first one (be careful!)
            print("\nGetting nearby entities...")
            entities = send_command(s, "get_entities", {"radius": 10})
            if entities.get("status") == "ok":
                ent_list = entities["data"].get("entities", [])
                print(f"Found {len(ent_list)} entities nearby")
                
                # Find a hostile mob (optional - comment out if you don't want to attack)
                for e in ent_list:
                    if "zombie" in e.get("type", "").lower() or "skeleton" in e.get("type", "").lower():
                        print(f"\nAttacking {e['type']} (ID: {e['id']})...")
                        attack = send_command(s, "attack_entity", {"entity_id": e["id"]})
                        print("Attack response:", attack.get("data"))
                        break
                else:
                    print("No hostile mobs to attack (good!)")
            
            print("\nDone!")
                
    except ConnectionRefusedError:
        print("Could not connect to Baritone Bridge. Is Minecraft running with the mod?")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
