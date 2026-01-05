package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.BlockPos;

import java.net.Socket;
import java.util.concurrent.CompletableFuture;

/**
 * Interface for rollback operations that can undo state changes made by commands.
 * Each rollback operation represents a reversible action that can be executed
 * to restore the game state to its previous condition.
 */
public interface RollbackOperation {
    /**
     * Execute the rollback operation to undo the original action.
     *
     * @param client Minecraft client
     * @param baritone Baritone API instance
     * @param clientSocket Client socket
     * @return CompletableFuture that completes when rollback is done
     */
    CompletableFuture<Void> rollback(MinecraftClient client, IBaritone baritone, Socket clientSocket);

    /**
     * Get a description of what this rollback operation will do.
     *
     * @return Human-readable description
     */
    String getDescription();

    /**
     * Check if this rollback operation can still be executed safely.
     * For example, a block break rollback might not be safe if the block is no longer there.
     *
     * @param client Minecraft client
     * @return true if rollback can be executed safely
     */
    boolean canRollback(MinecraftClient client);
}