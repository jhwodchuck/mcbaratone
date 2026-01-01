package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;

import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.screen.slot.SlotActionType;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.net.Socket;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

public class InventoryCommandHandlerTest {

    @Mock
    private MinecraftClient minecraftClient;
    @Mock
    private IBaritone baritone;
    @Mock
    private ClientPlayerEntity player;
    @Mock
    private PlayerInventory inventory;
    @Mock
    private Socket clientSocket;

    private InventoryCommandHandler handler;

    @BeforeEach
    public void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new InventoryCommandHandler();
        when(minecraftClient.player).thenReturn(player);
        when(minecraftClient.interactionManager).thenReturn(interactionManager);
        when(player.getInventory()).thenReturn(inventory);
    }

    @Test
    public void testGetCommandName() {
        assertEquals("inventory", handler.getCommandName());
    }

    @Test
    public void testHandleGetInventory() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "get");

        // Mock empty inventory slots
        when(inventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(inventory.selectedSlot).thenReturn(2);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertTrue(result.isSuccess());
        JsonObject data = result.getData();
        assertTrue(data.has("inventory"));
        assertTrue(data.has("armor"));
        assertTrue(data.has("offhand"));
        assertEquals(2, data.get("selected_slot").getAsInt());
    }

    @Test
    public void testHandleGetInventoryWithItems() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "get");

        ItemStack stoneStack = new ItemStack(Items.STONE, 5);
        when(inventory.getStack(0)).thenReturn(stoneStack);
        when(inventory.getStack(anyInt())).thenReturn(ItemStack.EMPTY);
        when(inventory.selectedSlot).thenReturn(0);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertTrue(result.isSuccess());
        JsonObject data = result.getData();
        assertEquals("minecraft:stone", data.getAsJsonArray("inventory").get(0).getAsJsonObject().get("id").getAsString());
        assertEquals(5, data.getAsJsonArray("inventory").get(0).getAsJsonObject().get("count").getAsInt());
    }

    @Test
    public void testHandleGetInventoryNoPlayer() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "get");

        when(minecraftClient.player).thenReturn(null);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Player not available", result.getError());
    }

    @Test
    public void testHandleInventoryClickInvalidType() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "click");
        params.addProperty("type", "invalid_type");

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertTrue(result.getError().contains("Invalid click type"));
    }

    @Test
    public void testHandleInventoryClickNoPlayer() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "click");

        when(minecraftClient.player).thenReturn(null);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Player or interaction manager not available", result.getError());
    }

    @Test
    public void testHandleInventoryClickNoInteractionManager() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "click");

        when(minecraftClient.interactionManager).thenReturn(null);

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Player or interaction manager not available", result.getError());
    }

    @Test
    public void testHandleUnknownAction() {
        JsonObject params = new JsonObject();
        params.addProperty("action", "unknown");

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Unknown inventory action: unknown", result.getError());
    }

    @Test
    public void testDefaultAction() {
        JsonObject params = new JsonObject();
        // No action specified, should default to "get"

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertTrue(result.isSuccess());
        assertTrue(result.getData().has("inventory"));
    }
}