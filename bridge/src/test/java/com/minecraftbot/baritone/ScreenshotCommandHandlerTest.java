package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.util.ScreenshotRecorder;
import baritone.api.IBaritone;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockedStatic;
import org.mockito.MockitoAnnotations;

import java.io.File;
import java.net.Socket;
import java.nio.file.Files;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

/**
 * Tests for ScreenshotCommandHandler.
 *
 * Validation tests run without Minecraft bootstrap because parameter
 * validation completes before any Minecraft class is touched. Capture tests
 * require ScreenshotRecorder class initialization (Minecraft bootstrap) and
 * stay disabled in the unit-test environment; they document the real
 * contract: saveScreenshot(File, String, Framebuffer, int, Consumer&lt;Text&gt;)
 * writes asynchronously and reports completion via the callback.
 */
public class ScreenshotCommandHandlerTest {

    @Mock
    private MinecraftClient mockClient;

    @Mock
    private IBaritone mockBaritone;

    @Mock
    private Socket mockSocket;

    private ScreenshotCommandHandler handler;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        handler = new ScreenshotCommandHandler();
    }

    @Test
    void testGetCommandName() {
        assertEquals("screenshot", handler.getCommandName());
    }

    @Test
    void testValidation_InvalidFormat() {
        JsonObject params = new JsonObject();
        params.addProperty("format", "INVALID");

        CompletableFuture<CommandResult> future = handler.execute(params, mockClient, mockBaritone, mockSocket);
        CommandResult result = future.join();

        assertFalse(result.isSuccess());
        assertEquals("Unsupported format: INVALID. Supported: PNG", result.getErrorMessage());
    }

    @Test
    void testValidation_NonPngFormatRejected() {
        // The vanilla recorder only writes PNG; JPEG/BMP must not be advertised.
        for (String format : new String[] { "JPEG", "BMP" }) {
            JsonObject params = new JsonObject();
            params.addProperty("format", format);

            CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

            assertFalse(result.isSuccess(), "Format " + format + " should be rejected");
        }
    }

    @Test
    void testValidation_EmptyCustomName() {
        JsonObject params = new JsonObject();
        params.addProperty("custom_name", "");

        CompletableFuture<CommandResult> future = handler.execute(params, mockClient, mockBaritone, mockSocket);
        CommandResult result = future.join();

        assertFalse(result.isSuccess());
        assertEquals("Custom name cannot be empty", result.getErrorMessage());
    }

    @Test
    @Disabled("Requires Minecraft Bootstrap (ScreenshotRecorder class initialization) which fails in unit test environment")
    void testSuccessfulScreenshot_FilenameParam() throws Exception {
        File runDir = Files.createTempDirectory("mc-run").toFile();
        TestUtils.setField(mockClient, "runDirectory", runDir);
        // Run main-thread tasks inline for the mock client.
        doAnswer(inv -> {
            ((Runnable) inv.getArgument(0)).run();
            return null;
        }).when(mockClient).execute(any());

        JsonObject params = new JsonObject();
        params.addProperty("filename", "phase_test");
        params.addProperty("include_metadata", false);

        try (MockedStatic<ScreenshotRecorder> mockedScreenshot = mockStatic(ScreenshotRecorder.class)) {
            mockedScreenshot.when(() -> ScreenshotRecorder.saveScreenshot(
                    any(File.class), anyString(), any(), anyInt(), any()))
                    .thenAnswer(inv -> {
                        // Simulate the async IO worker: create the file at the
                        // pinned location, then invoke the completion callback.
                        File gameDir = inv.getArgument(0);
                        String name = inv.getArgument(1);
                        File out = new File(new File(gameDir, "screenshots"), name);
                        out.getParentFile().mkdirs();
                        assertTrue(out.createNewFile());
                        Consumer<net.minecraft.text.Text> callback = inv.getArgument(4);
                        callback.accept(null);
                        return null;
                    });

            CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertTrue(data.get("saved").getAsBoolean());
            // Python's phase_executor reads 'path'; file name uses the sanitized
            // 'filename' param plus timestamp and uniqueness counter.
            assertTrue(data.has("path"));
            assertTrue(data.get("file_name").getAsString().startsWith("phase_test_"));
            assertTrue(data.get("file_name").getAsString().endsWith(".png"));
            assertEquals("PNG", data.get("format").getAsString());
        }
    }

    @Test
    @Disabled("Requires Minecraft Bootstrap (ScreenshotRecorder class initialization) which fails in unit test environment")
    void testFailedScreenshot_FileNotWritten() throws Exception {
        File runDir = Files.createTempDirectory("mc-run").toFile();
        TestUtils.setField(mockClient, "runDirectory", runDir);
        doAnswer(inv -> {
            ((Runnable) inv.getArgument(0)).run();
            return null;
        }).when(mockClient).execute(any());

        JsonObject params = new JsonObject();

        try (MockedStatic<ScreenshotRecorder> mockedScreenshot = mockStatic(ScreenshotRecorder.class)) {
            mockedScreenshot.when(() -> ScreenshotRecorder.saveScreenshot(
                    any(File.class), anyString(), any(), anyInt(), any()))
                    .thenAnswer(inv -> {
                        // Callback fires but no file was written.
                        Consumer<net.minecraft.text.Text> callback = inv.getArgument(4);
                        callback.accept(null);
                        return null;
                    });

            CommandResult result = handler.execute(params, mockClient, mockBaritone, mockSocket).join();

            assertTrue(result.isSuccess());
            JsonObject data = result.getData();
            assertFalse(data.get("saved").getAsBoolean());
            assertTrue(data.has("error"));
        }
    }
}
