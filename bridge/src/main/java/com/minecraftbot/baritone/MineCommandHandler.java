package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.level.block.Block;

/**
 * Command handler for mining operations.
 */
public class MineCommandHandler extends AsyncCommandHandler {

    @Override
    public String getCommandName() {
        return "mine";
    }

    @Override
    public CompletableFuture<CommandResult> execute(
            JsonObject params,
            Minecraft client,
            IBaritone baritone,
            Socket clientSocket) {
        int count = params.has("count") ? params.get("count").getAsInt() :
                   (params.has("quantity") ? params.get("quantity").getAsInt() : 0);

        if (params.has("x") && params.has("y") && params.has("z")) {
            return handleMiningAtPos(params, client, baritone);
        } else if (params.has("block_type")) {
            return handleSingleBlock(params, client, baritone, count);
        } else if (params.has("blocks")) {
            return handleMultipleBlocks(params, client, baritone, count);
        } else {
            return CompletableFuture.completedFuture(CommandResult.error(
                "Missing block_type, blocks, or x,y,z parameters"));
        }
    }

    private CompletableFuture<CommandResult> handleMiningAtPos(
            JsonObject params, Minecraft client, IBaritone baritone) {
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        net.minecraft.core.BlockPos pos = new net.minecraft.core.BlockPos(x, y, z);
        
        return executeOnMainThread(client, () -> {
            baritone.getBuilderProcess().clearArea(pos, pos);
            JsonObject data = appliedData(client);
            data.addProperty("x", x);
            data.addProperty("y", y);
            data.addProperty("z", z);
            return CommandResult.success(data);
        });
    }

    private CompletableFuture<CommandResult> handleSingleBlock(
            JsonObject params, Minecraft client, IBaritone baritone, int count) {
        String blockId = params.get("block_type").getAsString();
        Identifier id = Identifier.parse(blockId);

        if (!BuiltInRegistries.BLOCK.containsKey(id)) {
            return CompletableFuture.completedFuture(
                CommandResult.error("Unknown block: " + blockId));
        }

        Block block = BuiltInRegistries.BLOCK.getValue(id);
        return executeOnMainThread(client, () -> {
            baritone.getMineProcess().mine(count, block);
            JsonObject data = appliedData(client);
            data.addProperty("block", blockId);
            data.addProperty("count", count);
            return CommandResult.success(data);
        });
    }

    private CompletableFuture<CommandResult> handleMultipleBlocks(
            JsonObject params, Minecraft client, IBaritone baritone, int count) {
        JsonArray blocksArray = params.getAsJsonArray("blocks");
        if (blocksArray.isEmpty()) {
            return CompletableFuture.completedFuture(
                CommandResult.error("No block IDs provided"));
        }

        List<Block> lookup = new ArrayList<>();
        for (JsonElement element : blocksArray) {
            if (!element.isJsonPrimitive()) continue;
            String blockId = element.getAsString();
            Identifier id = Identifier.parse(blockId);
            if (BuiltInRegistries.BLOCK.containsKey(id)) {
                lookup.add(BuiltInRegistries.BLOCK.getValue(id));
            }
        }

        if (lookup.isEmpty()) {
            return CompletableFuture.completedFuture(
                CommandResult.error("No valid blocks found to mine"));
        }

        Block[] blockArray = lookup.toArray(new Block[0]);
        return executeOnMainThread(client, () -> {
            baritone.getMineProcess().mine(count, blockArray);
            JsonObject data = appliedData(client);
            data.addProperty("blocks_found", lookup.size());
            data.addProperty("count", count);
            return CommandResult.success(data);
        });
    }

    private JsonObject appliedData(Minecraft client) {
        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("accepted", true);
        data.addProperty("applied", true);
        if (client.level != null) {
            data.addProperty("applied_tick", client.level.getGameTime());
        }
        return data;
    }
}
