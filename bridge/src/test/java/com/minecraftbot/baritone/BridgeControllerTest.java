package com.minecraftbot.baritone;

import com.google.gson.JsonObject;
import java.io.PrintStream;

public class BridgeControllerTest {

    static class MockBridgeContext implements BridgeContext {
        boolean playerAvailable = true;
        String lastChat;
        String lastMineBlock;
        int goalX, goalY, goalZ, goalRadius;
        
        @Override
        public void sendChat(String message) {
            this.lastChat = message;
        }

        @Override
        public void setGoal(int x, int y, int z, int radius) {
            this.goalX = x;
            this.goalY = y;
            this.goalZ = z;
            this.goalRadius = radius;
        }

        @Override
        public void mine(String blockType) {
            this.lastMineBlock = blockType;
        }

        @Override
        public boolean isPlayerAvailable() {
            return playerAvailable;
        }
    }

    public static void main(String[] args) {
        System.out.println("Running BridgeController Tests...");
        int passed = 0;
        int failed = 0;

        try {
            testGoto();
            passed++;
        } catch (Exception e) {
            System.out.println("FAIL: testGoto - " + e.getMessage());
            e.printStackTrace();
            failed++;
        }

        try {
            testMine();
            passed++;
        } catch (Exception e) {
            System.out.println("FAIL: testMine - " + e.getMessage());
            e.printStackTrace();
            failed++;
        }
        
        try {
            testMissingCommand();
            passed++;
        } catch (Exception e) {
            System.out.println("FAIL: testMissingCommand - " + e.getMessage());
            e.printStackTrace();
            failed++;
        }

        try {
            testChat();
            passed++;
        } catch (Exception e) {
            System.out.println("FAIL: testChat - " + e.getMessage());
            e.printStackTrace();
            failed++;
        }

        try {
            testUnknownCommand();
            passed++;
        } catch (Exception e) {
            System.out.println("FAIL: testUnknownCommand - " + e.getMessage());
            e.printStackTrace();
            failed++;
        }

        try {
            testPlayerUnavailable();
            passed++;
        } catch (Exception e) {
            System.out.println("FAIL: testPlayerUnavailable - " + e.getMessage());
            e.printStackTrace();
            failed++;
        }

        try {
            testIdEcho();
            passed++;
        } catch (Exception e) {
            System.out.println("FAIL: testIdEcho - " + e.getMessage());
            e.printStackTrace();
            failed++;
        }

        try {
            testChatEmpty();
            passed++;
        } catch (Exception e) {
            System.out.println("FAIL: testChatEmpty - " + e.getMessage());
            e.printStackTrace();
            failed++;
        }

        System.out.println("Tests Completed. Passed: " + passed + ", Failed: " + failed);
        if (failed > 0) System.exit(1);
    }

    private static void testGoto() {
        MockBridgeContext context = new MockBridgeContext();
        BridgeController controller = new BridgeController(context);

        JsonObject req = new JsonObject();
        req.addProperty("command", "goto");
        JsonObject params = new JsonObject();
        params.addProperty("x", 100);
        params.addProperty("y", 64);
        params.addProperty("z", -200);
        req.add("params", params);

        JsonObject res = controller.handleCommand(req);

        assertEquals("ok", res.get("status").getAsString());
        assertEquals(100, context.goalX);
        assertEquals(-200, context.goalZ);
        System.out.println("PASS: testGoto");
    }

    private static void testMine() {
        MockBridgeContext context = new MockBridgeContext();
        BridgeController controller = new BridgeController(context);

        JsonObject req = new JsonObject();
        req.addProperty("command", "mine");
        JsonObject params = new JsonObject();
        params.addProperty("block_type", "diamond_ore");
        req.add("params", params);

        JsonObject res = controller.handleCommand(req);

        assertEquals("ok", res.get("status").getAsString());
        assertEquals("diamond_ore", context.lastMineBlock);
        System.out.println("PASS: testMine");
    }
    
    private static void testMissingCommand() {
        MockBridgeContext context = new MockBridgeContext();
        BridgeController controller = new BridgeController(context);
        
        JsonObject req = new JsonObject();
        // No command property
        
        JsonObject res = controller.handleCommand(req);
        assertEquals("error", res.get("status").getAsString());
        assertEquals("Missing command", res.get("error").getAsString());
         System.out.println("PASS: testMissingCommand");
    }

    private static void testChat() {
        MockBridgeContext context = new MockBridgeContext();
        BridgeController controller = new BridgeController(context);

        JsonObject req = new JsonObject();
        req.addProperty("command", "chat");
        JsonObject params = new JsonObject();
        params.addProperty("message", "Hello World");
        req.add("params", params);

        JsonObject res = controller.handleCommand(req);

        assertEquals("ok", res.get("status").getAsString());
        assertEquals("Hello World", context.lastChat);
        System.out.println("PASS: testChat");
    }

    private static void testUnknownCommand() {
        MockBridgeContext context = new MockBridgeContext();
        BridgeController controller = new BridgeController(context);

        JsonObject req = new JsonObject();
        req.addProperty("command", "fly");

        JsonObject res = controller.handleCommand(req);

        assertEquals("error", res.get("status").getAsString());
        assertEquals("Unknown command: fly", res.get("error").getAsString());
        System.out.println("PASS: testUnknownCommand");
    }

    private static void testPlayerUnavailable() {
        MockBridgeContext context = new MockBridgeContext();
        context.playerAvailable = false;
        BridgeController controller = new BridgeController(context);

        JsonObject req = new JsonObject();
        req.addProperty("command", "goto");

        JsonObject res = controller.handleCommand(req);

        assertEquals("error", res.get("status").getAsString());
        assertEquals("Player not available", res.get("error").getAsString());
        System.out.println("PASS: testPlayerUnavailable");
    }

    private static void testIdEcho() {
        MockBridgeContext context = new MockBridgeContext();
        BridgeController controller = new BridgeController(context);

        JsonObject req = new JsonObject();
        req.addProperty("id", "test-id-123");
        req.addProperty("command", "goto");
        
        JsonObject res = controller.handleCommand(req);

        assertEquals("test-id-123", res.get("id").getAsString());
        System.out.println("PASS: testIdEcho");
    }

    private static void testChatEmpty() {
        MockBridgeContext context = new MockBridgeContext();
        BridgeController controller = new BridgeController(context);

        JsonObject req = new JsonObject();
        req.addProperty("command", "chat");
        JsonObject params = new JsonObject();
        params.addProperty("message", "");
        req.add("params", params);

        JsonObject res = controller.handleCommand(req);
        
        // Should be ok but not sent
        assertEquals("ok", res.get("status").getAsString());
        assertEquals(null, context.lastChat);
        System.out.println("PASS: testChatEmpty");
    }

    private static void assertEquals(Object expected, Object actual) {
        if (expected == null) {
            if (actual != null) {
                throw new RuntimeException("Assertion failed! Expected: null, Actual: " + actual);
            }
            return;
        }
        if (!expected.equals(actual)) {
            throw new RuntimeException("Assertion failed! Expected: " + expected + ", Actual: " + actual);
        }
    }
}
