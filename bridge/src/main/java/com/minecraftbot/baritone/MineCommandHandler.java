package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import baritone.api.utils.BlockOptionalMeta;
import baritone.api.utils.BlockOptionalMetaLookup;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;
import net.minecraft.block.Block;

import java.net.Socket;
import java.util.ArrayList;
import java.util.List;

/**
 * Command handler for mining operations.
 */
public class MineCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "mine";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        int count = params.has("count") ? params.get("count").getAsInt() :
                   (params.has("quantity") ? params.get("quantity").getAsInt() : 0);

        if (params.has("block_type")) {
            return handleSingleBlock(params, baritone, count);
        } else if (params.has("blocks")) {
            return handleMultipleBlocks(params, baritone, count);
        } else {
            return CommandResult.error("Missing block_type or blocks parameter");
        }
    }

    private CommandResult handleSingleBlock(JsonObject params, IBaritone baritone, int count) {
        String blockId = params.get("block_type").getAsString();
        Identifier id = Identifier.of(blockId);

        if (!Registries.BLOCK.containsId(id)) {
            return CommandResult.error("Unknown block: " + blockId);
        }

        Block block = Registries.BLOCK.get(id);
        executeOnMainThread(MinecraftClient.getInstance(), () ->
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
            Identifier id = Identifier.of(blockId);
            if (Registries.BLOCK.containsId(id)) {
                lookup.add(new BlockOptionalMeta(Registries.BLOCK.get(id)));
            }
        }

        if (lookup.isEmpty()) {
            return CommandResult.error("No valid blocks found to mine");
        }

        BlockOptionalMeta[] blockArray = lookup.toArray(new BlockOptionalMeta[0]);
        executeOnMainThread(MinecraftClient.getInstance(), () ->
            baritone.getMineProcess().mine(count, blockArray));

        JsonObject data = new JsonObject();
        data.addProperty("started", true);
        data.addProperty("blocks_found", lookup.size());
        data.addProperty("count", count);
        return CommandResult.success(data);
    }
}