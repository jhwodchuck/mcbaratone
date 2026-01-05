package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.server.MinecraftServer;
import java.net.Socket;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.lang.reflect.Method;

public class ChatCommandHandler implements CommandHandler {
    @Override
    public CompletableFuture<CommandResult> handle(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        if (client.player == null) {
            return CompletableFuture.completedFuture(CommandResult.error("Player not available"));
        }

        try {
            String message = params.get("message").getAsString();
            String trimmed = message.trim();

            // Check if this is a Baritone command
            if (trimmed.startsWith("#")) {
                // Execute as Baritone command (strip the #)
                String command = trimmed.substring(1);

                // Execute on main thread for thread safety
                return CompletableFuture.supplyAsync(() -> {
                    try {
                        client.execute(() -> {
                            try {
                                baritone.getCommandManager().execute(command);
                            } catch (Exception e) {
                                throw new RuntimeException(e);
                            }
                        });
                        // Note: execute() returns true if command was recognized, even if it "failed" logically
                        // So we always return success here if no exception occurred
                        return CommandResult.success();
                    } catch (Exception e) {
                        return CommandResult.error("Baritone command failed: " + e.getMessage());
                    }
                });
            } else if (trimmed.startsWith("/")) {
                String command = trimmed.substring(1);
                return CompletableFuture.supplyAsync(() -> {
                    try {
                        MinecraftServer server = client.getServer();
                        if (server != null) {
                            CountDownLatch latch = new CountDownLatch(1);
                            AtomicReference<CommandResult> resultRef = new AtomicReference<>();
                            server.execute(() -> {
                                try {
                                    server.getCommandManager().executeWithPrefix(
                                        server.getCommandSource(),
                                        command
                                    );
                                    JsonObject data = new JsonObject();
                                    data.addProperty("executed", true);
                                    resultRef.set(CommandResult.success(data));
                                } catch (Exception e) {
                                    resultRef.set(CommandResult.error("Command failed: " + e.getMessage()));
                                } finally {
                                    latch.countDown();
                                }
                            });
                            if (!latch.await(2, TimeUnit.SECONDS)) {
                                return CommandResult.error("Command failed: timed out waiting for server execution");
                            }
                            CommandResult result = resultRef.get();
                            return result != null ? result : CommandResult.error("Command failed: no result");
                        }

                        client.submit(() -> {
                            try {
                                Method method;
                                try {
                                    method = client.player.networkHandler.getClass().getMethod("sendCommand", String.class);
                                } catch (NoSuchMethodException e) {
                                    method = client.player.networkHandler.getClass().getMethod("sendChatCommand", String.class);
                                }
                                method.invoke(client.player.networkHandler, command);
                            } catch (ReflectiveOperationException e) {
                                client.player.networkHandler.sendChatMessage("/" + command);
                            }
                            return null;
                        }).get();
                        return CommandResult.success();
                    } catch (Exception e) {
                        return CommandResult.error("Command failed: " + e.getMessage());
                    }
                });
            } else {
                // Regular chat message - send to server
                return CompletableFuture.supplyAsync(() -> {
                    try {
                        client.submit(() -> {
                            client.player.networkHandler.sendChatMessage(message);
                            return null;
                        }).get();
                        return CommandResult.success();
                    } catch (Exception e) {
                        return CommandResult.error("Chat failed: " + e.getMessage());
                    }
                });
            }
        } catch (Exception e) {
            return CompletableFuture.completedFuture(CommandResult.error("Chat failed: " + e.getMessage()));
        }
    }
    @Override
    public String getCommandName() { return "chat"; }
}
