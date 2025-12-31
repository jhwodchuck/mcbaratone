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
            
            print("\nRequesting nearby entities (radius 30)...")
            response = send_command(s, "get_entities", {"radius": 30})
            
            if response.get("status") == "ok":
                data = response.get("data", {})
                entities = data.get("entities", [])
                print(f"Found {len(entities)} entities:")
                for entity in entities:
                    type_id = entity.get("type", "unknown")
                    name = entity.get("name", "unknown")
                    pos = f"({entity.get('x', 0):.1f}, {entity.get('y', 0):.1f}, {entity.get('z', 0):.1f})"
                    dist = f"{entity.get('distance', 0):.1f}m"
                    health = f"Health: {entity.get('health', 0)}" if entity.get("is_living") else ""
                    print(f" - [{type_id}] {name} at {pos} dist={dist} {health}")
            else:
                print("Error:", response.get("error"))
                
    except ConnectionRefusedError:
        print("Could not connect to Baritone Bridge. Is Minecraft running with the mod?")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
