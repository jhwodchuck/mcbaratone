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
            
            # Get recipes with a filter
            print("\nSearching for stick recipes...")
            resp = send_command(s, "get_recipes", {"filter": "stick", "limit": 10})
            
            if resp.get("status") == "ok":
                data = resp.get("data", {})
                print(f"Found {data.get('count')} recipes:")
                
                for recipe in data.get("recipes", []):
                    print(f"\n  Recipe: {recipe['id']}")
                    print(f"  Output: {recipe['output_count']}x {recipe['output']}")
                    print(f"  Type: {recipe['type']}")
                    print("  Ingredients:")
                    for i, ing in enumerate(recipe.get("ingredients", [])):
                        if ing:  # Skip empty ingredient slots
                            print(f"    Slot {i}: {' or '.join(ing[:3])}{'...' if len(ing) > 3 else ''}")
            else:
                print("Error:", resp.get("data", {}).get("error"))
            
            # Also show plank recipes
            print("\n\nSearching for plank recipes...")
            resp = send_command(s, "get_recipes", {"filter": "plank", "limit": 5})
            
            if resp.get("status") == "ok":
                data = resp.get("data", {})
                print(f"Found {data.get('count')} recipes:")
                
                for recipe in data.get("recipes", []):
                    print(f"  - {recipe['output_count']}x {recipe['output']} from {recipe['type']}")
                
    except ConnectionRefusedError:
        print("Could not connect to Baritone Bridge. Is Minecraft running with the mod?")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
