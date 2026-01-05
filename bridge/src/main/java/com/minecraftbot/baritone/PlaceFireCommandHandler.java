package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.util.Hand;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Direction;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.World;

import java.net.Socket;
import java.util.*;
import java.util.concurrent.CompletableFuture;

/**
 * Enhanced PlaceFireCommandHandler with advanced fire placement logic.
 * Implements terrain analysis, safety checks, placement optimization, inventory management, and risk assessment.
 */
public class PlaceFireCommandHandler extends AsyncCommandHandler {

    private static final int SCAN_RADIUS = 3;
    private static final Set<Block> FLAMMABLE_BLOCKS = Set.of(
        Blocks.OAK_LOG, Blocks.SPRUCE_LOG, Blocks.BIRCH_LOG, Blocks.JUNGLE_LOG,
        Blocks.ACACIA_LOG, Blocks.DARK_OAK_LOG, Blocks.MANGROVE_LOG, Blocks.CRIMSON_STEM,
        Blocks.WARPED_STEM, Blocks.OAK_PLANKS, Blocks.SPRUCE_PLANKS, Blocks.BIRCH_PLANKS,
        Blocks.JUNGLE_PLANKS, Blocks.ACACIA_PLANKS, Blocks.DARK_OAK_PLANKS, Blocks.MANGROVE_PLANKS,
        Blocks.OAK_LEAVES, Blocks.SPRUCE_LEAVES, Blocks.BIRCH_LEAVES, Blocks.JUNGLE_LEAVES,
        Blocks.ACACIA_LEAVES, Blocks.DARK_OAK_LEAVES, Blocks.MANGROVE_LEAVES, Blocks.AZALEA_LEAVES,
        Blocks.FLOWERING_AZALEA_LEAVES, Blocks.BROWN_MUSHROOM_BLOCK, Blocks.RED_MUSHROOM_BLOCK,
        Blocks.HAY_BLOCK, Blocks.COAL_BLOCK, Blocks.BOOKSHELF, Blocks.TNT
    );

    private static final Set<Block> FIRE_RESISTANT_BLOCKS = Set.of(
        Blocks.STONE, Blocks.COBBLESTONE, Blocks.STONE_BRICKS, Blocks.BRICKS,
        Blocks.NETHERRACK, Blocks.NETHER_BRICKS, Blocks.OBSIDIAN, Blocks.BEDROCK,
        Blocks.IRON_BLOCK, Blocks.GOLD_BLOCK, Blocks.DIAMOND_BLOCK
    );

    @Override
    public String getCommandName() {
        return "place_fire";
    }

    @Override
    public CompletableFuture<CommandResult> execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        // Validate coordinates
        CommandResult validation = validateCoordinates(params);
        if (validation != null) {
            return CompletableFuture.completedFuture(validation);
        }

        BlockPos targetPos = getBlockPos(params);

