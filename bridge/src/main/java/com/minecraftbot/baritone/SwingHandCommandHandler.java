package com.minecraftbot.baritone;

import baritone.api.IBaritone;
import com.google.gson.JsonObject;
import java.net.Socket;
import net.minecraft.client.Minecraft;
import net.minecraft.world.InteractionHand;

/**
 * Command handler for swing_hand: triggers the arm-swing animation without
 * an accompanying block/entity interaction.
 *
 * Callers that already act on the world through a dedicated command (e.g.
 * attack_block, interact_block) get the swing for free -- those handlers
 * call player.swing() themselves. This exists for the remaining case: a
 * caller that wants the animation on its own, such as a background safety
 * system swinging while it clears an entanglement it is handling via
 * attack_block on a tick where the swing needs to be independently timed.
 */
public class SwingHandCommandHandler extends AbstractCommandHandler {

    @Override
    public String getCommandName() {
        return "swing_hand";
    }

    @Override
    protected CommandResult execute(JsonObject params, Minecraft client, IBaritone baritone, Socket clientSocket) {
        String handStr = params != null && params.has("hand")
            ? params.get("hand").getAsString().toUpperCase()
            : "MAIN_HAND";
        InteractionHand hand = "OFF_HAND".equals(handStr) ? InteractionHand.OFF_HAND : InteractionHand.MAIN_HAND;

        client.execute(() -> {
            if (client.player != null) {
                client.player.swing(hand);
            }
        });

        return CommandResult.success();
    }
}
