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
            print("Polling for events every 2 seconds (Ctrl+C to stop)...")
            print("Try sending a chat message in-game or taking damage!\n")
            
            while True:
                resp = send_command(s, "get_events")
                
                if resp.get("status") == "ok":
                    data = resp.get("data", {})
                    count = data.get("count", 0)
                    
                    if count > 0:
                        print(f"Received {count} events:")
                        for event in data.get("events", []):
                            event_type = event.get("type")
                            timestamp = event.get("timestamp")
                            event_data = event.get("data", {})
                            
                            if event_type == "chat":
                                print(f"  [CHAT] {event_data.get('message')}")
                            elif event_type == "damage":
                                print(f"  [DAMAGE] Took {event_data.get('damage_taken'):.1f} damage! Health: {event_data.get('health'):.1f}/{event_data.get('max_health'):.1f}")
                            else:
                                print(f"  [{event_type.upper()}] {event_data}")
                        print()
                else:
                    print("Error:", resp.get("data", {}).get("error"))
                
                time.sleep(2)
                
    except KeyboardInterrupt:
        print("\nStopped polling.")
    except ConnectionRefusedError:
        print("Could not connect to Baritone Bridge. Is Minecraft running with the mod?")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
