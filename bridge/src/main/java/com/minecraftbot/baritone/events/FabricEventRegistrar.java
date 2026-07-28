package com.minecraftbot.baritone.events;

import com.minecraftbot.baritone.EventManager;
import com.minecraftbot.baritone.BaritoneAPIBridge;
import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.client.message.v1.ClientReceiveMessageEvents;
import net.fabricmc.fabric.api.event.player.AttackBlockCallback;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.fabricmc.fabric.api.event.player.AttackEntityCallback;
import net.fabricmc.fabric.api.event.player.UseEntityCallback;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.level.block.state.BlockState;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Registers Fabric API event listeners and forwards events to the EventManager.
 */
public class FabricEventRegistrar {

    private static final Logger LOGGER = LoggerFactory.getLogger("baritone-event-registrar");
    private final EventManager eventManager;

    public FabricEventRegistrar(EventManager eventManager) {
        this.eventManager = eventManager;
    }

    public void registerEvents() {
        // Chat message listener
        ClientReceiveMessageEvents.GAME.register((message, overlay) -> {
            if (!overlay) {
                JsonObject data = new JsonObject();
                data.addProperty("message", message.getString());
                eventManager.publishEvent(EventManager.EventType.CHAT, data);
            }
        });

        // Block break listener (when player starts breaking a block)
        AttackBlockCallback.EVENT.register((player, world, hand, pos, direction) -> {
            if (player == Minecraft.getInstance().player) {
                BlockState state = world.getBlockState(pos);
                JsonObject data = new JsonObject();
                data.addProperty("x", pos.getX());
                data.addProperty("y", pos.getY());
                data.addProperty("z", pos.getZ());
                data.addProperty("block_type", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
                data.addProperty("dimension", world.dimension().identifier().toString());
                eventManager.publishEvent(EventManager.EventType.BLOCK_BREAK, data, EventManager.Priority.NORMAL, "block_break");
            }
            return InteractionResult.PASS;
        });

        // Block place listener (when player uses/places a block)
        UseBlockCallback.EVENT.register((player, world, hand, hitResult) -> {
            if (player == Minecraft.getInstance().player) {
                BlockPos pos = hitResult.getBlockPos();
                BlockState state = world.getBlockState(pos);
                JsonObject data = new JsonObject();
                data.addProperty("x", pos.getX());
                data.addProperty("y", pos.getY());
                data.addProperty("z", pos.getZ());
                data.addProperty("block_type", BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString());
                data.addProperty("dimension", world.dimension().identifier().toString());
                eventManager.publishEvent(EventManager.EventType.BLOCK_PLACE, data, EventManager.Priority.NORMAL, "block_place");
            }
            return InteractionResult.PASS;
        });

        // Entity interaction listeners
        AttackEntityCallback.EVENT.register((player, world, hand, entity, hitResult) -> {
            if (player == Minecraft.getInstance().player) {
                JsonObject data = new JsonObject();
                data.addProperty("x", entity.getX());
                data.addProperty("y", entity.getY());
                data.addProperty("z", entity.getZ());
                data.addProperty("entity_type", BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString());
                data.addProperty("entity_id", entity.getId());
                data.addProperty("dimension", world.dimension().identifier().toString());
                data.addProperty("item_used", BuiltInRegistries.ITEM.getKey(player.getItemInHand(hand).getItem()).toString());
                data.addProperty("success", true);
                eventManager.publishEvent(EventManager.EventType.ENTITY_ATTACK, data, EventManager.Priority.NORMAL, "entity_attack");
            }
            return InteractionResult.PASS;
        });

        UseEntityCallback.EVENT.register((player, world, hand, entity, hitResult) -> {
            if (player == Minecraft.getInstance().player) {
                String type = BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString();
                EventManager.EventType eventType = null;

                if (type.equals("minecraft:wolf") || type.equals("minecraft:cat") || type.equals("minecraft:parrot")) {
                    eventType = EventManager.EventType.ENTITY_TAME;
                } else if (type.equals("minecraft:sheep")) {
                    eventType = EventManager.EventType.ENTITY_SHEAR;
                } else if (type.equals("minecraft:cow") || type.equals("minecraft:mooshroom")) {
                    eventType = EventManager.EventType.ENTITY_MILK;
                }

                if (eventType != null) {
                    JsonObject data = new JsonObject();
                    data.addProperty("x", entity.getX());
                    data.addProperty("y", entity.getY());
                    data.addProperty("z", entity.getZ());
                    data.addProperty("entity_type", type);
                    data.addProperty("entity_id", entity.getId());
                    data.addProperty("dimension", world.dimension().identifier().toString());
                    data.addProperty("item_used", BuiltInRegistries.ITEM.getKey(player.getItemInHand(hand).getItem()).toString());
                    data.addProperty("success", true);
                    eventManager.publishEvent(eventType, data, EventManager.Priority.NORMAL, "entity_" + eventType.name().toLowerCase().substring(7));
                }
            }
            return InteractionResult.PASS;
        });

        LOGGER.info("Fabric event listeners registered");
    }
}