        return executeOnMainThread(client, () -> {
            if (client.player == null || client.world == null) {
                return CommandResult.error("Player or world not available");
            }

            // Perform comprehensive analysis
            FirePlacementAnalysis analysis = analyzeFirePlacement(client.world, client.player, targetPos);

            // Check if placement is safe
            if (!analysis.isSafe) {
                return CommandResult.error("Fire placement unsafe: " + analysis.safetyReason);
            }

            // Check inventory
            InventoryCheckResult inventoryCheck = checkInventory(client.player);
            if (!inventoryCheck.available) {
                return CommandResult.error("Inventory check failed: " + inventoryCheck.reason);
            }

            // Select optimal tool slot
            client.player.getInventory().selectedSlot = inventoryCheck.slot;
            client.player.networkHandler.sendPacket(
                new net.minecraft.network.packet.c2s.play.UpdateSelectedSlotC2SPacket(inventoryCheck.slot)
            );

            // Execute fire placement
            BlockHitResult hitResult = new BlockHitResult(
                Vec3d.ofCenter(targetPos), Direction.UP, targetPos, false
            );
            client.interactionManager.interactBlock(client.player, Hand.MAIN_HAND, hitResult);
            client.player.swingHand(Hand.MAIN_HAND);

            // Prepare response data
            JsonObject data = new JsonObject();
            data.addProperty("ignited", true);
            data.addProperty("x", targetPos.getX());
            data.addProperty("y", targetPos.getY());
            data.addProperty("z", targetPos.getZ());
            data.addProperty("fuel_score", analysis.fuelScore);
            data.addProperty("risk_level", analysis.riskLevel);
            data.addProperty("flammable_blocks_nearby", analysis.flammableBlocksNearby);
            data.addProperty("tool_durability", inventoryCheck.durability);

            // Add optimization data if available
            if (analysis.optimalPosition != null) {
                JsonObject optimal = new JsonObject();
                optimal.addProperty("x", analysis.optimalPosition.getX());
                optimal.addProperty("y", analysis.optimalPosition.getY());
                optimal.addProperty("z", analysis.optimalPosition.getZ());
                optimal.addProperty("improvement", analysis.optimizationReason);
                data.add("optimal_position", optimal);
            }

            return CommandResult.success(data);
        });
    }

    /**
     * Analyzes terrain for fire placement suitability.
     */
    private FirePlacementAnalysis analyzeFirePlacement(World world, PlayerEntity player, BlockPos targetPos) {
        FirePlacementAnalysis analysis = new FirePlacementAnalysis();

        // Check if target block can have fire placed on it
        BlockState targetState = world.getBlockState(targetPos);
        if (!targetState.isAir() && !isValidFireSurface(targetState.getBlock())) {
            analysis.isSafe = false;
            analysis.safetyReason = "Invalid surface for fire placement";
            return analysis;
        }

        // Terrain analysis - scan for flammable materials
        List<BlockPos> flammablePositions = new ArrayList<>();
        for (int dx = -SCAN_RADIUS; dx <= SCAN_RADIUS; dx++) {
            for (int dy = -SCAN_RADIUS; dy <= SCAN_RADIUS; dy++) {
                for (int dz = -SCAN_RADIUS; dz <= SCAN_RADIUS; dz++) {
                    if (dx == 0 && dy == 0 && dz == 0) continue;

                    BlockPos checkPos = targetPos.add(dx, dy, dz);
                    BlockState state = world.getBlockState(checkPos);
                    Block block = state.getBlock();

                    if (FLAMMABLE_BLOCKS.contains(block)) {
                        flammablePositions.add(checkPos);
                    }
                }
            }
        }

        analysis.flammableBlocksNearby = flammablePositions.size();

        // Safety checks
        if (flammablePositions.size() > 10) {
            analysis.isSafe = false;
            analysis.safetyReason = "Too many flammable blocks nearby - high fire spread risk";
            return analysis;
        }

        // Check for player safety
        BlockPos playerPos = player.getBlockPos();
        double distanceToPlayer = Math.sqrt(targetPos.getSquaredDistance(playerPos));
        if (distanceToPlayer < 2.0) {
            analysis.isSafe = false;
            analysis.safetyReason = "Too close to player position";
            return analysis;
        }

        // Placement optimization - find position with best fuel availability
        analysis.fuelScore = calculateFuelScore(world, targetPos, flammablePositions);

        // Risk assessment
        analysis.riskLevel = calculateRiskLevel(flammablePositions.size(), distanceToPlayer);

        // Find optimal position if current one isn't ideal
        BlockPos optimalPos = findOptimalPosition(world, targetPos, flammablePositions);
        if (!optimalPos.equals(targetPos)) {
            analysis.optimalPosition = optimalPos;
            int optimalFuelScore = calculateFuelScore(world, optimalPos, flammablePositions);
            analysis.optimizationReason = String.format("Better fuel score: %d vs %d", optimalFuelScore, analysis.fuelScore);
        }

        analysis.isSafe = true;
        return analysis;
    }

    /**
     * Checks if a block is a valid surface for fire placement.
     */
    private boolean isValidFireSurface(Block block) {
        return block == Blocks.NETHERRACK || block == Blocks.SOUL_SOIL ||
               block == Blocks.SOUL_SAND || block == Blocks.MAGMA_BLOCK ||
               FIRE_RESISTANT_BLOCKS.contains(block);
    }

    /**
     * Calculates fuel score based on nearby flammable materials.
     */
    private int calculateFuelScore(World world, BlockPos pos, List<BlockPos> flammablePositions) {
        int score = 0;
        for (BlockPos flammablePos : flammablePositions) {
            double distance = Math.sqrt(pos.getSquaredDistance(flammablePos));
            if (distance <= 2.0) {
                score += 3; // Close flammable material
            } else if (distance <= 4.0) {
                score += 1; // Medium distance
            }
        }
        return score;
    }

    /**
     * Calculates risk level based on flammable proximity and player distance.
     */
    private String calculateRiskLevel(int flammableCount, double playerDistance) {
        if (flammableCount > 5 || playerDistance < 3.0) {
            return "HIGH";
        } else if (flammableCount > 2 || playerDistance < 5.0) {
            return "MEDIUM";
        } else {
            return "LOW";
        }
    }

    /**
     * Finds optimal fire placement position based on fuel availability.
     */
    private BlockPos findOptimalPosition(World world, BlockPos originalPos, List<BlockPos> flammablePositions) {
        BlockPos bestPos = originalPos;
        int bestScore = calculateFuelScore(world, originalPos, flammablePositions);

        // Check adjacent positions
        for (Direction direction : Direction.values()) {
            if (direction == Direction.UP || direction == Direction.DOWN) continue;

            BlockPos checkPos = originalPos.offset(direction);
            BlockState state = world.getBlockState(checkPos);
            if (state.isAir() || isValidFireSurface(state.getBlock())) {
                int score = calculateFuelScore(world, checkPos, flammablePositions);
                if (score > bestScore) {
                    bestScore = score;
                    bestPos = checkPos;
                }
            }
        }

        return bestPos;
    }

    /**
     * Checks inventory for flint & steel or fire charge availability and durability.
     */
    private InventoryCheckResult checkInventory(PlayerEntity player) {
        InventoryCheckResult result = new InventoryCheckResult();

        for (int i = 0; i < 9; i++) {
            ItemStack stack = player.getInventory().getStack(i);
            if (stack.getItem() == Items.FLINT_AND_STEEL) {
                if (stack.getDamage() < stack.getMaxDamage() - 1) { // Has at least 1 use left
                    result.available = true;
                    result.slot = i;
                    result.durability = (stack.getMaxDamage() - stack.getDamage()) / (double) stack.getMaxDamage();
                    result.toolType = "flint_and_steel";
                    return result;
                }
            } else if (stack.getItem() == Items.FIRE_CHARGE) {
                result.available = true;
                result.slot = i;
                result.durability = stack.getCount() / 64.0; // Relative to stack size
                result.toolType = "fire_charge";
                return result;
            }
        }

        result.available = false;
        result.reason = "No usable flint and steel or fire charge in hotbar";
        return result;
    }

    /**
     * Data class for fire placement analysis results.
     */
    private static class FirePlacementAnalysis {
        boolean isSafe = false;
        String safetyReason = "";
        int fuelScore = 0;
        String riskLevel = "UNKNOWN";
        int flammableBlocksNearby = 0;
        BlockPos optimalPosition = null;
        String optimizationReason = "";
    }

    /**
     * Data class for inventory check results.
     */
    private static class InventoryCheckResult {
        boolean available = false;
        String reason = "";
        int slot = -1;
        double durability = 0.0;
        String toolType = "";
    }
}
