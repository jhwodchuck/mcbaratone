package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.utils.BlockOptionalMeta;
import baritone.api.utils.BlockOptionalMetaLookup;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import net.minecraft.client.Minecraft;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.level.block.Block;

/**
 * Command handler for mining operations.
 */
public class MineCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "mine";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        int count = params.has("count") ? params.get("count").getAsInt() :
                   (params.has("quantity") ? params.get("quantity").getAsInt() : 0);

        if (params.has("x") && params.has("y") && params.has("z")) {
            return handleMiningAtPos(params, baritone);
        } else if (params.has("block_type")) {
            return handleSingleBlock(params, baritone, count);
        } else if (params.has("blocks")) {
            return handleMultipleBlocks(params, baritone, count);
        } else {
            return CommandResult.error("Missing block_type, blocks, or x,y,z parameters");
        }
    }

    private CommandResult handleMiningAtPos(JsonObject params, IBaritone baritone) {
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();
        net.minecraft.core.BlockPos pos = new net.minecraft.core.BlockPos(x, y, z);
        
        executeOnMainThread(Minecraft.getInstance(), () -> {
            baritone.getBuilderProcess().clearArea(pos, pos);
        });

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("x", x);
        data.addProperty("y", y);
        data.addProperty("z", z);
        return CommandResult.success(data);
    }

    private CommandResult handleSingleBlock(JsonObject params, IBaritone baritone, int count) {
        String blockId = params.get("block_type").getAsString();
        Identifier id = Identifier.parse(blockId);

        if (!BuiltInRegistries.BLOCK.containsKey(id)) {
            return CommandResult.error("Unknown block: " + blockId);
        }

        Block block = BuiltInRegistries.BLOCK.getValue(id);
        executeOnMainThread(Minecraft.getInstance(), () ->
            baritone.getMineProcess().mine(count, block));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("block", blockId);
        data.addProperty("count", count);
        return CommandResult.success(data);
    }

    private CommandResult handleMultipleBlocks(JsonObject params, IBaritone baritone, int count) {
        JsonArray blocksArray = params.getAsJsonArray("blocks");
        if (blocksArray.isEmpty()) {
            return CommandResult.error("No block IDs provided");
        }

        List<BlockOptionalMeta> lookup = new ArrayList<>();
        for (JsonElement element : blocksArray) {
            if (!element.isJsonPrimitive()) continue;
            String blockId = element.getAsString();
            Identifier id = Identifier.parse(blockId);
            if (BuiltInRegistries.BLOCK.containsKey(id)) {
                lookup.add(new BlockOptionalMeta(BuiltInRegistries.BLOCK.getValue(id)));
            }
        }

        if (lookup.isEmpty()) {
            return CommandResult.error("No valid blocks found to mine");
        }

        BlockOptionalMeta[] blockArray = lookup.toArray(new BlockOptionalMeta[0]);
        executeOnMainThread(Minecraft.getInstance(), () ->
            baritone.getMineProcess().mine(count, blockArray));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("blocks_found", lookup.size());
        data.addProperty("count", count);
        return CommandResult.success(data);
    }
}