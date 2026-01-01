package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.process.IMineProcess;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;
import net.minecraft.block.Block;
import net.minecraft.block.Blocks;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockedStatic;
import org.mockito.MockitoAnnotations;

import java.net.Socket;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

public class MineCommandHandlerTest {

    @Mock
    private MinecraftClient minecraftClient;
    @Mock
    private IBaritone baritone;
    @Mock
    private IMineProcess mineProcess;
    @Mock
    private Socket clientSocket;

    private MineCommandHandler handler;

    @BeforeEach
    public void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new MineCommandHandler();
        when(baritone.getMineProcess()).thenReturn(mineProcess);
    }

    @Test
    public void testGetCommandName() {
        assertEquals("mine", handler.getCommandName());
    }

    @Test
    public void testHandleSingleBlock() {
        JsonObject params = new JsonObject();
        params.addProperty("block_type", "minecraft:stone");
        params.addProperty("count", 10);

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class);
             MockedStatic<Registries> registriesStatic = mockStatic(Registries.class)) {

            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);
            registriesStatic.when(() -> Registries.BLOCK.containsId(any(Identifier.class))).thenReturn(true);
            registriesStatic.when(() -> Registries.BLOCK.get(any(Identifier.class))).thenReturn(Blocks.STONE);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertTrue(data.get("started").getAsBoolean());
            assertEquals("minecraft:stone", data.get("block").getAsString());
            assertEquals(10, data.get("count").getAsInt());

            verify(mineProcess).mine(10, Blocks.STONE);
        }
    }

    @Test
    public void testHandleSingleBlockNoCount() {
        JsonObject params = new JsonObject();
        params.addProperty("block_type", "minecraft:dirt");

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class);
             MockedStatic<Registries> registriesStatic = mockStatic(Registries.class)) {

            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);
            registriesStatic.when(() -> Registries.BLOCK.containsId(any(Identifier.class))).thenReturn(true);
            registriesStatic.when(() -> Registries.BLOCK.get(any(Identifier.class))).thenReturn(Blocks.DIRT);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertEquals("minecraft:dirt", data.get("block").getAsString());
            assertEquals(0, data.get("count").getAsInt());

            verify(mineProcess).mine(0, Blocks.DIRT);
        }
    }

    @Test
    public void testHandleSingleBlockUnknownBlock() {
        JsonObject params = new JsonObject();
        params.addProperty("block_type", "minecraft:unknown_block");

        try (MockedStatic<Registries> registriesStatic = mockStatic(Registries.class)) {
            registriesStatic.when(() -> Registries.BLOCK.containsId(any(Identifier.class))).thenReturn(false);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertFalse(result.isSuccess());
            assertTrue(result.getError().contains("Unknown block: minecraft:unknown_block"));
        }
    }

    @Test
    public void testHandleMultipleBlocks() {
        JsonObject params = new JsonObject();
        JsonArray blocks = new JsonArray();
        blocks.add("minecraft:stone");
        blocks.add("minecraft:dirt");
        params.add("blocks", blocks);
        params.addProperty("quantity", 5);

        try (MockedStatic<MinecraftClient> minecraftStatic = mockStatic(MinecraftClient.class);
             MockedStatic<Registries> registriesStatic = mockStatic(Registries.class)) {

            minecraftStatic.when(MinecraftClient::getInstance).thenReturn(minecraftClient);
            registriesStatic.when(() -> Registries.BLOCK.containsId(any(Identifier.class))).thenReturn(true);
            registriesStatic.when(() -> Registries.BLOCK.get(any(Identifier.class))).thenReturn(Blocks.STONE, Blocks.DIRT);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertTrue(data.get("started").getAsBoolean());
            assertEquals(2, data.get("blocks_found").getAsInt());
            assertEquals(5, data.get("count").getAsInt());

            verify(mineProcess).mine(eq(5), any());
        }
    }

    @Test
    public void testHandleMultipleBlocksEmptyArray() {
        JsonObject params = new JsonObject();
        params.add("blocks", new JsonArray());

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("No block IDs provided", result.getError());
    }

    @Test
    public void testHandleMultipleBlocksNoValidBlocks() {
        JsonObject params = new JsonObject();
        JsonArray blocks = new JsonArray();
        blocks.add("minecraft:unknown1");
        blocks.add("minecraft:unknown2");
        params.add("blocks", blocks);

        try (MockedStatic<Registries> registriesStatic = mockStatic(Registries.class)) {
            registriesStatic.when(() -> Registries.BLOCK.containsId(any(Identifier.class))).thenReturn(false);

            CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

            assertFalse(result.isSuccess());
            assertEquals("No valid blocks found to mine", result.getError());
        }
    }

    @Test
    public void testHandleMissingParameters() {
        JsonObject params = new JsonObject();
        // No block_type or blocks

        CommandResult result = handler.handle(params, minecraftClient, baritone, clientSocket);

        assertFalse(result.isSuccess());
        assertEquals("Missing block_type or blocks parameter", result.getError());
    }
}