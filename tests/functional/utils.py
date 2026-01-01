
import time

def give_item(client, item_id: str, count: int = 1):
    """
    Give the player items using cheats.
    """
    print(f"[SETUP] Giving {count} {item_id}")
    client.transport.dispatch("chat", {"message": f"/give @p {item_id} {count}"})
    time.sleep(0.5)

def set_time(client, time_val: str):
    """
    Set world time (e.g., 'day', 'night', '0', '13000').
    """
    print(f"[SETUP] Setting time to {time_val}")
    client.transport.dispatch("chat", {"message": f"/time set {time_val}"})
    time.sleep(0.5)

def teleport(client, x, y, z):
    """
    Teleport player.
    """
    print(f"[SETUP] Teleporting to {x} {y} {z}")
    client.transport.dispatch("chat", {"message": f"/tp @p {x} {y} {z}"})
    time.sleep(1.0) # Wait for chunk load

def clear_inventory(client):
    """
    Clear inventory.
    """
    print("[SETUP] Clearing inventory")
    client.transport.dispatch("chat", {"message": "/clear"})
    time.sleep(0.5)

def gamemode(client, mode: str):
    """
    Set gamemode (survival, creative, spectator).
    """
    print(f"[SETUP] Setting gamemode to {mode}")
    client.transport.dispatch("chat", {"message": f"/gamemode {mode}"})
    time.sleep(0.5)
