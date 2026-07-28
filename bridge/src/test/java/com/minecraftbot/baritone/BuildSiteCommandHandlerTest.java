package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;

import java.util.function.IntPredicate;
import net.minecraft.client.Minecraft;
import java.net.Socket;

import static org.junit.jupiter.api.Assertions.*;

public class BuildSiteCommandHandlerTest {

    private BuildSiteCommandHandler handler;

    @Mock
    private Minecraft mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private Socket mockSocket;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new BuildSiteCommandHandler();
    }

    @Test
    void testGetCommandName() {
        assertEquals("inspect_build_site", handler.getCommandName());
    }

    @Test
    void testOfflineSafe() {
        assertTrue(handler.isOfflineSafe());
    }

    @Test
    void testHandle_MissingClient() {
        CommandResult result = handler.execute(new JsonObject(), null, mockBaritone, mockSocket);
        assertFalse(result.isSuccess());
        assertEquals("Minecraft client not available", result.getErrorMessage());
    }

    @Test
    void testHandle_MissingPlayerOrWorld() {
        TestUtils.setField(mockClient, "player", null);
        TestUtils.setField(mockClient, "level", null);

        CommandResult result = handler.execute(new JsonObject(), mockClient, mockBaritone, mockSocket);
        assertFalse(result.isSuccess());
        assertEquals("World or player not available", result.getErrorMessage());
    }

    @Test
    void testFactoryRegistration() {
        CommandHandler handler = CommandHandlerFactory.getHandler("inspect_build_site");
        assertNotNull(handler);
        assertEquals("inspect_build_site", handler.getCommandName());
        assertInstanceOf(BuildSiteCommandHandler.class, handler);
    }

    @Test
    void testHasFluidInSurfaceScan_InWindowFluidsDetected() {
        IntPredicate fluidPredicate = y -> y == 70;
        assertTrue(BuildSiteCommandHandler.hasFluidInSurfaceScan(64, fluidPredicate));
    }

    @Test
    void testHasFluidInSurfaceScan_OutsideWindowFluidsIgnored() {
        IntPredicate fluidPredicate = y -> y == 64 + 7;
        assertFalse(BuildSiteCommandHandler.hasFluidInSurfaceScan(64, fluidPredicate));
    }
}
