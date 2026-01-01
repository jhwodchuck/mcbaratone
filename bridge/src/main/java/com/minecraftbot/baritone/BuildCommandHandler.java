package com.minecraftbot.baritone;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import baritone.api.schematic.ISchematic;
import baritone.api.schematic.IStaticSchematic;
import baritone.api.schematic.format.ISchematicFormat;
import baritone.api.selection.ISelection;
import baritone.api.utils.BetterBlockPos;
import com.google.gson.JsonObject;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.math.Vec3i;

import java.io.File;
import java.io.FileInputStream;
import java.net.Socket;
import java.util.Optional;

/**
 * Command handler for build operations: build, selection.
 */
public class BuildCommandHandler extends AbstractCommandHandler {

    private final File schematicDir;

    public BuildCommandHandler() {
        this.schematicDir = new File(MinecraftClient.getInstance().runDirectory, "schematics");
    }

    @Override
    public String getCommandName() {
        return "build";
    }

    @Override
    protected CommandResult execute(JsonObject params, MinecraftClient client, IBaritone baritone, Socket clientSocket) {
        String action = params.has("action") ? params.get("action").getAsString() : "build";

        switch (action) {
            case "build":
                return handleBuild(params, baritone);
            case "select":
            case "selection":
                return handleSelection(params, baritone);
            default:
                return CommandResult.error("Unknown build action: " + action);
        }
    }

    private CommandResult handleBuild(JsonObject params, IBaritone baritone) {
        if (!params.has("schematic")) {
            return CommandResult.error("Missing schematic parameter");
        }

        if (!params.has("x") || !params.has("y") || !params.has("z")) {
            return CommandResult.error("Missing coordinates (x, y, z)");
        }

        String name = params.get("schematic").getAsString();
        int x = params.get("x").getAsInt();
        int y = params.get("y").getAsInt();
        int z = params.get("z").getAsInt();

        File file = new File(schematicDir, name);
        if (!file.exists()) {
            return CommandResult.error("Schematic not found: " + name);
        }

        try {
            // Load schematic using Baritone API
            Optional<ISchematicFormat> format = BaritoneAPI.getProvider().getSchematicSystem().getByFile(file);
            if (format.isPresent()) {
                ISchematic schematic = format.get().parse(new FileInputStream(file));
                executeOnMainThread(MinecraftClient.getInstance(), () ->
                    baritone.getBuilderProcess().build(name, schematic, new Vec3i(x, y, z)));

                JsonObject data = new JsonObject();
                data.addProperty("started", true);
                data.addProperty("schematic", name);
                data.addProperty("x", x);
                data.addProperty("y", y);
                data.addProperty("z", z);
                return CommandResult.success(data);
            } else {
                return CommandResult.error("Unsupported schematic format for file: " + name);
            }
        } catch (Exception e) {
            logger.error("Error loading schematic", e);
            return CommandResult.error("Failed to load schematic: " + e.getMessage());
        }
    }

    private CommandResult handleSelection(JsonObject params, IBaritone baritone) {
        if (!params.has("action")) {
            return CommandResult.error("Missing selection action");
        }

        String action = params.get("action").getAsString();

        executeOnMainThread(MinecraftClient.getInstance(), () -> {
            if ("set".equals(action)) {
                handleSelectionSet(params, baritone);
            } else if ("clear".equals(action)) {
                baritone.getSelectionManager().removeAllSelections();
            } else {
                handleSelectionModify(params, baritone, action);
            }
        });

        JsonObject data = new JsonObject();
        if ("set".equals(action)) {
            data.addProperty("set", true);
        } else {
            data.addProperty("updated", true);
        }
        data.addProperty("action", action);
        return CommandResult.success(data);
    }

    private void handleSelectionSet(JsonObject params, IBaritone baritone) {
        int x1 = params.get("x1").getAsInt();
        int y1 = params.get("y1").getAsInt();
        int z1 = params.get("z1").getAsInt();
        int x2 = params.get("x2").getAsInt();
        int y2 = params.get("y2").getAsInt();
        int z2 = params.get("z2").getAsInt();

        baritone.getSelectionManager().removeAllSelections();
        baritone.getSelectionManager().addSelection(
            new BetterBlockPos(x1, y1, z1),
            new BetterBlockPos(x2, y2, z2)
        );
    }

    private void handleSelectionModify(JsonObject params, IBaritone baritone, String action) {
        ISelection[] sels = baritone.getSelectionManager().getSelections();
        if (sels.length == 0) {
            return;
        }

        ISelection sel = sels[0]; // operate on first
        var direction = net.minecraft.util.math.Direction.valueOf(params.get("direction").getAsString().toUpperCase());
        int blocks = params.get("blocks").getAsInt();

        if ("expand".equals(action)) {
            baritone.getSelectionManager().expand(sel, direction, blocks);
        } else if ("contract".equals(action)) {
            baritone.getSelectionManager().contract(sel, direction, blocks);
        } else if ("shift".equals(action)) {
            baritone.getSelectionManager().shift(sel, direction, blocks);
        }
    }
}