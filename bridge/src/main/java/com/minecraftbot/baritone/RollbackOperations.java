package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.pathing.goals.GoalBlock;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.player.PlayerInventory;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.World;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.Socket;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

/**
 * Concrete implementations of rollback operations for different command types.
 */
public class RollbackOperations {
    private static final Logger LOGGER = LoggerFactory.getLogger(RollbackOperations.class);

    /**
     * Rollback operation for crafting operations.
     * Returns ingredients back to inventory by uncrafting the crafted item.
     */
    public static class CraftingRollback implements RollbackOperation {
        private final String recipeId;
        private final int countCrafted;
        private final Map<String, Integer> ingredientsUsed;

        public CraftingRollback(String recipeId, int countCrafted, Map<String, Integer> ingredientsUsed) {
            this.recipeId = recipeId;
            this.countCrafted = countCrafted;
            this.ingredientsUsed = ingredientsUsed;
        }

        @Override
        public CompletableFuture<Void> rollback(MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return CompletableFuture.runAsync(() -> {
                try {
                    LOGGER.info("Rolling back crafting of {} x{} of {}", countCrafted, recipeId);

                    // For now, we can't easily uncraft items programmatically in Minecraft
                    // This would require complex inventory manipulation
                    // Instead, we'll log what should be done
                    LOGGER.warn("Crafting rollback not fully implemented - ingredients should be manually returned: {}",
                        ingredientsUsed);

                    // Future enhancement: Could implement by crafting reverse recipes if they exist
                    // or by tracking exact inventory changes

                } catch (Exception e) {
                    LOGGER.error("Failed to rollback crafting operation", e);
                }
            });
        }

        @Override
        public String getDescription() {
            return String.format("Uncraft %d x %s (return ingredients: %s)", countCrafted, recipeId, ingredientsUsed);
        }

        @Override
        public boolean canRollback(MinecraftClient client) {
            // Crafting rollback is always possible (though may not be fully implemented)
            return true;
        }
    }

    /**
     * Rollback operation for block placement.
     * Breaks the placed block to restore the original state.
     */
    public static class BlockPlacementRollback implements RollbackOperation {
        private final BlockPos blockPos;
        private final String blockId;

        public BlockPlacementRollback(BlockPos blockPos, String blockId) {
            this.blockPos = blockPos;
            this.blockId = blockId;
        }

        @Override
        public CompletableFuture<Void> rollback(MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return CompletableFuture.runAsync(() -> {
                try {
                    LOGGER.info("Rolling back block placement of {} at {}", blockId, blockPos);

                    // Use Baritone to break the block
                    if (baritone != null) {
                        baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(blockPos));
                        // Wait for path completion would be handled by the sequence executor
                    } else {
                        LOGGER.warn("Cannot rollback block placement: Baritone not available");
                    }

                } catch (Exception e) {
                    LOGGER.error("Failed to rollback block placement", e);
                }
            });
        }

        @Override
        public String getDescription() {
            return String.format("Break block %s at (%d,%d,%d)", blockId, blockPos.getX(), blockPos.getY(), blockPos.getZ());
        }

        @Override
        public boolean canRollback(MinecraftClient client) {
            if (client.world == null) return false;

            // Check if the block is still there and is what we expect
            var blockState = client.world.getBlockState(blockPos);
            String currentBlockId = Registries.BLOCK.getId(blockState.getBlock()).toString();

            return currentBlockId.equals(blockId);
        }
    }

    /**
     * Rollback operation for entity interactions.
     * Attempts to revert entity state where possible (limited functionality).
     */
    public static class EntityInteractionRollback implements RollbackOperation {
        private final int entityId;
        private final String interactionType;
        private final JsonObject originalState;

        public EntityInteractionRollback(int entityId, String interactionType, JsonObject originalState) {
            this.entityId = entityId;
            this.interactionType = interactionType;
            this.originalState = originalState;
        }

        @Override
        public CompletableFuture<Void> rollback(MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return CompletableFuture.runAsync(() -> {
                try {
                    LOGGER.info("Rolling back entity interaction {} on entity {}", interactionType, entityId);

                    // Entity rollback is complex and depends on the interaction type
                    // Many interactions cannot be easily reversed
                    LOGGER.warn("Entity interaction rollback not implemented for type: {}", interactionType);

                } catch (Exception e) {
                    LOGGER.error("Failed to rollback entity interaction", e);
                }
            });
        }

        @Override
        public String getDescription() {
            return String.format("Revert %s interaction on entity %d", interactionType, entityId);
        }

        @Override
        public boolean canRollback(MinecraftClient client) {
            // Entity rollback safety check is complex - assume it's possible for now
            return true;
        }
    }

    /**
     * Rollback operation for movement.
     * Paths back to the original position.
     */
    public static class MovementRollback implements RollbackOperation {
        private final BlockPos originalPosition;

        public MovementRollback(BlockPos originalPosition) {
            this.originalPosition = originalPosition;
        }

        @Override
        public CompletableFuture<Void> rollback(MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return CompletableFuture.runAsync(() -> {
                try {
                    LOGGER.info("Rolling back movement to original position {}", originalPosition);

                    if (baritone != null && client.player != null) {
                        // Check if we're not already at the original position
                        BlockPos currentPos = client.player.getBlockPos();
                        if (!currentPos.equals(originalPosition)) {
                            baritone.getCustomGoalProcess().setGoalAndPath(new GoalBlock(originalPosition));
                        }
                    } else {
                        LOGGER.warn("Cannot rollback movement: Baritone or player not available");
                    }

                } catch (Exception e) {
                    LOGGER.error("Failed to rollback movement", e);
                }
            });
        }

        @Override
        public String getDescription() {
            return String.format("Return to position (%d,%d,%d)",
                originalPosition.getX(), originalPosition.getY(), originalPosition.getZ());
        }

        @Override
        public boolean canRollback(MinecraftClient client) {
            return client.player != null && client.world != null;
        }
    }

    /**
     * Rollback operation for inventory changes (like throwing items).
     * Attempts to restore items to inventory.
     */
    public static class InventoryChangeRollback implements RollbackOperation {
        private final Map<String, Integer> itemsToRestore;

        public InventoryChangeRollback(Map<String, Integer> itemsToRestore) {
            this.itemsToRestore = itemsToRestore;
        }

        @Override
        public CompletableFuture<Void> rollback(MinecraftClient client, IBaritone baritone, Socket clientSocket) {
            return CompletableFuture.runAsync(() -> {
                try {
                    LOGGER.info("Rolling back inventory changes: {}", itemsToRestore);

                    // Inventory rollback would require picking up dropped items or
                    // having them available nearby
                    LOGGER.warn("Inventory rollback not implemented - items may need to be manually recovered: {}",
                        itemsToRestore);

                } catch (Exception e) {
                    LOGGER.error("Failed to rollback inventory changes", e);
                }
            });
        }

        @Override
        public String getDescription() {
            return String.format("Restore items to inventory: %s", itemsToRestore);
        }

        @Override
        public boolean canRollback(MinecraftClient client) {
            // Check if there's space in inventory
            if (client.player == null) return false;

            PlayerInventory inv = client.player.getInventory();
            int emptySlots = 0;
            for (int i = 0; i < inv.size(); i++) {
                if (inv.getStack(i).isEmpty()) emptySlots++;
            }

            return emptySlots >= itemsToRestore.size();
        }
    }
}